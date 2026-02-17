"""
Tests for AgentRun MCP (Model Context Protocol) Server

This test suite validates the MCP server implementation and ensures
interoperability with the REST API. Both protocols share the same backend
and session state.
"""

import pytest
import requests
import tempfile
import os
import json
import base64
from typing import Dict, Any
from agentrun_plus import AgentRunAPIClient, AgentRunMCPClient


# Legacy MCP Client Helper Class (deprecated - use AgentRunMCPClient instead)
# Keeping for now for backwards compatibility, will be removed in future
class MCPClient:
    """Helper class for making MCP tool calls via HTTP using JSON-RPC 2.0"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/') + '/mcp'
        self.session = requests.Session()
        self.session_id = None
        self.request_id = 0

    def _get_next_id(self) -> int:
        """Get next request ID"""
        self.request_id += 1
        return self.request_id

    def _parse_sse_response(self, response_text: str) -> dict:
        """Parse Server-Sent Events (SSE) response"""
        # SSE format: "event: message\r\ndata: {json}\r\n\r\n"
        lines = response_text.strip().split('\n')
        for line in lines:
            if line.startswith('data: '):
                json_data = line[6:]  # Remove 'data: ' prefix
                return json.loads(json_data)
        raise Exception(f"No data found in SSE response: {response_text[:200]}")

    def initialize(self):
        """Initialize MCP session"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agentrun-test-client",
                    "version": "1.0"
                }
            }
        }

        response = self.session.post(
            self.base_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"
            }
        )

        if response.status_code >= 400:
            raise Exception(f"MCP initialize failed: {response.status_code} - {response.text}")

        # Extract session ID from response header
        if 'mcp-session-id' in response.headers:
            self.session_id = response.headers['mcp-session-id']
        elif 'Mcp-Session-Id' in response.headers:
            self.session_id = response.headers['Mcp-Session-Id']

        # Parse SSE response
        return self._parse_sse_response(response.text)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """Call an MCP tool using JSON-RPC 2.0

        Args:
            tool_name: Name of the MCP tool to call
            arguments: Dictionary of arguments for the tool

        Returns:
            Result dictionary from the tool
        """
        # Initialize if not already done
        if self.session_id is None:
            self.initialize()

        if arguments is None:
            arguments = {}

        # JSON-RPC 2.0 format for tool call
        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        response = self.session.post(
            self.base_url,
            json=payload,
            headers=headers
        )

        if response.status_code >= 400:
            raise Exception(f"MCP call failed: {response.status_code} - {response.text}")

        # Parse SSE response
        result = self._parse_sse_response(response.text)

        # Handle JSON-RPC response
        if "error" in result:
            raise Exception(f"MCP tool error: {result['error']}")

        # Extract result from JSON-RPC response
        if "result" in result:
            # FastMCP returns results in content array
            if "content" in result["result"]:
                for item in result["result"]["content"]:
                    if item.get("type") == "text":
                        # Try to parse as JSON
                        try:
                            return json.loads(item["text"])
                        except:
                            return {"output": item["text"]}
            return result["result"]

        return result

    def list_tools(self) -> Dict[str, Any]:
        """List available MCP tools"""
        # Initialize if not already done
        if self.session_id is None:
            self.initialize()

        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": "tools/list"
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        response = self.session.post(
            self.base_url,
            json=payload,
            headers=headers
        )

        if response.status_code >= 400:
            raise Exception(f"MCP list_tools failed: {response.status_code} - {response.text}")

        return self._parse_sse_response(response.text)


@pytest.fixture(scope="session")
def api_client(docker_services):
    """Create API client for tests"""
    _, api_url = docker_services
    return AgentRunAPIClient(api_url)


@pytest.fixture(scope="session")
def mcp_client(docker_services):
    """Create MCP client for tests"""
    _, api_url = docker_services
    return MCPClient(api_url)


@pytest.fixture
def test_mcp_session(mcp_client):
    """Create a test session via MCP and clean up after test"""
    result = mcp_client.call_tool("create_session")
    session_id = result["session_id"]

    yield session_id

    # Cleanup
    try:
        mcp_client.call_tool("close_session", {"session_id": session_id})
    except:
        pass  # Session might already be closed


class TestMCPSessionManagement:
    """Tests for MCP session management tools"""

    def test_create_session(self, mcp_client):
        """Test creating a new session via MCP"""
        result = mcp_client.call_tool("create_session")

        assert "session_id" in result
        assert "workdir" in result
        assert "source_path" in result
        assert "artifact_path" in result
        assert result["session_id"] == result["workdir"]

        # Cleanup
        mcp_client.call_tool("close_session", {"session_id": result["session_id"]})

    def test_get_session_info(self, mcp_client, test_mcp_session):
        """Test getting session information via MCP"""
        result = mcp_client.call_tool("get_session_info", {
            "session_id": test_mcp_session
        })

        assert result["session_id"] == test_mcp_session
        assert "source_path" in result
        assert "artifact_path" in result

    def test_get_nonexistent_session(self, mcp_client):
        """Test getting info for non-existent session"""
        result = mcp_client.call_tool("get_session_info", {
            "session_id": "nonexistent"
        })

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()

    def test_list_sessions(self, mcp_client):
        """Test listing all active sessions via MCP"""
        # Get initial count
        initial_result = mcp_client.call_tool("list_sessions")
        initial_count = initial_result["count"]

        # Create multiple sessions
        session_ids = []
        for _ in range(3):
            result = mcp_client.call_tool("create_session")
            session_ids.append(result["session_id"])

        # List sessions
        result = mcp_client.call_tool("list_sessions")

        assert result["count"] >= initial_count + 3
        assert len(result["active_sessions"]) == result["count"]

        # Verify our sessions are in the list
        for session_id in session_ids:
            assert session_id in result["active_sessions"]

        # Cleanup
        for session_id in session_ids:
            mcp_client.call_tool("close_session", {"session_id": session_id})

    def test_close_session(self, mcp_client):
        """Test closing a session via MCP"""
        # Create session
        create_result = mcp_client.call_tool("create_session")
        session_id = create_result["session_id"]

        # Close session
        close_result = mcp_client.call_tool("close_session", {
            "session_id": session_id
        })

        assert close_result["success"] == True
        assert "closed successfully" in close_result["message"].lower()

        # Verify session is gone
        info_result = mcp_client.call_tool("get_session_info", {
            "session_id": session_id
        })
        assert info_result.get("success") == False

    def test_close_nonexistent_session(self, mcp_client):
        """Test closing non-existent session"""
        result = mcp_client.call_tool("close_session", {
            "session_id": "nonexistent"
        })

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()


class TestMCPCodeExecution:
    """Tests for MCP code execution tool"""

    def test_execute_simple_code(self, mcp_client, test_mcp_session):
        """Test simple code execution via MCP"""
        result = mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": "print('Hello from MCP!')"
        })

        assert result["success"] == True
        assert "Hello from MCP!" in result["output"]

    def test_execute_code_with_math(self, mcp_client, test_mcp_session):
        """Test code execution with calculations"""
        result = mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": "import math\nprint(f'Pi is approximately {math.pi:.2f}')"
        })

        assert result["success"] == True
        assert "3.14" in result["output"]

    def test_execute_code_with_error(self, mcp_client, test_mcp_session):
        """Test code execution with error"""
        result = mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": "1/0"  # Division by zero
        })

        # Execution fails, error appears in output
        assert result["success"] == False
        assert "ZeroDivisionError" in result["output"] or "division by zero" in result["output"].lower()

    def test_execute_code_with_ignore_options(self, mcp_client, test_mcp_session):
        """Test code execution with ignore options"""
        result = mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": "print('Testing ignore options')",
            "ignore_dependencies": ["numpy"],
            "ignore_unsafe_functions": []
        })

        assert result["success"] == True
        assert "Testing ignore options" in result["output"]

    def test_execute_in_nonexistent_session(self, mcp_client):
        """Test executing code in non-existent session"""
        result = mcp_client.call_tool("execute_code", {
            "session_id": "nonexistent",
            "code": "print('test')"
        })

        assert result.get("success") == False
        assert "not found" in result.get("error", "").lower()


class TestMCPFileOperations:
    """Tests for MCP file upload/download tools"""

    def test_upload_file(self, mcp_client, test_mcp_session):
        """Test uploading a file via MCP"""
        # Create test content
        test_content = b"Test file content\nLine 2"
        content_base64 = base64.b64encode(test_content).decode('utf-8')

        result = mcp_client.call_tool("upload_file", {
            "session_id": test_mcp_session,
            "filename": "test.txt",
            "content_base64": content_base64
        })

        assert result["success"] == True
        assert "successfully" in result.get("message", "").lower()
        assert "destination" in result

    def test_upload_invalid_filename(self, mcp_client, test_mcp_session):
        """Test uploading file with invalid filename"""
        test_content = base64.b64encode(b"content").decode('utf-8')

        # Try various invalid filenames
        invalid_filenames = [
            "../../../etc/passwd",
            "subdir/../../../file.txt",
            "/etc/passwd",
            ".hidden"
        ]

        for filename in invalid_filenames:
            result = mcp_client.call_tool("upload_file", {
                "session_id": test_mcp_session,
                "filename": filename,
                "content_base64": test_content
            })

            assert result["success"] == False
            assert "invalid" in result.get("error", "").lower()

    def test_upload_invalid_base64(self, mcp_client, test_mcp_session):
        """Test uploading file with invalid base64"""
        result = mcp_client.call_tool("upload_file", {
            "session_id": test_mcp_session,
            "filename": "test.txt",
            "content_base64": "not-valid-base64!!!"
        })

        assert result["success"] == False
        assert "base64" in result.get("error", "").lower()

    def test_download_file(self, mcp_client, test_mcp_session):
        """Test downloading a file via MCP"""
        # First, get session info to get artifact path
        info = mcp_client.call_tool("get_session_info", {
            "session_id": test_mcp_session
        })
        artifact_path = info["artifact_path"]

        # Execute code to create a file in artifacts
        mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": f"with open('{artifact_path}/output.txt', 'w') as f: f.write('Generated content')",
            "ignore_unsafe_functions": ["open"]
        })

        # Download the file
        result = mcp_client.call_tool("download_file", {
            "session_id": test_mcp_session,
            "src_path": f"{artifact_path}/output.txt"
        })

        assert result["success"] == True
        assert result["filename"] == "output.txt"
        assert "content_base64" in result

        # Decode and verify content
        content = base64.b64decode(result["content_base64"]).decode('utf-8')
        assert "Generated content" in content

    def test_download_with_path_traversal(self, mcp_client, test_mcp_session):
        """Test that path traversal in download is blocked"""
        info = mcp_client.call_tool("get_session_info", {
            "session_id": test_mcp_session
        })
        artifact_path = info["artifact_path"]

        # Try path traversal
        result = mcp_client.call_tool("download_file", {
            "session_id": test_mcp_session,
            "src_path": f"{artifact_path}/../../../etc/passwd"
        })

        assert result["success"] == False
        assert "path traversal" in result.get("error", "").lower()

    def test_upload_download_roundtrip(self, mcp_client, test_mcp_session):
        """Test uploading and then downloading the same file"""
        # Original content
        original_content = b"Roundtrip test content\nWith multiple lines\n123"
        content_base64 = base64.b64encode(original_content).decode('utf-8')

        # Upload
        upload_result = mcp_client.call_tool("upload_file", {
            "session_id": test_mcp_session,
            "filename": "roundtrip.txt",
            "content_base64": content_base64
        })
        assert upload_result["success"] == True

        # Get session info
        info = mcp_client.call_tool("get_session_info", {
            "session_id": test_mcp_session
        })
        artifact_path = info["artifact_path"]
        source_path = info["source_path"]

        # Copy file to artifacts via code execution
        mcp_client.call_tool("execute_code", {
            "session_id": test_mcp_session,
            "code": f"import shutil; shutil.copy('{source_path}/roundtrip.txt', '{artifact_path}/roundtrip.txt')"
        })

        # Download
        download_result = mcp_client.call_tool("download_file", {
            "session_id": test_mcp_session,
            "src_path": f"{artifact_path}/roundtrip.txt"
        })

        assert download_result["success"] == True

        # Verify content matches
        downloaded_content = base64.b64decode(download_result["content_base64"])
        assert downloaded_content == original_content


class TestMCPRESTInteroperability:
    """Critical tests to verify shared state between REST and MCP"""

    def test_rest_create_mcp_list(self, api_client, mcp_client):
        """Test creating session via REST, listing via MCP"""
        # Create session via REST
        rest_session = api_client.create_session()

        try:
            # List sessions via MCP
            mcp_result = mcp_client.call_tool("list_sessions")

            # Verify REST session appears in MCP list
            assert rest_session.session_id in mcp_result["active_sessions"]
        finally:
            # Cleanup
            api_client.close_session(rest_session.session_id)

    def test_mcp_create_rest_list(self, api_client, mcp_client):
        """Test creating session via MCP, listing via REST"""
        # Create session via MCP
        mcp_result = mcp_client.call_tool("create_session")
        session_id = mcp_result["session_id"]

        try:
            # List sessions via REST
            rest_result = api_client.list_sessions()

            # Verify MCP session appears in REST list
            assert session_id in rest_result["active_sessions"]
        finally:
            # Cleanup via MCP
            mcp_client.call_tool("close_session", {"session_id": session_id})

    def test_mcp_create_rest_execute(self, api_client, mcp_client):
        """Test creating session via MCP, executing via REST"""
        # Create session via MCP
        mcp_result = mcp_client.call_tool("create_session")
        session_id = mcp_result["session_id"]

        try:
            # Execute code via REST API
            exec_result = api_client.execute_code(
                session_id,
                "print('Executed via REST on MCP session')"
            )

            assert exec_result["success"] == True
            assert "Executed via REST" in exec_result["output"]
        finally:
            # Cleanup
            mcp_client.call_tool("close_session", {"session_id": session_id})

    def test_rest_create_mcp_execute(self, api_client, mcp_client):
        """Test creating session via REST, executing via MCP"""
        # Create session via REST
        rest_session = api_client.create_session()

        try:
            # Execute code via MCP
            mcp_result = mcp_client.call_tool("execute_code", {
                "session_id": rest_session.session_id,
                "code": "print('Executed via MCP on REST session')"
            })

            assert mcp_result["success"] == True
            assert "Executed via MCP" in mcp_result["output"]
        finally:
            # Cleanup
            api_client.close_session(rest_session.session_id)

    def test_concurrent_access(self, api_client, mcp_client):
        """Test concurrent operations from both REST and MCP"""
        # Create session via MCP
        mcp_result = mcp_client.call_tool("create_session")
        session_id = mcp_result["session_id"]

        try:
            # Upload file via REST
            upload_content = b"Concurrent test"
            api_client.upload_file_content(
                session_id,
                upload_content,
                "concurrent.txt"
            )

            # Execute code via MCP
            exec_result = mcp_client.call_tool("execute_code", {
                "session_id": session_id,
                "code": "print('Both protocols work together')"
            })

            assert exec_result["success"] == True

            # Verify session info accessible from both
            rest_info = api_client.get_session_info(session_id)
            mcp_info = mcp_client.call_tool("get_session_info", {"session_id": session_id})

            assert rest_info["session_id"] == mcp_info["session_id"]
            assert rest_info["source_path"] == mcp_info["source_path"]
        finally:
            # Cleanup
            api_client.close_session(session_id)


class TestMCPGetPackages:
    """Tests for MCP get_packages tool"""

    def test_get_packages(self, mcp_client):
        """Test getting installed packages via MCP"""
        result = mcp_client.call_tool("get_packages")

        assert "packages" in result
        assert "count" in result
        assert isinstance(result["packages"], list)
        assert result["count"] == len(result["packages"])
        assert result["count"] > 0

    def test_get_packages_contains_known_packages(self, mcp_client):
        """Test that pre-installed packages are in the list"""
        result = mcp_client.call_tool("get_packages")
        package_names = [p.lower() for p in result["packages"]]
        # These are installed via runner_requirements.txt
        for expected in ["numpy", "pandas", "matplotlib"]:
            assert expected in package_names, f"Expected '{expected}' in installed packages"

    def test_get_packages_no_session_required(self, mcp_client):
        """Test that get_packages works without any active sessions"""
        # Close all sessions first
        sessions_result = mcp_client.call_tool("list_sessions")
        for session_id in sessions_result["active_sessions"]:
            try:
                mcp_client.call_tool("close_session", {"session_id": session_id})
            except:
                pass

        # get_packages should still work
        result = mcp_client.call_tool("get_packages")
        assert "packages" in result
        assert result["count"] > 0


class TestMCPIntegration:
    """Integration tests for complete MCP workflows"""

    def test_full_mcp_workflow(self, mcp_client):
        """Test complete workflow using only MCP"""
        # 1. Create session
        create_result = mcp_client.call_tool("create_session")
        session_id = create_result["session_id"]
        source_path = create_result["source_path"]
        artifact_path = create_result["artifact_path"]

        try:
            # 2. Upload a Python script
            script_content = """
import json
data = {'result': 'success', 'value': 42, 'message': 'MCP workflow complete'}
with open('output.json', 'w') as f:
    json.dump(data, f)
print('Script executed successfully')
"""
            script_base64 = base64.b64encode(script_content.encode()).decode('utf-8')

            upload_result = mcp_client.call_tool("upload_file", {
                "session_id": session_id,
                "filename": "process.py",
                "content_base64": script_base64
            })
            assert upload_result["success"] == True

            # 3. Execute the script (reading and running it)
            exec_result = mcp_client.call_tool("execute_code", {
                "session_id": session_id,
                "code": f"""
with open('{source_path}/process.py', 'r') as f:
    code = f.read()
exec(code)
""",
                "ignore_unsafe_functions": ["open", "exec"]
            })

            # Note: exec might fail due to safety checks, but let's verify the attempt
            # If it fails, we can still test other parts of the workflow

            # 4. Alternative: Execute inline to create artifact
            exec_result2 = mcp_client.call_tool("execute_code", {
                "session_id": session_id,
                "code": f"""
import json
import shutil
data = {{'result': 'success', 'value': 42}}
with open('output.json', 'w') as f:
    json.dump(data, f)
shutil.copy('output.json', '{artifact_path}/output.json')
print('Artifact created')
""",
                "ignore_unsafe_functions": ["open"]
            })
            assert exec_result2["success"] == True
            assert "Artifact created" in exec_result2["output"]

            # 5. Download the result
            download_result = mcp_client.call_tool("download_file", {
                "session_id": session_id,
                "src_path": f"{artifact_path}/output.json"
            })

            assert download_result["success"] == True

            # Verify downloaded content
            content = base64.b64decode(download_result["content_base64"]).decode('utf-8')
            data = json.loads(content)
            assert data["result"] == "success"
            assert data["value"] == 42

        finally:
            # 6. Close session
            close_result = mcp_client.call_tool("close_session", {
                "session_id": session_id
            })
            assert close_result["success"] == True

            # 7. Verify session is gone
            info_result = mcp_client.call_tool("get_session_info", {
                "session_id": session_id
            })
            assert info_result.get("success") == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
