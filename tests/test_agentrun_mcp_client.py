"""
Tests for AgentRunMCPClient (Production MCP Client)

This test suite validates the AgentRunMCPClient implementation which provides
a production-ready client for the MCP server with the same interface as
AgentRunAPIClient.
"""

import pytest
import tempfile
import os
from agentrun_plus import AgentRunMCPClient, AgentRunAPIClient, SessionInfo


@pytest.fixture(scope="session")
def mcp_client(docker_services):
    """Create production MCP client for tests"""
    _, api_url = docker_services
    return AgentRunMCPClient(api_url)


@pytest.fixture(scope="session")
def api_client(docker_services):
    """Create REST API client for comparison tests"""
    _, api_url = docker_services
    return AgentRunAPIClient(api_url)


@pytest.fixture
def mcp_session(mcp_client):
    """Create a test session via MCP and clean up after test"""
    session = mcp_client.create_session()
    yield session
    # Cleanup
    try:
        mcp_client.close_session(session.session_id)
    except:
        pass  # Session might already be closed


class TestMCPClientHealth:
    """Tests for MCP client health check functionality"""

    def test_health_check(self, mcp_client):
        """Test health check via MCP client"""
        result = mcp_client.get_health()

        assert "status" in result
        assert result["status"] == "healthy"
        assert "active_sessions" in result
        assert isinstance(result["active_sessions"], int)
        assert result["active_sessions"] >= 0

    def test_health_check_returns_session_count(self, mcp_client):
        """Test that health check accurately reports active sessions"""
        # Get initial count
        initial_health = mcp_client.get_health()
        initial_count = initial_health["active_sessions"]

        # Create a session
        session = mcp_client.create_session()

        # Check health again
        new_health = mcp_client.get_health()
        assert new_health["active_sessions"] == initial_count + 1

        # Cleanup
        mcp_client.close_session(session.session_id)


class TestMCPClientSessionManagement:
    """Tests for MCP client session management"""

    def test_create_session(self, mcp_client):
        """Test creating a new session via MCP client"""
        session = mcp_client.create_session()

        assert isinstance(session, SessionInfo)
        assert session.session_id
        assert session.workdir
        assert session.source_path
        assert session.artifact_path
        assert session.session_id == session.workdir
        # New URL fields
        assert session.upload_url
        assert session.artifacts_url
        assert session.upload_url.endswith(f"/sessions/{session.session_id}/copy-to")
        assert session.artifacts_url.endswith(f"/sessions/{session.session_id}/artifacts")

        # Cleanup
        mcp_client.close_session(session.session_id)

    def test_get_session_info(self, mcp_client, mcp_session):
        """Test getting session information via MCP client"""
        result = mcp_client.get_session_info(mcp_session.session_id)

        assert result["session_id"] == mcp_session.session_id
        assert "source_path" in result
        assert "artifact_path" in result

    def test_get_nonexistent_session(self, mcp_client):
        """Test getting info for non-existent session"""
        result = mcp_client.get_session_info("nonexistent")

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()

    def test_list_sessions(self, mcp_client):
        """Test listing all active sessions via MCP client"""
        # Get initial count
        initial_result = mcp_client.list_sessions()
        initial_count = initial_result["count"]

        # Create multiple sessions
        sessions = []
        for _ in range(3):
            session = mcp_client.create_session()
            sessions.append(session)

        # List sessions
        result = mcp_client.list_sessions()

        assert result["count"] >= initial_count + 3
        assert len(result["active_sessions"]) == result["count"]

        # Verify our sessions are in the list
        for session in sessions:
            assert session.session_id in result["active_sessions"]

        # Cleanup
        for session in sessions:
            mcp_client.close_session(session.session_id)

    def test_close_session(self, mcp_client):
        """Test closing a session via MCP client"""
        # Create session
        session = mcp_client.create_session()

        # Close session
        close_result = mcp_client.close_session(session.session_id)

        assert close_result["success"] == True
        assert "successfully" in close_result["message"].lower()

        # Verify session is closed
        info_result = mcp_client.get_session_info(session.session_id)
        assert info_result.get("success") == False

    def test_close_nonexistent_session(self, mcp_client):
        """Test closing a non-existent session"""
        result = mcp_client.close_session("nonexistent")

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()


class TestMCPClientCodeExecution:
    """Tests for MCP client code execution"""

    def test_execute_simple_code(self, mcp_client, mcp_session):
        """Test executing simple Python code"""
        code = "print('Hello from MCP!')"
        result = mcp_client.execute_code(mcp_session.session_id, code)

        assert result["success"] == True
        assert "Hello from MCP!" in result["output"]

    def test_execute_code_with_error(self, mcp_client, mcp_session):
        """Test executing code that raises an error"""
        code = "raise ValueError('Test error')"
        result = mcp_client.execute_code(mcp_session.session_id, code)

        assert result["success"] == False
        assert "ValueError" in result["output"]
        assert "Test error" in result["output"]

    def test_execute_code_with_dependencies(self, mcp_client, mcp_session):
        """Test executing code with external dependencies"""
        code = """
import pandas as pd
df = pd.DataFrame({'a': [1, 2, 3]})
print(f'DataFrame shape: {df.shape}')
"""
        result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )

        assert result["success"] == True
        assert "DataFrame shape: (3, 1)" in result["output"]

    def test_execute_code_nonexistent_session(self, mcp_client):
        """Test executing code in non-existent session"""
        result = mcp_client.execute_code("nonexistent", "print('test')")

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()

    def test_execute_unsafe_code(self, mcp_client, mcp_session):
        """Test that unsafe code is blocked"""
        code = "import os; os.system('ls')"
        result = mcp_client.execute_code(mcp_session.session_id, code)

        assert result["success"] == False
        assert "unsafe" in result["output"].lower() or "restricted" in result["output"].lower()

    def test_execute_code_with_ignore_unsafe(self, mcp_client, mcp_session):
        """Test executing code with unsafe functions allowed"""
        code = """
with open('/tmp/test.txt', 'w') as f:
    f.write('test')
print('File written')
"""
        result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )

        assert result["success"] == True
        assert "File written" in result["output"]


class TestMCPClientFileOperations:
    """Tests for MCP client file upload/download"""

    def test_upload_file(self, mcp_client, mcp_session):
        """Test uploading a file via MCP client"""
        # Create a temporary file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write('Test file content')
            temp_file = f.name

        try:
            # Upload file
            result = mcp_client.upload_file(
                mcp_session.session_id,
                temp_file,
                'uploaded_test.txt'
            )

            assert result["success"] == True
            assert "uploaded_test.txt" in result.get("destination", "")

            # Verify file exists by reading it in code
            # NOTE: Uploaded files are in src/ subdirectory
            code = """
with open('src/uploaded_test.txt', 'r') as f:
    content = f.read()
print(f'Content: {content}')
"""
            exec_result = mcp_client.execute_code(
                mcp_session.session_id,
                code,
                ignore_unsafe_functions=['open']
            )

            assert exec_result["success"] == True
            assert "Test file content" in exec_result["output"]

        finally:
            os.unlink(temp_file)

    def test_upload_file_content(self, mcp_client, mcp_session):
        """Test uploading file content directly"""
        content = b"Direct content upload test"
        filename = "direct_upload.txt"

        result = mcp_client.upload_file_content(
            mcp_session.session_id,
            content,
            filename
        )

        assert result["success"] == True
        assert filename in result.get("destination", "")

        # Verify content
        # NOTE: Uploaded files are in src/ subdirectory
        code = f"""
with open('src/{filename}', 'rb') as f:
    content = f.read()
print(content.decode('utf-8'))
"""
        exec_result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )

        assert exec_result["success"] == True
        assert "Direct content upload test" in exec_result["output"]

    def test_upload_binary_file(self, mcp_client, mcp_session):
        """Test uploading binary file content"""
        # Create binary content
        binary_content = bytes(range(256))

        result = mcp_client.upload_file_content(
            mcp_session.session_id,
            binary_content,
            "binary_test.bin"
        )

        assert result["success"] == True

        # Verify binary content
        # NOTE: Uploaded files are in src/ subdirectory
        code = """
with open('src/binary_test.bin', 'rb') as f:
    content = f.read()
print(f'Length: {len(content)}')
print(f'First 5 bytes: {list(content[:5])}')
"""
        exec_result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )

        assert exec_result["success"] == True
        assert "Length: 256" in exec_result["output"]
        assert "[0, 1, 2, 3, 4]" in exec_result["output"]

    def test_download_file(self, mcp_client, mcp_session):
        """Test downloading a file via MCP client"""
        # Create a file in the session
        # NOTE: Files must be saved to artifacts/ to be downloadable
        code = """
with open('artifacts/download_test.txt', 'w') as f:
    f.write('Download test content')
print('File created in artifacts/')
"""
        exec_result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )
        assert exec_result["success"] == True

        # Download the file
        # NOTE: Must include artifacts/ prefix in path!
        with tempfile.TemporaryDirectory() as tmpdir:
            downloaded_path = mcp_client.download_file(
                mcp_session.session_id,
                'artifacts/download_test.txt',
                tmpdir,
                'downloaded.txt'
            )

            # Verify file was downloaded
            assert os.path.exists(downloaded_path)
            with open(downloaded_path, 'r') as f:
                content = f.read()
            assert content == 'Download test content'

    def test_upload_download_roundtrip(self, mcp_client, mcp_session):
        """Test upload and download preserve file content"""
        original_content = b"Test roundtrip content\nWith multiple lines\nAnd binary: \x00\x01\x02"

        # Upload
        mcp_client.upload_file_content(
            mcp_session.session_id,
            original_content,
            "roundtrip.bin"
        )

        # Execute code to copy to artifacts (without using shutil)
        # Read from src/ and write to artifacts/
        code = """
with open('src/roundtrip.bin', 'rb') as f:
    content = f.read()
with open('artifacts/roundtrip_out.bin', 'wb') as f:
    f.write(content)
print('Copied to artifacts')
"""
        exec_result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )
        assert exec_result["success"] == True

        # Download
        # NOTE: Must include artifacts/ prefix!
        with tempfile.TemporaryDirectory() as tmpdir:
            downloaded_path = mcp_client.download_file(
                mcp_session.session_id,
                'artifacts/roundtrip_out.bin',
                tmpdir
            )

            with open(downloaded_path, 'rb') as f:
                downloaded_content = f.read()

            assert downloaded_content == original_content


class TestMCPClientGetPackages:
    """Tests for MCP client get_packages functionality"""

    def test_get_packages(self, mcp_client):
        """Test getting installed packages via MCP client"""
        result = mcp_client.get_packages()

        assert "packages" in result
        assert "count" in result
        assert isinstance(result["packages"], list)
        assert result["count"] == len(result["packages"])
        assert result["count"] > 0

    def test_get_packages_contains_known_packages(self, mcp_client):
        """Test that pre-installed packages are in the list"""
        result = mcp_client.get_packages()
        package_names = [p.lower() for p in result["packages"]]
        for expected in ["numpy", "pandas", "matplotlib"]:
            assert expected in package_names, f"Expected '{expected}' in installed packages"

    def test_get_packages_matches_rest(self, mcp_client, api_client):
        """Test that MCP and REST return the same package list"""
        mcp_result = mcp_client.get_packages()
        rest_result = api_client.get_packages()

        assert set(mcp_result["packages"]) == set(rest_result["packages"])
        assert mcp_result["count"] == rest_result["count"]


class TestMCPClientRESTInteroperability:
    """Tests for MCP and REST API interoperability"""

    def test_mcp_session_visible_to_rest(self, mcp_client, api_client):
        """Test that MCP sessions are visible to REST API"""
        # Create session via MCP
        mcp_session = mcp_client.create_session()

        # List via REST
        rest_result = api_client.list_sessions()

        assert mcp_session.session_id in rest_result["active_sessions"]

        # Cleanup
        mcp_client.close_session(mcp_session.session_id)

    def test_rest_session_visible_to_mcp(self, mcp_client, api_client):
        """Test that REST sessions are visible to MCP"""
        # Create session via REST
        rest_session = api_client.create_session()

        # List via MCP
        mcp_result = mcp_client.list_sessions()

        assert rest_session.session_id in mcp_result["active_sessions"]

        # Cleanup
        api_client.close_session(rest_session.session_id)

    def test_mcp_upload_rest_execute(self, mcp_client, api_client):
        """Test uploading via MCP and executing via REST"""
        # Create session
        session = mcp_client.create_session()

        try:
            # Upload via MCP
            mcp_client.upload_file_content(
                session.session_id,
                b"Interop test content",
                "interop.txt"
            )

            # Execute via REST
            # NOTE: Uploaded files are in src/ subdirectory
            code = """
with open('src/interop.txt', 'r') as f:
    print(f.read())
"""
            result = api_client.execute_code(
                session.session_id,
                code,
                ignore_unsafe_functions=['open']
            )

            assert result["success"] == True
            assert "Interop test content" in result["output"]

        finally:
            mcp_client.close_session(session.session_id)


class TestMCPClientListArtifacts:
    """Tests for MCP client list_artifacts method"""

    def test_list_artifacts_empty(self, mcp_client, mcp_session):
        """Test listing artifacts in a fresh session"""
        result = mcp_client.list_artifacts(mcp_session.session_id)

        assert "artifacts" in result
        assert "count" in result
        assert "artifacts_url" in result
        assert result["count"] == 0
        assert result["artifacts"] == []

    def test_list_artifacts_with_file(self, mcp_client, mcp_session):
        """Test listing artifacts after generating a file via code"""
        code = """
with open('artifacts/result.txt', 'w') as f:
    f.write('generated output')
print('done')
"""
        exec_result = mcp_client.execute_code(
            mcp_session.session_id,
            code,
            ignore_unsafe_functions=['open']
        )
        assert exec_result["success"] == True

        result = mcp_client.list_artifacts(mcp_session.session_id)

        assert result["count"] == 1
        artifact = result["artifacts"][0]
        assert artifact["name"] == "result.txt"
        assert "size_bytes" in artifact
        assert "download_url" in artifact
        assert artifact["download_url"].startswith("http")

    def test_list_artifacts_nonexistent_session(self, mcp_client):
        """Test list_artifacts with a non-existent session"""
        result = mcp_client.list_artifacts("nonexistent")

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()


class TestMCPClientListSrc:
    """Tests for MCP client list_src method"""

    def test_list_src_empty(self, mcp_client, mcp_session):
        """Test listing src files in a fresh session"""
        result = mcp_client.list_src(mcp_session.session_id)

        assert "files" in result
        assert "count" in result
        assert result["count"] == 0
        assert result["files"] == []

    def test_list_src_after_upload(self, mcp_client, mcp_session):
        """Test listing src files after uploading a file"""
        content = b"test content for listing"
        mcp_client.upload_file_content(
            mcp_session.session_id,
            content,
            "listing_test.txt"
        )

        result = mcp_client.list_src(mcp_session.session_id)

        assert result["count"] >= 1
        names = [f["name"] for f in result["files"]]
        assert "listing_test.txt" in names

        for f in result["files"]:
            assert "name" in f
            assert "size_bytes" in f

        listing_file = next(f for f in result["files"] if f["name"] == "listing_test.txt")
        assert listing_file["size_bytes"] == len(content)

    def test_list_src_nonexistent_session(self, mcp_client):
        """Test list_src with a non-existent session"""
        result = mcp_client.list_src("nonexistent")

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()


class TestMCPClientInterfaceCompatibility:
    """Tests that MCP client has same interface as REST client"""

    def test_same_methods(self, mcp_client, api_client):
        """Test that both clients have the same public methods"""
        # Get public methods (excluding private/magic methods)
        mcp_methods = {m for m in dir(mcp_client) if not m.startswith('_')}
        api_methods = {m for m in dir(api_client) if not m.startswith('_')}

        # MCP client should have all REST client methods
        missing_methods = api_methods - mcp_methods
        # Allow for some internal differences (debug, session object, etc.)
        critical_methods = {
            'get_health', 'get_packages', 'create_session', 'get_session_info',
            'close_session', 'list_sessions', 'execute_code',
            'upload_file', 'upload_file_content', 'download_file',
            'list_artifacts', 'list_src'
        }

        for method in critical_methods:
            assert method in mcp_methods, f"MCP client missing method: {method}"
            assert method in api_methods, f"REST client missing method: {method}"

    def test_create_session_returns_same_type(self, mcp_client, api_client):
        """Test that create_session returns SessionInfo for both clients"""
        mcp_session = mcp_client.create_session()
        rest_session = api_client.create_session()

        assert isinstance(mcp_session, SessionInfo)
        assert isinstance(rest_session, SessionInfo)

        # Cleanup
        mcp_client.close_session(mcp_session.session_id)
        api_client.close_session(rest_session.session_id)

    def test_execute_code_returns_same_format(self, mcp_client, api_client):
        """Test that execute_code returns same format for both clients"""
        mcp_session = mcp_client.create_session()
        rest_session = api_client.create_session()

        try:
            code = "print('test')"

            mcp_result = mcp_client.execute_code(mcp_session.session_id, code)
            rest_result = api_client.execute_code(rest_session.session_id, code)

            # Both should have same keys
            assert set(mcp_result.keys()) == set(rest_result.keys())
            assert "success" in mcp_result
            assert "output" in mcp_result

        finally:
            mcp_client.close_session(mcp_session.session_id)
            api_client.close_session(rest_session.session_id)
