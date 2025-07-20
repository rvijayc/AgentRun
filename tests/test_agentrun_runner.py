import pytest
import requests
import tempfile
import os
import shutil
from pathlib import Path
import json
import time
from typing import Generator
import subprocess
import sys
import threading
from urllib.parse import urljoin

# Test configuration
TEST_SERVER_URL = "http://localhost:8000"
TEST_TIMEOUT = 30

class SandboxServerManager:
    """Manages the sandbox server for testing"""
    
    def __init__(self, url):
        self.server_url = url

@pytest.fixture(scope="session")
def sandbox_server(docker_services):
    """Session-scoped fixture that starts/stops the sandbox server"""
    runner_url, _ = docker_services
    server = SandboxServerManager(runner_url)
    yield server

@pytest.fixture
def client(sandbox_server):
    """HTTP client for making requests to the sandbox server"""
    session = requests.Session()
    session.timeout = TEST_TIMEOUT
    
    class TestClient:
        def __init__(self, base_url, session):
            self.base_url = base_url
            self.session = session
            
        def get(self, path, **kwargs):
            url = urljoin(self.base_url, path)
            return self.session.get(url, **kwargs)
            
        def post(self, path, **kwargs):
            url = urljoin(self.base_url, path)
            return self.session.post(url, **kwargs)
            
        def delete(self, path, **kwargs):
            url = urljoin(self.base_url, path)
            return self.session.delete(url, **kwargs)
    
    client = TestClient(sandbox_server.server_url, session)
    yield client
    session.close()

@pytest.fixture
def temp_file():
    """Create a temporary file for testing"""
    fd, path = tempfile.mkstemp()
    try:
        with os.fdopen(fd, 'w') as f:
            f.write("Test file content\nLine 2\nLine 3")
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)

class TestHealthCheck:
    """Test health check endpoint"""
    
    def test_health_endpoint(self, client):
        """Test that health endpoint returns 200"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "sandbox_dir" in data
        assert "python_version" in data

    def test_root_endpoint(self, client):
        """Test root endpoint"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "sandbox_dir" in data

class TestCommandExecution:
    """Test command execution functionality"""
    
    def test_simple_command_success(self, client):
        """Test executing a simple successful command"""
        request_data = {
            "command": "echo 'Hello World'",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Hello World" in data["stdout"]
        assert data["return_code"] == 0
        assert data["execution_time"] > 0

    def test_command_failure(self, client):
        """Test executing a command that fails"""
        request_data = {
            "command": "exit 1",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is False
        assert data["return_code"] == 1

    def test_command_with_stderr(self, client):
        """Test command that outputs to stderr"""
        request_data = {
            "command": "echo 'error message' >&2",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert "error message" in data["stderr"]

    def test_command_timeout(self, client):
        """Test command timeout"""
        request_data = {
            "command": "sleep 5",
            "timeout": 1
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is False
        assert "timed out" in data["stderr"].lower()

    def test_command_with_working_directory(self, client):
        """Test command execution with working directory"""
        # First create a directory
        client.post("/execute-command", json={
            "command": "mkdir -p test_dir",
            "timeout": 10
        })
        
        # Then run command in that directory
        request_data = {
            "command": "pwd",
            "working_dir": "test_dir",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "test_dir" in data["stdout"]

    def test_multiline_command(self, client):
        """Test executing multiline commands"""
        request_data = {
            "command": "echo 'Line 1' && echo 'Line 2' && echo 'Line 3'",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Line 1" in data["stdout"]
        assert "Line 2" in data["stdout"]
        assert "Line 3" in data["stdout"]

class TestPythonExecution:
    """Test Python code execution functionality"""
    
    def test_simple_python_code(self, client):
        """Test executing simple Python code"""
        request_data = {
            "code": "print('Hello from Python')\nresult = 2 + 2\nprint(f'2 + 2 = {result}')",
            "timeout": 10,
            "working_dir": "."
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Hello from Python" in data["stdout"]
        assert "2 + 2 = 4" in data["stdout"]
        assert data["execution_time"] > 0

    def test_python_code_with_error(self, client):
        """Test Python code that raises an exception"""
        request_data = {
            "code": "raise ValueError('Test error')",
            "timeout": 10
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is False
        assert "ValueError" in data["stderr"]
        assert "Test error" in data["stderr"]

    def test_python_imports(self, client):
        """Test Python code with imports"""
        request_data = {
            "code": """
import os
import sys
import json

print(f"Python version: {sys.version}")
print(f"Current directory: {os.getcwd()}")
data = {"test": "value"}
print(json.dumps(data))
""",
            "timeout": 10,
            "working_dir": "."
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Python version:" in data["stdout"]
        assert "Current directory:" in data["stdout"]
        assert '"test": "value"' in data["stdout"]

    def test_python_file_operations(self, client):
        """Test Python code that creates and reads files"""
        request_data = {
            "code": """
# Create a file
with open("test_file.txt", "w") as f:
    f.write("Hello from Python file!")

# Read it back
with open("test_file.txt", "r") as f:
    content = f.read()
    print(f"File content: {content}")

# List files
import os
files = os.listdir(".")
print(f"Files in directory: {files}")
""",
            "timeout": 10,
            "working_dir": "."
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Hello from Python file!" in data["stdout"]
        assert "test_file.txt" in data["stdout"]

    def test_python_variables_and_calculations(self, client):
        """Test Python code with variables and calculations"""
        request_data = {
            "code": """
# Math operations
a = 10
b = 20
sum_result = a + b
product = a * b
division = b / a

print(f"Sum: {sum_result}")
print(f"Product: {product}")
print(f"Division: {division}")

# String operations
text = "Hello, World!"
print(f"Text: {text}")
print(f"Length: {len(text)}")
print(f"Uppercase: {text.upper()}")
""",
            "working_dir": ".",
            "timeout": 10
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is True
        assert "Sum: 30" in data["stdout"]
        assert "Product: 200" in data["stdout"]
        assert "Division: 2.0" in data["stdout"]
        assert "HELLO, WORLD!" in data["stdout"]

    def test_python_syntax_error(self, client):
        """Test Python code with syntax error"""
        request_data = {
            "code": "print('missing closing quote",
            "working_dir": ".",
            "timeout": 10
        }
        response = client.post("/execute-python", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["success"] is False
        assert "SyntaxError" in data["stderr"] or "Error" in data["result"]

class TestFileOperations:
    """Test file upload/download functionality"""
    
    def test_upload_file(self, client, temp_file):
        """Test file upload"""
        with open(temp_file, "rb") as f:
            files = {"file": ("test.txt", f, "text/plain")}
            data = {"destination": "uploaded_test.txt"}
            response = client.post("/upload-file", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "uploaded successfully" in result["message"]

    def test_download_file(self, client):
        """Test file download"""
        # First create a file
        client.post("/execute-python", json={
            "code": """
with open("download_test.txt", "w") as f:
    f.write("Content for download test")
""",
            "working_dir": ".",
            "timeout": 10
        })
        
        # Then download it
        response = client.get("/download-file", params={"file_path": "download_test.txt"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert b"Content for download test" in response.content

    def test_download_nonexistent_file(self, client):
        """Test downloading a file that doesn't exist"""
        response = client.get("/download-file", params={"file_path": "nonexistent.txt"})
        assert response.status_code == 404

    def test_copy_file(self, client):
        """Test file copying"""
        # Create source file
        client.post("/execute-python", json={
            "code": """
with open("source.txt", "w") as f:
    f.write("Source file content")
""",
            "working_dir": ".",
            "timeout": 10
        })
        
        # Copy file
        response = client.post("/copy-file", params={
            "source": "source.txt",
            "destination": "copied.txt"
        })
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"] is True
        
        # Verify copy was successful
        verify_response = client.get("/download-file", params={"file_path": "copied.txt"})
        assert verify_response.status_code == 200
        assert b"Source file content" in verify_response.content

    def test_list_files(self, client):
        """Test file listing"""
        # Create some test files
        client.post("/execute-python", json={
            "code": """
import os
os.makedirs("test_dir", exist_ok=True)
with open("file1.txt", "w") as f:
    f.write("File 1")
with open("file2.txt", "w") as f:
    f.write("File 2")
with open("test_dir/file3.txt", "w") as f:
    f.write("File 3")
""",
            "working_dir": ".",
            "timeout": 10
        })
        
        # List files in root
        response = client.get("/list-files")
        assert response.status_code == 200
        
        data = response.json()
        assert "files" in data
        file_names = [f["name"] for f in data["files"]]
        assert "file1.txt" in file_names
        assert "file2.txt" in file_names
        assert "test_dir" in file_names

    def test_delete_file(self, client):
        """Test file deletion"""
        # Create a file to delete
        client.post("/execute-python", json={
            "code": """
with open("to_delete.txt", "w") as f:
    f.write("This file will be deleted")
""",
            "working_dir": ".",
            "timeout": 10
        })
        
        # Delete the file
        response = client.delete("/delete-file", params={"file_path": "to_delete.txt"})
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"] is True
        assert "deleted successfully" in result["message"]
        
        # Verify file is gone
        download_response = client.get("/download-file", params={"file_path": "to_delete.txt"})
        assert download_response.status_code == 404

    def test_upload_binary_file(self, client):
        """Test uploading a binary file"""
        # Create a simple binary file
        binary_data = bytes(range(256))
        
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(binary_data)
            temp_file.seek(0)
            
            files = {"file": ("binary.dat", temp_file, "application/octet-stream")}
            data = {"destination": "uploaded_binary.dat"}
            response = client.post("/upload-file", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        
        # Verify the binary file was uploaded correctly
        download_response = client.get("/download-file", params={"file_path": "uploaded_binary.dat"})
        assert download_response.status_code == 200
        assert download_response.content == binary_data

class TestSecurityAndEdgeCases:
    """Test security features and edge cases"""
    
    def test_path_traversal_protection(self, client):
        """Test that path traversal attacks are prevented"""
        # Try to access files outside sandbox
        response = client.get("/download-file", params={"file_path": "../../../etc/passwd"})
        assert response.status_code == 400
        
        # Try with upload
        with tempfile.NamedTemporaryFile() as temp:
            temp.write(b"malicious content")
            temp.seek(0)
            files = {"file": ("test.txt", temp, "text/plain")}
            data = {"destination": "../../../tmp/malicious.txt"}
            response = client.post("/upload-file", files=files, data=data)
        
        # Should either fail or be contained within sandbox
        if response.status_code == 200:
            # If it succeeds, the file should be in sandbox, not /tmp
            result = response.json()
            assert result['success'] == False
            assert "outside sandbox directory" in result['message']

    def test_large_command_output(self, client):
        """Test handling of commands with large output"""
        request_data = {
            "command": "python -c \"print('x' * 10000)\"",
            "timeout": 10
        }
        response = client.post("/execute-command", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert len(data["stdout"]) > 9000  # Should contain the large output

    def test_invalid_python_code(self, client):
        """Test various types of invalid Python code"""
        invalid_codes = [
            "import nonexistent_module",
            "undefined_variable",
            "1 / 0",
            "int('not_a_number')"
        ]
        
        for code in invalid_codes:
            response = client.post("/execute-python", json={
                "code": code,
                "working_dir": ".",
                "timeout": 10
            })
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False

    def test_empty_requests(self, client):
        """Test handling of empty or invalid requests"""
        # Empty command
        response = client.post("/execute-command", json={"command": " ", "timeout": 10, "working_dir":"."})
        assert response.status_code == 200
        
        # Empty Python code
        response = client.post("/execute-python", json={"code": " ", "timeout": 10})
        assert response.status_code == 200
        
        # Invalid JSON (this should return 422)
        response = client.post("/execute-command", 
                             data="invalid json", 
                             headers={"Content-Type": "application/json"})
        assert response.status_code == 422

    def test_concurrent_requests(self, client):
        """Test multiple concurrent requests using threading"""
        def make_request(i):
            return client.post("/execute-python", json={
                "code": f"print('Request {i}')\nimport time\ntime.sleep(0.1)",
                "working_dir": ".",
                "timeout": 10
            })
        
        # Make 5 concurrent requests using threads
        import concurrent.futures
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(5)]
            responses = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        # All should succeed
        for response in responses:
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

class TestPerformance:
    """Performance and stress tests"""
    
    def test_rapid_requests(self, client):
        """Test rapid successive requests"""
        start_time = time.time()
        
        responses = []
        for i in range(20):
            response = client.post("/execute-python", json={
                "code": f"print('Fast request {i}')",
                "timeout": 5
            })
            responses.append(response)
        
        end_time = time.time()
        
        # All should succeed
        for response in responses:
            assert response.status_code == 200
        
        # Should complete reasonably quickly
        assert end_time - start_time < 30

    def test_large_file_operations(self, client):
        """Test operations with larger files"""
        # Create a larger file (about 1MB)
        large_content = "Large file content\n" * 50000
        json={
            "code": f"""
content = '''{large_content}'''
with open("large_file.txt", "w") as f:
    f.write(content)
print(f"Created file with {{len(content)}} characters")
""",
            "timeout": 30
        }       
        response = client.post("/execute-python", json=json)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        
        # Test downloading the large file
        download_response = client.get("/download-file", params={"file_path": "large_file.txt"})
        assert download_response.status_code == 200
        assert len(download_response.content) > 500000  # Should be substantial

    def test_long_running_operations(self, client):
        """Test longer running operations"""
        response = client.post("/execute-python", json={
            "code": """
import time
for i in range(5):
    print(f"Step {i+1}/5")
    time.sleep(0.5)
print("Completed long operation")
""",
            "timeout": 10
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Completed long operation" in data["stdout"]

# Custom markers for different test categories
pytest.mark.unit = pytest.mark.unit if hasattr(pytest.mark, 'unit') else lambda f: f
pytest.mark.integration = pytest.mark.integration if hasattr(pytest.mark, 'integration') else lambda f: f
pytest.mark.security = pytest.mark.security if hasattr(pytest.mark, 'security') else lambda f: f

# Apply markers to test classes
TestHealthCheck = pytest.mark.unit(TestHealthCheck)
TestCommandExecution = pytest.mark.unit(TestCommandExecution)
TestPythonExecution = pytest.mark.unit(TestPythonExecution)
TestFileOperations = pytest.mark.integration(TestFileOperations)
TestSecurityAndEdgeCases = pytest.mark.security(TestSecurityAndEdgeCases)

if __name__ == "__main__":
    # Run tests when script is executed directly
    pytest.main([__file__, "-v"])
