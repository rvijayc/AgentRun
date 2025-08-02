# conftest.py - Place this in your test directory or project root
import pytest
import subprocess
import time
import requests
import os
from pathlib import Path

def wait_for_api(url, timeout=30, interval=1):
    """Wait for API to be ready"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{url}/health")
            if response.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(interval)
    return False

@pytest.fixture(scope="session")
def docker_compose_files():
    """Path to docker-compose file"""
    # Adjust this path based on your project structure
    return [
            Path("agentrun_plus/docker-compose.yml").resolve(),
            Path("agentrun_plus/docker-compose.test.yml").resolve(),
    ]


@pytest.fixture(scope="session")
def docker_compose_project_name():
    """Docker compose project name to isolate test containers"""
    return "agentrun-test"

@pytest.fixture(scope="session")
def docker_services(docker_compose_files, docker_compose_project_name):
    """Ensure docker-compose services are up and running"""
    workdir = str(Path(__file__).parent.parent.resolve()/"agentrun_plus")

    for docker_compose_file in docker_compose_files:
        # Check if docker-compose file exists
        if not docker_compose_file.exists():
            pytest.exit(f"Docker compose file not found: {docker_compose_file}")
    
    compose_cmd = [
        "./docker-compose.sh", 
        "test-dev",
    ]
    
    # Pull images first (optional, but ensures you have latest)
    print("\nPulling Docker images...")
    subprocess.run(compose_cmd + ["pull"], check=True, cwd=workdir)
    
    # Start services
    print("Starting Docker services...")
    subprocess.run(compose_cmd + ["up", "-d"], check=True, cwd=workdir)
    
    # Get the API URL (you might need to adjust this based on your docker-compose.yml)
    api_urls = (
            "http://localhost:5000",
            "http://localhost:8000"
    )
    
    # Wait for services to be ready
    for api_url in api_urls:
        print(f"Waiting for API at {api_url} to be ready...")
        if not wait_for_api(api_url):
            # If services don't come up, show logs and fail
            subprocess.run(compose_cmd + ["logs"], cwd=workdir)
            subprocess.run(compose_cmd + ["down", "-v"], cwd=workdir)
            pytest.exit("Docker services failed to start")
        
    print("Docker services are ready!")
    
    yield api_urls
    
    # Teardown: stop and remove containers
    print("\nStopping Docker services...")
    subprocess.run(compose_cmd + ["down", "-v"], cwd=workdir)

@pytest.fixture(scope="session")
def api_base_url(docker_services):
    """Provides the API base URL after ensuring services are running"""
    return docker_services

# Alternative: Use pytest-docker plugin
# First install: pip install pytest-docker

@pytest.fixture(scope="session")
def docker_compose_services_with_plugin(docker_ip, docker_services):
    """If using pytest-docker plugin"""
    # The plugin automatically handles docker-compose up/down
    port = docker_services.port_for("agentrun-api", 8000)
    api_url = f"http://{docker_ip}:{port}"
    
    # Wait for readiness
    if not wait_for_api(api_url):
        pytest.exit("API service failed to become ready")
    
    return api_url

# You can also use class-based configuration
class DockerComposeConfig:
    """Configuration for docker-compose setup"""
    
    # Paths relative to project root
    COMPOSE_FILE = "agentrun-api/docker-compose.yml"
    PROJECT_NAME = "agentrun-test"
    
    # Service configuration
    API_SERVICE = "agentrun-api"
    API_PORT = 8000
    API_HEALTH_ENDPOINT = "/health"
    
    # Timeouts
    STARTUP_TIMEOUT = 60  # seconds
    HEALTH_CHECK_INTERVAL = 1  # seconds

# More advanced fixture with configuration
@pytest.fixture(scope="session")
def docker_environment():
    """Complete Docker environment setup with configuration"""
    config = DockerComposeConfig()
    compose_file = Path(config.COMPOSE_FILE).resolve()
    
    if not compose_file.exists():
        pytest.exit(f"Docker compose file not found: {compose_file}")
    
    compose = DockerCompose(
        compose_file=str(compose_file),
        project_name=config.PROJECT_NAME
    )
    
    try:
        # Start services
        compose.up()
        
        # Wait for health
        api_url = f"http://localhost:{config.API_PORT}"
        if not wait_for_api(api_url, config.STARTUP_TIMEOUT, config.HEALTH_CHECK_INTERVAL):
            compose.logs()
            raise Exception("Services failed to become healthy")
        
        yield {
            "api_url": api_url,
            "compose": compose,
            "config": config
        }
        
    finally:
        # Always cleanup
        compose.down(volumes=True)

# Helper class for docker-compose operations
class DockerCompose:
    def __init__(self, compose_file, project_name):
        self.compose_file = compose_file
        self.project_name = project_name
        self.base_cmd = [
            "docker", 
            "compose",
            "-f", self.compose_file,
        ]
    
    def up(self, detach=True, build=False):
        cmd = self.base_cmd + ["up"]
        if detach:
            cmd.append("-d")
        if build:
            cmd.append("--build")
        subprocess.run(cmd, check=True)
    
    def down(self, volumes=False):
        cmd = self.base_cmd + ["down"]
        if volumes:
            cmd.append("-v")
        subprocess.run(cmd, check=True)
    
    def logs(self, service=None):
        cmd = self.base_cmd + ["logs"]
        if service:
            cmd.append(service)
        subprocess.run(cmd)
    
    def ps(self):
        subprocess.run(self.base_cmd + ["ps"])

# Update your test file to use the fixture
@pytest.fixture(scope="module")
def api_client_with_docker(api_base_url):
    """Create API client with Docker-provided URL"""
    from test_agentrun_api import AgentRunAPIClient
    return AgentRunAPIClient(api_base_url)

# For debugging: fixture to show docker logs on test failure
@pytest.fixture(autouse=True)
def docker_logs_on_failure(request, docker_compose_files, docker_compose_project_name):
    """Show docker logs if a test fails"""
    yield
    
    workdir=Path(__file__)/".."/"agentrun_plus"
    workdir = str(workdir.resolve())

    if request.node.rep_call.failed:
        print("\n=== Docker Logs on Failure ===")
        compose_cmd = [
            "docker", 
            "compose",
            "-f", str(docker_compose_files[0]),
            "-f", str(docker_compose_files[1]),
            "logs", "--tail=50"
        ]
        subprocess.run(compose_cmd, cwd=workdir)

# Hook to add test result to node
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
