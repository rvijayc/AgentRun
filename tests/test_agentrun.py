import os

import docker
import docker.errors
import pytest
import tempfile
from uuid import uuid4

from agentrun_plus import AgentRun, UVInstallPolicy, AgentRunSession

LOG_LEVEL="WARNING"
class TestUVInstallPolicy(UVInstallPolicy):
    
    def init_cmds(self):
        return [
                "pip install uv",
        ]

    def install_cmd(self, package):
        return f"uv pip install {package} --system"

def check_session_clean(runner, name):

    # make sure the work folder has been cleaned up.
    workdir = os.path.join(runner.homedir, name)
    exit_code, output = runner.execute_command_in_container(
            f'test -d {workdir}',
            runner.homedir
    )
    assert exit_code == 1
    assert output == ''

@pytest.fixture(scope="session")
def docker_container():
    client = docker.from_env()

    # Create a volume to avoid creating files as root.
    volume = client.volumes.create('test-volume')

    # Run a container with the Python image
    container = client.containers.run(
        "python:3.12.2-slim-bullseye",
        name="test-container",
        detach=True,
        volumes={'test-volume': {"bind": "/home/pythonuser", "mode": "rw"}},
        environment={
            "UV_CONCURRENT_INSTALLS": "1",
        },
        command=["tail", "-f", "/dev/null"],  # Keep the container running
        pids_limit=10,
        security_opt=["no-new-privileges:true"],
    )

    yield container  # Provide the container to the test

    # Cleanup: Stop and remove the container
    container.stop()
    container.remove()

    # Remove the volume.
    volume.remove(force=True)

@pytest.mark.parametrize(
    "code, expected",
    [
        (
            "print('Hello, World!')",
            {"safe": True, "message": "The code is safe to execute."},
        ),
        (
            "print('Hello, World!'",
            {
                "safe": False,
                "message": "Syntax error: '(' was never closed (<unknown>, line 1)",
            },
        ),
        (
            "import os.path\nprint(os.path.join('dir', 'file.txt'))",
            {"safe": False, "message": "Unsafe module import: os.path"},
        ),
        (
            "from os import path\nprint(path.join('dir', 'file.txt'))",
            {"safe": False, "message": "Unsafe module import: os"},
        ),
        (
            "class MyClass:\n    def __init__(self):\n        self.eval = eval\n\nobj = MyClass()\nobj.eval('print(\"Hello, World!\")')",
            {"safe": False, "message": "Unsafe function call: eval"},
        ),
        (
            "def my_function():\n    pass\n\nmy_function.__globals__['__builtins__']['eval']('print(\"Hello, World!\")')",
            {
                "safe": False,
                "message": 'RestrictedPython detected an unsafe pattern: (\'Line 4: "__globals__" is an invalid attribute name because it starts with "_".\',)',
            },
        ),
        (
            "import os\nos.system('rm -rf /')",
            {"safe": False, "message": "Unsafe module import: os"},
        ),
        (
            "mod_name = 'os'\n__import__(mod_name).system('ls')",
            {"safe": False, "message": "Unsafe function call: __import__"},
        ),
        (
            "exec('import os\\nos.system(\\'ls\\')')",
            {"safe": False, "message": "Use of dangerous built-in function: exec"},
        ),
        (
            "eval('os.system(\\'ls\\')', {'os': __import__('os')})",
            {"safe": False, "message": "Use of dangerous built-in function: eval"},
        ),
        (
            "globals()[chr(111)+chr(115)].system('rm -rf / --no-preserve-root')",
            {"safe": False, "message": "Use of dangerous built-in function: globals"},
        ),
        (
            "import os\nprint('This is safe')\nos.system('ls')",
            {"safe": False, "message": "Unsafe module import: os"},
        ),
        # fails restritive python. It is (safe?) for our machine, but not for other people's machine.
        (
            "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\ns.connect(('example.com', 80))",
            {"safe": True, "message": "The code is safe to execute."},
        ),
        (
            "with open('secret_file.txt', 'r') as file:\n    print(file.read())",
            {"safe": False, "message": "Unsafe function call: open"},
        ),
        (
            "import subprocess\nsubprocess.Popen(['ping', '-c', '4', 'example.com'])",
            {"safe": False, "message": "Unsafe module import: subprocess"},
        ),
    ],
)
def test_safety_check(code, expected, docker_container):

    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL
    )
    result = runner._safety_check(code)
    assert result["safe"] == expected["safe"]
    assert result["message"] == expected["message"]


@pytest.mark.parametrize(
    "code, expected",
    [
        (
            "print('Hello, World!')",
            "Hello, World!\n",
        ),
        (
            "import time\ntime.sleep(3)",
            "Error: Execution timed out.",
        ),
    ],
)
def test_execute_code_with_timeout(code, expected, docker_container):

    runner = AgentRun(
        default_timeout=1,
        container_name="test-container",
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user='root'
    )
    name = uuid4().hex
    session = runner.create_session(name)
    output = session.execute_code(python_code=code)
    runner.close_session(session)
    assert output == expected

    # check if the session is properly cleaned-up
    check_session_clean(runner, name)

@pytest.mark.parametrize(
    "code, expected",
    [
        ("import os", []),
        ("import requests", ["requests"]),
        ("from collections import namedtuple", []),
        ("import sys\nimport numpy as np", ["numpy"]),
        ("import unknownpackage", ["unknownpackage"]),
        ("from scipy.optimize import minimize", ["scipy"]),
    ],
)
def test_parse_dependencies(code, expected, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL
    )
    result = runner._parse_dependencies(code)

    assert sorted(result) == sorted(expected)


@pytest.mark.parametrize(
    "code, expected, whitelist, cached",
    [
        # dependencies: arrow, open whitelist
        (
            "import arrow\nfixed_date = arrow.get('2023-04-15T12:00:00')\nprint(fixed_date.format('YYYY-MM-DD HH:mm:ss'))",
            "2023-04-15 12:00:00\n",
            ["*"],
            ["requests"],
        ),
        # dependencies: numpy, but not in the whitelist
        (
            "import numpy as np\nprint(np.array([1, 2, 3]))",
            "Dependency: numpy is not in the whitelist.",
            ["pandas"],
            [],
        ),
        # python built-in
        ("import math\nprint(math.sqrt(16))", "4.0\n", ["requests"], []),
        # dependencies: requests, in the whitelist
        (
            "import numpy as np\nprint(np.array([1, 2, 3]))",
            "[1 2 3]\n",
            ["numpy"],
            ["numpy"],
        ),
        # a dependency that doesn't exist
        (
            "import unknownpackage",
            "Failed to install dependency unknownpackage",
            ["*"],
            [],
        ),
    ],
)
def test_execute_code_with_dependencies(
    code, expected, whitelist, cached, docker_container
):
    runner = AgentRun(
        container_name=docker_container.name,
        dependencies_whitelist=whitelist,
        cached_dependencies=cached,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user='root'
    )

    # create session.
    name = uuid4().hex
    session = runner.create_session(name)
    output = session.execute_code(code)
    runner.close_session(session)
    assert output == expected

    # check if the session is properly cleaned-up
    check_session_clean(runner, name)

@pytest.mark.parametrize(
    "code, expected",
    [
        (
            "print('Hello, World!')",
            "Hello, World!\n",
        ),
        ("import os\nos.system('rm -rf /')", "Unsafe module import: os"),
    ],
)
def test_execute_code_in_container(code, expected, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user='root'
    )
    name = uuid4().hex
    session = runner.create_session(name)
    output = session.execute_code(code)
    runner.close_session(session)
    assert output == expected

    # check if the session is properly cleaned-up
    check_session_clean(runner, name)

def test_file_copy(docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user='root'
    )
    with tempfile.TemporaryDirectory() as tmpdir:

        # setup paths ...
        file_in_path = os.path.join(tmpdir, 'in', 'file.txt')
        os.makedirs(os.path.dirname(file_in_path), exist_ok=True)
        file_out_path = os.path.join(tmpdir, 'out', 'file.txt')
        os.makedirs(os.path.dirname(file_out_path), exist_ok=True)

        with open(file_in_path, 'wt') as f:
            f.write('Hello, World!')
        
        # copy the file to container in the user's base folder.
        runner.copy_file_to_container(
                src_path=file_in_path, 
                dst_folder='/home/pythonuser'
        )
        # copy it back to tmpdir.
        runner.copy_file_from_container(
                src_path=os.path.join('/home/pythonuser/file.txt'),
                dst_folder=os.path.dirname(file_out_path)
        )
        # verify contents.
        with open(file_in_path, 'rt') as fin, open(file_out_path, 'rt') as fout:
            data_in = fin.read()
            data_out = fout.read()
            assert data_in == data_out

def test_init_with_wrong_container_name(docker_container):
    with pytest.raises(ValueError) as excinfo:
        _ = AgentRun(
                container_name="wrong-container-name",
                install_policy=TestUVInstallPolicy(),
                log_level=LOG_LEVEL
                )

    assert "Container wrong-container-name not found" in str(excinfo.value)


def test_init_with_stopped_container(docker_container):
    # stop the docker_container
    docker_container.stop()
    with pytest.raises(ValueError):
        _ = AgentRun(
                    container_name=docker_container.name,
                    install_policy=TestUVInstallPolicy(),
                    log_level=LOG_LEVEL
                    )

    assert f"Container {docker_container.name} is not running."
    docker_container.start()


def test_init_with_docker_not_running():
    from unittest.mock import patch

    # Create a mock client that raises an exception when ping is called
    with patch("docker.DockerClient") as MockClient:
        mock_client = MockClient.return_value
        mock_client.ping.side_effect = docker.errors.DockerException(
            "Docker daemon not available"
        )

        # Test that initializing AgentRun with this mock client raises ValueError
        with pytest.raises(RuntimeError) as excinfo:
            _ = AgentRun(
                    container_name="any-name", 
                    client=mock_client,
                    install_policy=TestUVInstallPolicy(),
                    log_level=LOG_LEVEL
                    )

        assert (
            "Failed to connect to Docker daemon. Please make sure Docker is running. Docker daemon not available"
            in str(excinfo.value)
        )


def test_init_w_dependency_mismatch(docker_container):
    with pytest.raises(ValueError) as excinfo:
        _ = AgentRun(
            container_name=docker_container.name,
            dependencies_whitelist=[],
            cached_dependencies=["requests"],
            install_policy=TestUVInstallPolicy(),
            log_level=LOG_LEVEL
        )
    assert "Some cached dependencies are not in the whitelist." in str(excinfo.value)


"""**benchmarking**"""


def execute_code_in_container_benchmark(session: AgentRunSession, code):
    output = session.execute_code(code)
    return output


def test_cached_dependency_benchmark(benchmark, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        cached_dependencies=["numpy"],
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user="root"
    )
    name = uuid4().hex
    session = runner.create_session(name)
    result = benchmark(
        execute_code_in_container_benchmark,
        session=session,
        code="import numpy as np\nprint(np.array([1, 2, 3]))",
    )
    assert result == "[1 2 3]\n"
    runner.close_session(session)
    check_session_clean(runner, name)


def test_dependency_benchmark(benchmark, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user="root"
    )
    name = uuid4().hex
    session = runner.create_session(name)
    result = benchmark(
        execute_code_in_container_benchmark,
        session=session,
        # use requests
        code="import requests\nprint(requests.get('https://example.com').status_code)",
    )
    assert result == "200\n"
    runner.close_session(session)
    check_session_clean(runner, name)


def test_exception_benchmark(benchmark, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user="root"
    )
    name = uuid4().hex
    session = runner.create_session(name)
    result = benchmark(
        execute_code_in_container_benchmark,
        session=session,
        code="print(f'{1/0}')",
    )
    ends_with = "ZeroDivisionError: division by zero\n"
    assert result.endswith(ends_with)
    runner.close_session(session)
    check_session_clean(runner, name)


def test_vanilla_benchmark(benchmark, docker_container):
    runner = AgentRun(
        container_name=docker_container.name,
        install_policy=TestUVInstallPolicy(),
        log_level=LOG_LEVEL,
        user="root"
    )
    name = uuid4().hex
    session = runner.create_session(name)
    result = benchmark(
        execute_code_in_container_benchmark,
        session=session,
        code="print('Hello, World!')",
    )
    assert result == "Hello, World!\n"
    runner.close_session(session)
    check_session_clean(runner, name)
