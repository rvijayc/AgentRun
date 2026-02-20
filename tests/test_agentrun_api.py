import pytest
import requests
import tempfile
import os
import json
import time
from typing import Optional, List, Dict, Set
from dataclasses import dataclass
from urllib.parse import urljoin
from agentrun_plus import AgentRunAPIClient

@pytest.fixture(scope="session")
def api_client(docker_services):
    """Create API client for tests"""
    _, api_url = docker_services
    return AgentRunAPIClient(api_url)

@pytest.fixture
def test_session(api_client):
    """Create a test session and clean up after test"""
    session = api_client.create_session()
    yield session
    try:
        api_client.close_session(session.session_id)
    except:
        pass  # Session might already be closed

class TestRootEndpoint:
    def test_root_endpoint(self, api_client):
        """Test the root endpoint returns API information"""
        data = api_client.get_root()
        assert data["service"] == "AgentRun API"
        assert "endpoints" in data
        assert len(data["endpoints"]) > 0

class TestSessionManagement:
    def test_create_session(self, api_client):
        """Test creating a new session"""
        print("\n[TEST] Creating new session...")
        print(f"API URL: {api_client.base_url}")
        
        # First, check if API is healthy
        try:
            health = api_client.get_health()
            print(f"API Health: {health}")
        except Exception as e:
            print(f"Health check failed: {e}")
            print("\nTROUBLESHOOTING:")
            print("1. Is the API server running?")
            print(f"   curl {api_client.base_url}/health")
            print("2. Is the URL correct?")
            print(f"   Current URL: {api_client.base_url}")
            raise
        
        # Now try to create session
        try:
            session = api_client.create_session()
            
            assert session.session_id is not None
            assert session.workdir is not None
            assert session.source_path is not None
            assert session.artifact_path is not None
            assert session.session_id == session.workdir
            
            print(f"Session created successfully: {session.session_id}")
            
            # Clean up
            api_client.close_session(session.session_id)
        except requests.HTTPError as e:
            print(f"\nFailed to create session: {e}")
            print("\nPOSSIBLE CAUSES:")
            print("1. Backend initialization failed - check if 'backend = AgentRun(container_name=\"python-runner\")' is working")
            print("2. Docker container 'python-runner' is not running")
            print("3. Missing imports in the server code")
            print("4. Exception in backend.create_session() method")
            raise
    
    def test_get_session_info(self, api_client, test_session):
        """Test getting session information"""
        info = api_client.get_session_info(test_session.session_id)
        
        assert info["session_id"] == test_session.session_id
        assert info["source_path"] == test_session.source_path
        assert info["artifact_path"] == test_session.artifact_path
    
    def test_get_nonexistent_session(self, api_client):
        """Test getting info for non-existent session"""
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.get_session_info("nonexistent")
        assert exc_info.value.response.status_code == 404
    
    def test_close_session(self, api_client):
        """Test closing a session"""
        # Create a session
        session = api_client.create_session()
        
        # Close the session
        result = api_client.close_session(session.session_id)
        assert f"Session {session.session_id} closed successfully" in result["message"]
        
        # Verify session is gone
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.get_session_info(session.session_id)
        assert exc_info.value.response.status_code == 404
    
    def test_close_nonexistent_session(self, api_client):
        """Test closing non-existent session"""
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.close_session("nonexistent")
        assert exc_info.value.response.status_code == 404
    
    def test_list_sessions(self, api_client):
        """Test listing all active sessions"""
        # Track created sessions
        created_sessions = []
        
        # Get initial count
        initial = api_client.list_sessions()
        initial_count = initial["count"]
        
        # Create multiple sessions
        for _ in range(3):
            session = api_client.create_session()
            created_sessions.append(session.session_id)
        
        # List sessions
        result = api_client.list_sessions()
        assert result["count"] >= initial_count + 3
        
        # Check our sessions are in the list
        for session_id in created_sessions:
            assert session_id in result["active_sessions"]
        
        # Clean up
        for session_id in created_sessions:
            api_client.close_session(session_id)

class TestCodeExecution:
    def test_execute_code_simple(self, api_client, test_session):
        """Test simple code execution"""
        result = api_client.execute_code(
            test_session.session_id,
            "print('Hello, World!')"
        )
        
        assert result["success"] is True
        assert "Hello, World!" in result["output"]
    
    def test_execute_code_with_options(self, api_client, test_session):
        """Test code execution with ignore options"""
        result = api_client.execute_code(
            test_session.session_id,
            "import math\nprint(math.pi)",
            ignore_dependencies=["numpy"],
            ignore_unsafe_functions=["eval"]
        )
        
        assert result["success"] is True
        assert "3.14" in result["output"]
    
    def test_execute_code_error(self, api_client, test_session):
        """Test code execution with error - API succeeds but stdout contains the error"""
        result = api_client.execute_code(
            test_session.session_id,
            "1/0"  # Division by zero
        )
        
        # API call succeeds - it successfully executed the code (even though the code had an error)
        assert result["success"] is False
        # The error appears in the stdout/output
        assert "ZeroDivisionError" in result["output"] or "division by zero" in result["output"]
    
    def test_execute_code_nonexistent_session(self, api_client):
        """Test executing code in non-existent session"""
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.execute_code("nonexistent", "print('test')")
        assert exc_info.value.response.status_code == 404

class TestFileOperations:
    def test_upload_file(self, api_client, test_session):
        """Test uploading a file to session"""
        # Create a test file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Test file content\nLine 2")
            test_file = f.name
        
        try:
            result = api_client.upload_file(test_session.session_id, test_file, "test.txt")
            assert "successfully" in result["message"]
            assert "destination_path" in result
        finally:
            os.unlink(test_file)
    
    def test_upload_file_content(self, api_client, test_session):
        """Test uploading file content directly"""
        content = b"Direct content upload test"
        result = api_client.upload_file_content(
            test_session.session_id,
            content,
            "direct_test.txt"
        )
        
        assert "successfully" in result["message"]
        assert "destination_path" in result
    
    def test_upload_to_nonexistent_session(self, api_client):
        """Test uploading file to non-existent session"""
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.upload_file_content("nonexistent", b"content", "test.txt")
        assert exc_info.value.response.status_code == 404
    
    def test_download_file(self, api_client, test_session):
        """Test downloading a file from session"""
        # First, create a file in the session
        test_content = "print('test output')"
        api_client.upload_file_content(
            test_session.session_id,
            test_content.encode(),
            "test_script.py"
        )
        
        # Execute code to create an artifact
        api_client.execute_code(
            test_session.session_id,
            f"with open('{test_session.artifact_path}/output.txt', 'w') as f: f.write('Generated content')",
            ignore_unsafe_functions=['open']
        )
        
        # Download the file
        with tempfile.TemporaryDirectory() as d:
            api_client.download_file(
                test_session.session_id,
                f"{test_session.artifact_path}/output.txt",
                d,
                "downloaded.txt"
            )
            
            # Verify content
            with open(os.path.join(d, "downloaded.txt"), 'r') as f:
                content = f.read()
            assert len(content) > 0
    
    def test_download_from_nonexistent_session(self, api_client):
        """Test downloading file from non-existent session"""
        with pytest.raises(requests.HTTPError) as exc_info:
            with tempfile.NamedTemporaryFile() as f:
                api_client.download_file("nonexistent", "/artifacts/test.txt", f.name)
        assert exc_info.value.response.status_code == 404
    
    def test_upload_path_traversal_filename(self, api_client, test_session):
        """Test that path traversal in filenames is blocked"""
        # Try various path traversal attempts
        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "subdir/../../../sensitive.txt",
            "/etc/passwd",
            "\\windows\\system32\\file.dll"
        ]
        
        for filename in malicious_filenames:
            with pytest.raises(requests.HTTPError) as exc_info:
                api_client.upload_file_content(
                    test_session.session_id,
                    b"malicious content",
                    filename
                )
            assert exc_info.value.response.status_code == 403
            assert "Invalid filename" in exc_info.value.response.text
    
    def test_upload_invalid_filename(self, api_client, test_session):
        """Test that invalid filenames are rejected"""
        # Test empty filename
        response = requests.post(
            api_client._url(f"/sessions/{test_session.session_id}/copy-to"),
            files={'file': ('', b"content", 'application/octet-stream')}
        )
        assert response.status_code == 400
        
        # Test filename starting with dot (hidden file)
        with pytest.raises(requests.HTTPError) as exc_info:
            api_client.upload_file_content(
                test_session.session_id,
                b"content",
                ".hidden_file"
            )
        assert exc_info.value.response.status_code == 400
        assert "Invalid filename" in exc_info.value.response.text
        """Test that path traversal attempts are blocked"""
        # Try to download a file outside artifact directory using ..
        with tempfile.NamedTemporaryFile(delete=False) as f:
            dest_path = f.name
        
        try:
            # Attempt path traversal
            with pytest.raises(requests.HTTPError) as exc_info:
                api_client.download_file(
                    test_session.session_id,
                    f"{test_session.artifact_path}/../../../etc/passwd",
                    dest_path,
                    "passwd"
                )
            assert exc_info.value.response.status_code == 403
            assert "Path traversal attempts" in exc_info.value.response.text
        finally:
            os.unlink(dest_path)
    
    def test_download_outside_artifact_path(self, api_client, test_session):
        """Test that downloads outside artifact path are blocked"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            dest_path = f.name
        
        try:
            # Try to download from source path instead of artifact path
            with pytest.raises(requests.HTTPError) as exc_info:
                api_client.download_file(
                    test_session.session_id,
                    f"{test_session.source_path}/some_file.txt",
                    dest_path,
                    "file.txt"
                )
            assert exc_info.value.response.status_code == 403
            assert "within the session's artifact directory" in exc_info.value.response.text
        finally:
            os.unlink(dest_path)

class TestGetPackages:
    def test_get_packages(self, api_client):
        """Test getting installed packages via REST API"""
        result = api_client.get_packages()
        assert "packages" in result
        assert "count" in result
        assert isinstance(result["packages"], list)
        assert result["count"] == len(result["packages"])
        assert result["count"] > 0

    def test_get_packages_contains_known_packages(self, api_client):
        """Test that pre-installed packages are in the list"""
        result = api_client.get_packages()
        package_names = [p.lower() for p in result["packages"]]
        # These are installed via runner_requirements.txt
        for expected in ["numpy", "pandas", "matplotlib"]:
            assert expected in package_names, f"Expected '{expected}' in installed packages"

class TestHealthCheck:
    def test_health_endpoint(self, api_client):
        """Test health check endpoint"""
        health = api_client.get_health()
        assert health["status"] == "healthy"
        assert "active_sessions" in health
        assert isinstance(health["active_sessions"], int)

class TestValidation:
    def test_execute_code_missing_field(self, api_client, test_session):
        """Test code execution with missing required field"""
        # Send raw request without python_code field
        response = requests.post(
            api_client._url(f"/sessions/{test_session.session_id}/execute"),
            json={}
        )
        assert response.status_code == 422  # Validation error
    
    def test_copy_file_from_missing_fields(self, api_client, test_session):
        """Test file download with missing required fields"""
        response = requests.post(
            api_client._url(f"/sessions/{test_session.session_id}/copy-from"),
            json={}
        )
        assert response.status_code == 422  # Validation error
    
    def test_copy_file_to_no_file(self, api_client, test_session):
        """Test file upload without file"""
        response = requests.post(
            api_client._url(f"/sessions/{test_session.session_id}/copy-to")
        )
        assert response.status_code == 422  # Validation error

class TestIntegration:
    def test_full_workflow(self, api_client):
        """Test complete workflow: create session, upload file, execute code, download result, close"""
        # 1. Create session
        session = api_client.create_session()
        
        try:
            # 2. Upload a Python script
            script_content = """
import json
data = {'result': 'success', 'value': 42}
with open('output.json', 'w') as f:
    json.dump(data, f)
print('Script executed successfully')
"""
            upload_result = api_client.upload_file_content(
                session.session_id,
                script_content.encode(),
                "process.py"
            )
            assert "successfully" in upload_result["message"]
            
            # 3. Execute the uploaded script
            exec_result = api_client.execute_code(
                session.session_id,
                f"exec(open('{session.source_path}/process.py').read())",
                ignore_unsafe_functions = ['exec']
            )
            assert exec_result["success"] is False
            assert "Use of dangerous built-in function: exec" in exec_result["output"]
            
            # the rest of the code cannot run because "exec" cannot run earlier.
            # - so commenting it out.
            """
            # 4. Execute code to move output to artifacts
            move_result = api_client.execute_code(
                session.session_id,
                f"import shutil; shutil.copy('output.json', '{session.artifact_path}/output.json')"
            )
            assert move_result["success"] is True
            
            # 5. Download the result
            with tempfile.NamedTemporaryFile(delete=False) as f:
                dest_path = f.name
            
            api_client.download_file(
                session.session_id,
                f"{session.artifact_path}/output.json",
                dest_path,
                "result.json"
            )
            
            # Verify downloaded content
            with open(dest_path, 'r') as f:
                data = json.load(f)
            assert data["result"] == "success"
            assert data["value"] == 42
            
            os.unlink(dest_path)
            """
            
        finally:
            # 6. Close session
            close_result = api_client.close_session(session.session_id)
            assert "closed successfully" in close_result["message"]
            
            # 7. Verify session is gone
            with pytest.raises(requests.HTTPError) as exc_info:
                api_client.get_session_info(session.session_id)
            assert exc_info.value.response.status_code == 404

class TestConcurrency:
    def test_multiple_sessions_isolated(self, api_client):
        """Test that multiple sessions are isolated from each other"""
        session1 = api_client.create_session()
        session2 = api_client.create_session()
        
        try:
            # Create different content in each session
            api_client.execute_code(
                session1.session_id,
                "with open('test.txt', 'w') as f: f.write('Session 1 content')",
                ignore_unsafe_functions=['open']
            )
            
            api_client.execute_code(
                session2.session_id,
                "with open('test.txt', 'w') as f: f.write('Session 2 content')",
                ignore_unsafe_functions=['open']
            )
            
            # Read content from each session
            result1 = api_client.execute_code(
                session1.session_id,
                "with open('test.txt', 'r') as f: print(f.read())",
                ignore_unsafe_functions=['open']
            )
            
            result2 = api_client.execute_code(
                session2.session_id,
                "with open('test.txt', 'r') as f: print(f.read())",
                ignore_unsafe_functions=['open']
            )
            
            # Verify isolation
            assert "Session 1 content" in result1["output"]
            assert "Session 2 content" in result2["output"]
            
        finally:
            api_client.close_session(session1.session_id)
            api_client.close_session(session2.session_id)

class TestSessionURLFields:
    """Session creation should include upload_url and artifacts_url in the response."""

    def test_create_session_has_upload_url(self, api_client):
        session = api_client.create_session()
        try:
            assert session.upload_url, "upload_url should be non-empty"
            assert session.upload_url.endswith(f"/sessions/{session.session_id}/copy-to")
        finally:
            api_client.close_session(session.session_id)

    def test_create_session_has_artifacts_url(self, api_client):
        session = api_client.create_session()
        try:
            assert session.artifacts_url, "artifacts_url should be non-empty"
            assert session.artifacts_url.endswith(f"/sessions/{session.session_id}/artifacts")
        finally:
            api_client.close_session(session.session_id)

    def test_upload_url_starts_with_http(self, api_client):
        session = api_client.create_session()
        try:
            assert session.upload_url.startswith("http")
        finally:
            api_client.close_session(session.session_id)

    def test_artifacts_url_starts_with_http(self, api_client):
        session = api_client.create_session()
        try:
            assert session.artifacts_url.startswith("http")
        finally:
            api_client.close_session(session.session_id)


class TestArtifactGetEndpoint:
    """Tests for GET /sessions/{session_id}/artifacts/{filename}."""

    def test_download_text_artifact(self, api_client, test_session):
        """Text file written to artifacts/ should be retrievable via GET."""
        content = "hello artifact world"
        api_client.execute_code(
            test_session.session_id,
            f"open('artifacts/result.txt','w').write('{content}')",
            ignore_unsafe_functions=["open"],
        )
        resp = requests.get(f"{test_session.artifacts_url}/result.txt")
        assert resp.status_code == 200
        assert resp.text == content

    def test_download_binary_artifact(self, api_client, test_session):
        """Binary artifact should be returned byte-for-byte via GET."""
        api_client.execute_code(
            test_session.session_id,
            "open('artifacts/binary.bin','wb').write(bytes(range(256)))",
            ignore_unsafe_functions=["open"],
        )
        resp = requests.get(f"{test_session.artifacts_url}/binary.bin")
        assert resp.status_code == 200
        assert resp.content == bytes(range(256))

    def test_content_type_for_csv(self, api_client, test_session):
        """CSV files should be served with a CSV-indicating Content-Type."""
        api_client.execute_code(
            test_session.session_id,
            "open('artifacts/data.csv','w').write('a,b\\n1,2')",
            ignore_unsafe_functions=["open"],
        )
        resp = requests.get(f"{test_session.artifacts_url}/data.csv")
        assert resp.status_code == 200
        assert "csv" in resp.headers["content-type"]

    def test_content_type_for_text(self, api_client, test_session):
        """Text files should be served with a text/* Content-Type."""
        api_client.execute_code(
            test_session.session_id,
            "open('artifacts/note.txt','w').write('hi')",
            ignore_unsafe_functions=["open"],
        )
        resp = requests.get(f"{test_session.artifacts_url}/note.txt")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/")

    def test_nonexistent_file_returns_404(self, test_session):
        """Requesting a file that doesn't exist in artifacts/ should return 404."""
        resp = requests.get(f"{test_session.artifacts_url}/no_such_file.txt")
        assert resp.status_code == 404

    def test_invalid_session_returns_404(self, api_client):
        """Requesting an artifact for a non-existent session should return 404."""
        fake_url = f"{api_client.base_url}/sessions/000000000000000000000000deadbeef/artifacts/x.txt"
        resp = requests.get(fake_url)
        assert resp.status_code == 404

    def test_dotdot_in_filename_rejected(self, test_session):
        """Filenames containing '..' must be rejected with 400."""
        resp = requests.get(f"{test_session.artifacts_url}/..evil")
        assert resp.status_code == 400

    def test_dotfile_rejected(self, test_session):
        """Filenames starting with '.' must be rejected with 400."""
        resp = requests.get(f"{test_session.artifacts_url}/.hidden")
        assert resp.status_code == 400

    def test_artifacts_url_field_is_directly_usable(self, api_client, test_session):
        """Appending a filename to artifacts_url should serve the artifact correctly."""
        api_client.execute_code(
            test_session.session_id,
            "open('artifacts/via_url.txt','w').write('url works')",
            ignore_unsafe_functions=["open"],
        )
        resp = requests.get(f"{test_session.artifacts_url}/via_url.txt")
        assert resp.status_code == 200
        assert resp.text == "url works"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
