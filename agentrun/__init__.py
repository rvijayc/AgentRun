"""AgentRun - Run Python code in an isolated Docker container"""

import ast
import warnings
import os
import sys
import tarfile
from io import BytesIO
from threading import Thread
from typing import Any, Union, List, Optional, Tuple
from uuid import uuid4
import abc
from loguru import logger
import json

import docker
from docker.models.containers import Container
from RestrictedPython import compile_restricted

# list all packages installed in the current version of python.
PKG_LIST_PROGRAM=r"""'import pkgutil\nfor p in pkgutil.iter_modules():\n print(p.name)'"""
PKG_LIST_CMD=r"""python3 -c "exec({})" """.format(PKG_LIST_PROGRAM)

class InstallPolicy(abc.ABC):
    """
    An abstract base class for specifying Python package installation commands.
    At this time, this assumes that the command outputs are compatible with
    pip.
    """

    def init_cmds(self) -> List[str]:
        """ 
        [Optional] Specify a list of commands to run during initialization 

        For example, if you are using UV, you might want to run "pip install uv".
        """
        return []

    @abc.abstractmethod
    def install_cmd(self, package:str) -> str:
        """ Installs the specified Package """

    @abc.abstractmethod
    def uninstall_cmd(self, package:str) -> str:
        """ Uninstalls the specified Package """

    def list_cmd(self) -> str:
        """ Lists all available packages. The default version uses Python's pkgutil itself. """
        return PKG_LIST_CMD

    def parse_packages(self, output) -> List[str]:
        """
        Parse the output of "list_cmd" and return the packages deteected.
        """
        return [ 
            line
            for line in output.splitlines() 
            if not (line.startswith('_') or line.startswith('script_'))
        ]

class UVInstallPolicy(InstallPolicy):

    def install_cmd(self, package:str):
        return f"uv pip install {package}"

    def uninstall_cmd(self, package:str):
        return f"uv pip uninstall {package}"

    def list_cmd(self) -> str:
        return "uv pip list --format=json -q"

    def parse_packages(self, output) -> List[str]:
        pkg_list = []
        for pkg in json.loads(output):
            pkg_list.append(pkg['name'])
        return pkg_list

class PIPInstallPolicy(InstallPolicy):
    
    def install_cmd(self, package:str):
        return f"pip install {package}"

    def uninstall_cmd(self, package: str):
        return f"pip uninstall -y {package}"

    def list_cmd(self) -> str:
        return "pip list"

    def parse_packages(self, output):
        return [ line.split()[0].lower() for line in output.splitlines() if " " in line ]

class AgentRun:
    """Class to execute Python code in an isolated Docker container.

    Example usage:
        from agentrun import AgentRun\n
        runner = AgentRun(container_name="my_container") # container should be running\n
        result = runner.execute_code_in_container("print('Hello, world!')")\n
        print(result)

    Args:
        container_name: Name of the Docker container to use
        dependencies_whitelist: List of whitelisted dependencies to install. By default, all dependencies are allowed.
        cached_dependencies: List of dependencies to cache in the container
        cpu_quota: CPU quota in microseconds (default: 50,000)
        default_timeout: Default timeout in seconds (default: 20)
        memory_limit: Memory limit for the container (default: 100m)
        memswap_limit: Memory + swap limit for the container (default: 512m)
        client: Docker client object (default: docker.from_env())
    """

    def __init__(
        self,
        container_name,
        dependencies_whitelist=["*"],
        cached_dependencies=[],
        cpu_quota=50000,
        default_timeout=20,
        memory_limit="100m",
        memswap_limit="512m",
        client=None,
        install_policy=UVInstallPolicy(),
        log_level='WARNING'
    ) -> None:

        self.cpu_quota = cpu_quota
        self.default_timeout = default_timeout
        self.memory_limit = memory_limit
        self.memswap_limit = memswap_limit
        self.container_name = container_name
        self.dependencies_whitelist = dependencies_whitelist
        # this is to allow a mock client to be passed in for testing if docker is not available (not implemented yet)
        self.client = client or docker.from_env()
        self.install_policy = install_policy

        # initialize logging.
        self.logger = logger.bind(name='AgentRun')
        # remove the default logger.
        self.logger.remove()
        # add a async-friendly logger.
        self.logger.add(
            sys.stderr,
            format="{time} {level} {message}",
            level=log_level,
            enqueue=True  # Enables async behavior
        )

        try:
            self.client = client or docker.from_env()
            self.client.ping()
        except docker.errors.DockerException as e:
            raise RuntimeError(
                f"Failed to connect to Docker daemon. Please make sure Docker is running. {e}"
            )

        try:
            container = self.client.containers.get(self.container_name)
            if container.status != "running":
                raise ValueError(f"Container {self.container_name} is not running.")
        except docker.errors.NotFound:
            raise ValueError(f"Container {self.container_name} not found.")

        # run any initialization commands specified.
        for command in self.install_policy.init_cmds():
            container = self.client.containers.get(self.container_name)
            exit_code, output = self.execute_command_in_container(
                container, command, timeout=120
            )
            if exit_code != 0:
                self.logger.error(f"Failed to run {command}! See output below:")
                for line in output.splitlines():
                    self.logger.error(line)
                raise ValueError(f"Failed to run: {command}.")

        # any package that was already installed inside the container is considered "cached"
        exec_log = container.exec_run(cmd=self.install_policy.list_cmd(), workdir="/code")
        exit_code, output = exec_log.exit_code, exec_log.output.decode("utf-8")
        if exit_code != 0:
            raise RuntimeError('{} failed with output: {}'.format(
                self.install_policy.list_cmd(), output))
        self.cached_dependencies = set(self.install_policy.parse_packages(output))
        self.logger.debug(f'Found Packages: {", ".join(self.cached_dependencies)}')

        # validate all cached dependencies before installing them.
        if (
            not self.is_everything_whitelisted()
            and not self.validate_cached_dependencies(cached_dependencies)
        ):
            raise ValueError("Some cached dependencies are not in the whitelist.")

        # if additional packages are specified as cached dependencies, install
        # them on start-up and add them to the original list of cached
        # packages.
        if cached_dependencies:
            self.install_dependencies(container, cached_dependencies)
            self.cached_dependencies.update(cached_dependencies)

    class CommandTimeout(Exception):
        """Exception raised when a command execution times out."""

        pass

    def is_everything_whitelisted(self) -> bool:
        """
        Check if everything is whitelisted.

        Returns:
            bool: True if everything is whitelisted, False otherwise.
        """
        return "*" in self.dependencies_whitelist

    def validate_cached_dependencies(self, cached_dependencies) -> bool:
        """
        Validates the cached dependencies against the whitelist.

        Returns:
            bool: True if all cached dependencies are whitelisted, False otherwise.
        """
        if self.is_everything_whitelisted():
            return True
        return all(
            dep in self.dependencies_whitelist for dep in cached_dependencies
        )

    def install_cached_dependencies(self) -> None:
        """
        Attempts to install cached dependencies into the specified Docker container.
        Raises:
            ValueError: If the dependencies could not be successfully installed.
        """
        container = self.client.containers.get(self.container_name)
        output, _ = self.install_dependencies(container, self.cached_dependencies)
        if output != "Dependencies installed successfully.":
            raise ValueError(output)

    def execute_command_in_container(
        self, container: Container, cmd: str, timeout: int
    ) -> tuple[Any | None, Any | str]:
        """Execute a command in a Docker container with a timeout.

        This function runs the command in a separate thread and waits for the specified timeout.

        Args:
            container: Docker container object
            command: Command to execute
            timeout: Timeout in seconds
        Returns:
            Tuple of exit code and output

        """
        exit_code, output = None, None
        self.logger.debug(f'[{container.name}] Running {cmd} ...')

        def target():
            nonlocal exit_code, output
            exec_log = container.exec_run(cmd=cmd, workdir="/code")
            exit_code, output = exec_log.exit_code, exec_log.output

        thread = Thread(target=target)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            thread.join(1)
            raise self.CommandTimeout("Command timed out")
        output = output if output is not None else b""
        return exit_code, output.decode("utf-8")

    def safety_check(self, 
                     python_code: str,
                     ignore_unsafe_functions: Optional[List[str]]=None
    ) -> dict[str, object]:
        """Check if Python code is safe to execute.
        This function uses common patterns and RestrictedPython to check for unsafe patterns in the code.

        Args:
            python_code: Python code to check
            ignore_unsafe_functions: A list of functions that we wish to ignore
                for unsafe patterns. For example, the "compile" function is used in
                SQLAlchemy and we'd like to allow it.
        Returns:
            Dictionary with "safe" (bool) and "message" (str) keys
        """
        result = {"safe": True, "message": "The code is safe to execute."}

        # Crude check for problematic code (os, sys, subprocess, exec, eval, etc.)
        unsafe_modules = {"os", "sys", "subprocess", "builtins"}
        unsafe_functions = {
            "exec",
            "eval",
            "compile",
            "open",
            "input",
            "__import__",
            "getattr",
            "setattr",
            "delattr",
            "hasattr",
        }
        dangerous_builtins = {
            "globals",
            "locals",
            "vars",
            "dir",
            "eval",
            "exec",
            "compile",
        }
        
        # customize ignoring unsafe functions.
        # - some functions like "compile" get used by tools like sqlalchemy, so
        # some exceptions need to be made in such cases.
        # - some other thing.
        if ignore_unsafe_functions:
            for ignore in ignore_unsafe_functions:
                if ignore in unsafe_functions:
                    unsafe_functions.remove(ignore)

        # this a crude check first - no need to compile the code if it's obviously unsafe. Performance boost.
        try:
            tree = ast.parse(python_code)
        except SyntaxError as e:
            return {"safe": False, "message": f"Syntax error: {str(e)}"}

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in dangerous_builtins
            ):
                return {
                    "safe": False,
                    "message": f"Use of dangerous built-in function: {node.func.id}",
                }
            # Check for unsafe imports
            if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                module_name = node.module if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    if module_name and module_name.split(".")[0] in unsafe_modules:
                        return {
                            "safe": False,
                            "message": f"Unsafe module import: {module_name}",
                        }
                    if alias.name.split(".")[0] in unsafe_modules:
                        return {
                            "safe": False,
                            "message": f"Unsafe module import: {alias.name}",
                        }
            # Check for unsafe function calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in unsafe_functions:
                    return {
                        "safe": False,
                        "message": f"Unsafe function call: {node.func.id}",
                    }
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in unsafe_functions
                ):
                    return {
                        "safe": False,
                        "message": f"Unsafe function call: {node.func.attr}",
                    }

        try:
            # Compile the code using RestrictedPython with a filename indicating its dynamic nature
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                compiled_code = compile_restricted(
                    python_code, filename="<dynamic>", mode="exec"
                )
            # Note: Execution step is omitted to only check the code without running it
            # This is not perfect, but should catch most unsafe patterns
        except Exception as e:
            return {
                "safe": False,
                "message": f"RestrictedPython detected an unsafe pattern: {str(e)}",
            }

        return result

    def parse_dependencies(self, python_code: str) -> list[str]:
        """Parse Python code to find import statements and filter out standard library modules.
        This function returns a list of unique dependencies found in the code.

        Args:
            python_code: Python code to parse
        Returns:
            List of unique dependencies
        """
        tree = ast.parse(python_code)
        dependencies = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Get the base module name. E.g. for "import foo.bar", it's "foo"
                    module_name = alias.name.split(".")[0]
                    if (
                        module_name not in sys.stdlib_module_names
                        and module_name not in sys.builtin_module_names
                    ):
                        dependencies.append(module_name)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module.split(".")[0] if node.module else ""
                if (
                    module_name
                    and module_name not in sys.stdlib_module_names
                    and module_name not in sys.builtin_module_names
                ):
                    dependencies.append(module_name)
        return list(set(dependencies))  # Return unique dependencies

    def install_dependencies(self, container: Container, dependencies: list) -> Tuple[str, List[str]]:
        """Install dependencies in the container.
        Args:
            container: Docker container object
            dependencies: List of dependencies to install
        Returns:
            Success message or error message

        """
        everything_whitelisted = self.is_everything_whitelisted()

        # Perform a pre-check to ensure all dependencies are in the whitelist (or everything is whitelisted)
        if not everything_whitelisted:
            for dep in dependencies:
                if dep not in self.dependencies_whitelist:
                    return f"Dependency: {dep} is not in the whitelist.", []

        installed_deps = []
        for dep in dependencies:
            if dep.lower() in self.cached_dependencies:
                self.logger.debug(f'Package {dep} is already cached.')
                continue
            self.logger.info(f'Installing {dep} ...')
            command = self.install_policy.install_cmd(dep)
            exit_code, output = self.execute_command_in_container(
                container, command, timeout=120
            )
            if exit_code != 0:
                self.logger.error(f'{dep} installation failed! Printing stdout ...')
                for line in output.splitlines():
                    self.logger.error(line)
                return f"Failed to install dependency {dep}", installed_deps
            installed_deps.append(dep)

        return "Dependencies installed successfully.", installed_deps

    def uninstall_dependencies(self, container: Container, dependencies: list) -> str:
        """Uninstall dependencies in the container.
        Args:
            container: Docker container object
            dependencies: List of dependencies to uninstall
        Returns:
            Success message or error message
        """
        for dep in dependencies:
            # do not uninstall dependencies that are cached_dependencies
            if dep in self.cached_dependencies:
                continue
            self.logger.info(f'Uninstalling {dep} ...')
            command = self.install_policy.uninstall_cmd(dep)
            exit_code, output = self.execute_command_in_container(
                container, command, timeout=120
            )
            if exit_code != 0:
                self.logger.error(f'{dep} ininstall failed! Printing stdout ...')
                for line in output.splitlines():
                    self.logger.error(line)

        return "Dependencies uninstalled successfully."

    def copy_code_to_container(
        self, container: Container, python_code: str
    ) -> dict[str, Union[bool, str]]:
        """Copy Python code to the container.
        Args:
            container: Docker container object
            python_code: Python code to copy
        Returns:
            Success message or error message
        """
        result = {"success": False, "message": ""}
        script_name = f"script_{uuid4().hex}.py"
        temp_script_path = os.path.join("/tmp", script_name)

        with open(temp_script_path, "w") as file:
            file.write(python_code)

        tar_stream = BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(temp_script_path, arcname=script_name)
        tar_stream.seek(0)

        exec_result = container.put_archive(path="/code/", data=tar_stream)
        if exec_result:
            return {"success": True, "message": script_name}

        return {"success": False, "message": "Failed to copy script to container."}

    def clean_up(
        self, container: Container, script_name: str, dependencies: list
    ) -> None:
        """Clean up the container after execution.
        Args:
            container: Docker container object
            script_name: Name of the script to remove
        """
        if script_name:
            os.remove(os.path.join("/tmp", script_name))
            container.exec_run(cmd=f"rm /code/{script_name}", workdir="/code")
            dep_uninstall_result = self.uninstall_dependencies(container, dependencies)
        return None

    def execute_code_in_container(self, 
                                  python_code: str, 
                                  ignore_dependencies: Optional[List[str]]=None,
                                  ignore_unsafe_functions: Optional[List[str]]=None
    ) -> str:
        """Executes Python code in an isolated Docker container.
        This is the main function to execute Python code in a Docker container. It performs the following steps:
        1. Check if the code is safe to execute
        2. Update the container with the memory limits
        3. Copy the code to the container
        4. Install dependencies in the container
        5. Execute the code in the container
        5. Uninstall dependencies in the container & clean up

        Args:
            python_code: Python code to execute
        Returns:
            Output of the code execution or an error message
        """
        container = None
        installed_deps = []
        script_name: Optional[str] = None
        try:
            output = ""
            client = self.client
            timeout_seconds = self.default_timeout

            # check  if the code is safe to execute
            safety_result = self.safety_check(python_code, ignore_unsafe_functions)
            safety_message = safety_result["message"]
            safe = safety_result["safe"]
            if not safe:
                return safety_message

            container = client.containers.get(self.container_name)

            # update the container with the new limits
            container.update(
                cpu_quota=self.cpu_quota,
                mem_limit=self.memory_limit,
                memswap_limit=self.memswap_limit,
            )
            # Copy the code to the container
            exec_result = self.copy_code_to_container(container, python_code)
            successful_copy = exec_result["success"]
            message = exec_result["message"]
            if not successful_copy:
                return message

            script_name = message

            # Install dependencies in the container
            dependencies = self.parse_dependencies(python_code)
            if ignore_dependencies:
                # remove any dependencies specified in ignore dependencies.
                for ignore in ignore_dependencies:
                    if ignore in dependencies:
                        dependencies.remove(ignore)
            dep_install_result, installed_deps = self.install_dependencies(container, dependencies)
            if dep_install_result != "Dependencies installed successfully.":
                return dep_install_result

            try:
                _, output = self.execute_command_in_container(
                    container, f"python /code/{script_name}", timeout_seconds
                )
            except self.CommandTimeout:
                return "Execution timed out."

        except Exception as e:
            return str(e)

        finally:
            if container and script_name and len(installed_deps) > 0:
                self.clean_up(container, script_name, installed_deps)

        return output
