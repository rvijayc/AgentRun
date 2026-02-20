"""MCP Client for AgentRun

Provides a client implementation for communicating with AgentRun via the
Model Context Protocol (MCP). This client maintains API compatibility with
AgentRunAPIClient, allowing drop-in replacement.

Example:
    from agentrun_plus import AgentRunMCPClient

    client = AgentRunMCPClient("http://localhost:8000")
    health = client.get_health()
    session = client.create_session()
    result = client.execute_code(session.session_id, "print('Hello')")
"""

import os
import base64
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import requests

# Import SessionInfo from api.py for compatibility
from agentrun_plus.api.api import SessionInfo


class AgentRunMCPClient:
    """Client for AgentRun MCP endpoint with REST-compatible interface

    This client communicates with AgentRun using the Model Context Protocol (MCP)
    over HTTP with JSON-RPC 2.0. It provides the same interface as AgentRunAPIClient
    for seamless migration.

    Attributes:
        base_url: Base URL of the AgentRun server
        mcp_url: Full URL to the MCP endpoint
        session: Requests session for connection pooling
        mcp_session_id: MCP protocol session ID (from initialize handshake)
        request_id: Counter for JSON-RPC request IDs
        debug: Enable debug logging
    """

    def __init__(self, base_url: str):
        """Initialize MCP client

        Args:
            base_url: Base URL of AgentRun server (e.g., "http://localhost:8000")
        """
        self.base_url = base_url.rstrip('/')
        self.mcp_url = f"{self.base_url}/mcp"
        self.session = requests.Session()
        self.mcp_session_id: Optional[str] = None
        self.request_id = 0
        self.debug = os.getenv("AGENTRUN_DEBUG", "false").lower() == "true"

        # Initialize MCP session on creation
        self._initialize()

    def _get_next_id(self) -> int:
        """Get next JSON-RPC request ID"""
        self.request_id += 1
        return self.request_id

    def _parse_sse_response(self, response_text: str) -> dict:
        """Parse Server-Sent Events (SSE) response

        Args:
            response_text: Raw SSE response text

        Returns:
            Parsed JSON from SSE data field

        Raises:
            Exception: If no data found in SSE response
        """
        # SSE format: "event: message\r\ndata: {json}\r\n\r\n"
        lines = response_text.strip().split('\n')
        for line in lines:
            if line.startswith('data: '):
                json_data = line[6:]  # Remove 'data: ' prefix
                return json.loads(json_data)
        raise Exception(f"No data found in SSE response: {response_text[:200]}")

    def _initialize(self):
        """Initialize MCP session with protocol handshake

        Sends initialize request and stores MCP session ID from response header.
        This is required before making any tool calls.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agentrun-mcp-client",
                    "version": "0.4.0"
                }
            }
        }

        if self.debug:
            print(f"[DEBUG] MCP Initialize: {self.mcp_url}")
            print(f"[DEBUG] Payload: {json.dumps(payload, indent=2)}")

        response = self.session.post(
            self.mcp_url,
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
            self.mcp_session_id = response.headers['mcp-session-id']
        elif 'Mcp-Session-Id' in response.headers:
            self.mcp_session_id = response.headers['Mcp-Session-Id']

        if self.debug:
            print(f"[DEBUG] MCP Session ID: {self.mcp_session_id}")

        # Parse SSE response
        result = self._parse_sse_response(response.text)

        if self.debug:
            print(f"[DEBUG] Initialize result: {json.dumps(result, indent=2)}")

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """Call an MCP tool using JSON-RPC 2.0

        Args:
            tool_name: Name of the MCP tool to call
            arguments: Dictionary of arguments for the tool

        Returns:
            Result dictionary from the tool

        Raises:
            Exception: If tool call fails or returns error
        """
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
        if self.mcp_session_id:
            headers["Mcp-Session-Id"] = self.mcp_session_id

        if self.debug:
            print(f"[DEBUG] Calling tool: {tool_name}")
            print(f"[DEBUG] Arguments: {json.dumps(arguments, indent=2)}")

        response = self.session.post(
            self.mcp_url,
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
                            parsed = json.loads(item["text"])
                            if self.debug:
                                print(f"[DEBUG] Tool result: {json.dumps(parsed, indent=2)}")
                            return parsed
                        except:
                            if self.debug:
                                print(f"[DEBUG] Tool result (raw): {item['text']}")
                            return {"output": item["text"]}
            return result["result"]

        return result

    # Public API methods (compatible with AgentRunAPIClient)

    def get_health(self) -> dict:
        """Get health status

        Returns:
            dict: {"status": "healthy"|"unhealthy", "active_sessions": int}
        """
        try:
            return self._call_tool("get_health")
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }

    def create_session(self) -> SessionInfo:
        """Create a new AgentRun session

        Returns:
            SessionInfo: Session information with session_id, workdir, paths,
                         upload_url, and artifacts_url
        """
        result = self._call_tool("create_session")
        return SessionInfo(
            session_id=result["session_id"],
            workdir=result["workdir"],
            source_path=result["source_path"],
            artifact_path=result["artifact_path"],
            upload_url=result.get("upload_url", ""),
            artifacts_url=result.get("artifacts_url", ""),
        )

    def get_session_info(self, session_id: str) -> dict:
        """Get session information

        Args:
            session_id: The session ID

        Returns:
            dict: Session information
        """
        return self._call_tool("get_session_info", {"session_id": session_id})

    def close_session(self, session_id: str) -> dict:
        """Close a session

        Args:
            session_id: The session ID to close

        Returns:
            dict: {"success": bool, "message": str}
        """
        return self._call_tool("close_session", {"session_id": session_id})

    def list_sessions(self) -> dict:
        """List all active sessions

        Returns:
            dict: {"active_sessions": List[str], "count": int}
        """
        return self._call_tool("list_sessions")

    def get_packages(self) -> dict:
        """Get the list of installed Python packages in the runner container

        Returns:
            dict: {"packages": List[str], "count": int}
        """
        return self._call_tool("get_packages")

    def execute_code(self,
                     session_id: str,
                     python_code: str,
                     ignore_dependencies: Optional[List[str]] = None,
                     ignore_unsafe_functions: Optional[List[str]] = None) -> dict:
        """Execute Python code in a session

        Args:
            session_id: The session ID
            python_code: Python code to execute
            ignore_dependencies: List of dependencies to ignore
            ignore_unsafe_functions: List of unsafe functions to allow

        Returns:
            dict: {"success": bool, "output": str}
        """
        return self._call_tool("execute_code", {
            "session_id": session_id,
            "code": python_code,
            "ignore_dependencies": ignore_dependencies,
            "ignore_unsafe_functions": ignore_unsafe_functions
        })

    def upload_file(self, session_id: str, file_path: str, filename: Optional[str] = None) -> dict:
        """Upload a file to a session from file path

        Args:
            session_id: The session ID
            file_path: Path to file to upload
            filename: Optional filename (defaults to basename of file_path)

        Returns:
            dict: {"message": str, "destination_path": str}
        """
        if filename is None:
            filename = os.path.basename(file_path)

        # Read and encode file
        with open(file_path, 'rb') as f:
            content = f.read()

        return self.upload_file_content(session_id, content, filename)

    def upload_file_content(self, session_id: str, content: bytes, filename: str) -> dict:
        """Upload file content to a session

        Args:
            session_id: The session ID
            content: File content as bytes
            filename: Name for the file

        Returns:
            dict: {"message": str, "destination_path": str}
        """
        # Encode content as base64
        content_base64 = base64.b64encode(content).decode('utf-8')

        result = self._call_tool("upload_file", {
            "session_id": session_id,
            "filename": filename,
            "content_base64": content_base64
        })

        return result

    def list_artifacts(self, session_id: str) -> dict:
        """List files in a session's artifacts/ directory with download URLs and sizes.

        Args:
            session_id: The session ID

        Returns:
            dict: {
                "artifacts": [{"name": str, "size_bytes": int, "download_url": str}, ...],
                "count": int,
                "artifacts_url": str
            }
        """
        return self._call_tool("list_artifacts", {"session_id": session_id})

    def list_src(self, session_id: str) -> dict:
        """List files in a session's src/ directory (uploaded input files).

        Args:
            session_id: The session ID

        Returns:
            dict: {"files": [{"name": str, "size_bytes": int}, ...], "count": int}
        """
        return self._call_tool("list_src", {"session_id": session_id})

    def download_file(self, session_id: str, src_path: str, dest_path: str,
                     filename: Optional[str] = None) -> str:
        """Download a file from a session

        Args:
            session_id: The session ID
            src_path: Source path in session (relative to artifact directory)
            dest_path: Destination directory on local filesystem
            filename: Optional filename (defaults to basename of src_path)

        Returns:
            str: Full path to downloaded file
        """
        if filename is None:
            filename = os.path.basename(src_path)

        # Call MCP tool
        result = self._call_tool("download_file", {
            "session_id": session_id,
            "src_path": src_path
        })

        # Decode base64 content
        content = base64.b64decode(result["content_base64"])

        # Write to destination
        full_path = os.path.join(dest_path, filename)
        with open(full_path, 'wb') as f:
            f.write(content)

        return full_path
