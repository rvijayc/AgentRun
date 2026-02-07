"""MCP Server for AgentRun - Exposes sessions as MCP tools

This module creates an MCP (Model Context Protocol) server that exposes
AgentRun's session management and code execution capabilities as tools.
The MCP server shares state with the REST API through the same backend
and sessions dictionary.
"""

from typing import Dict, Optional, List
from fastmcp import FastMCP
from backend import AgentRun, AgentRunSession
import base64
import tempfile
import os
from pathlib import Path
from uuid import uuid4
import logging
import sys

# Create logger
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
log.addHandler(stream_handler)


def create_mcp_app(backend: AgentRun, sessions: Dict[str, AgentRunSession]):
    """Create MCP app that shares state with REST API

    Args:
        backend: Shared AgentRun backend instance
        sessions: Shared sessions dictionary (same as REST API)

    Returns:
        ASGI application for mounting to FastAPI
    """

    # Create FastMCP instance
    # In FastMCP 2.x, only the name parameter is required
    mcp = FastMCP("AgentRun")

    # Tool 1: Create Session
    @mcp.tool()
    def create_session() -> dict:
        """Create a new AgentRun session for code execution

        Returns a session_id that should be used for all subsequent operations
        in this session. Sessions are isolated with their own working directories.

        Returns:
            dict: {
                "session_id": str,
                "workdir": str,
                "source_path": str,
                "artifact_path": str
            }
        """
        try:
            # Generate unique session ID
            session_id = uuid4().hex
            workdir = session_id

            log.info(f'[MCP] Creating session {session_id}...')

            # Create session using backend
            session = backend.create_session(workdir=workdir)

            # Store session reference (shared with REST API)
            sessions[session_id] = session

            return {
                "session_id": session_id,
                "workdir": workdir,
                "source_path": session.source_path(),
                "artifact_path": session.artifact_path()
            }
        except Exception as e:
            log.error(f'[MCP] Failed to create session: {e}')
            return {
                "success": False,
                "error": f"Failed to create session: {str(e)}"
            }

    # Tool 2: Execute Code
    @mcp.tool()
    def execute_code(
        session_id: str,
        code: str,
        ignore_dependencies: Optional[List[str]] = None,
        ignore_unsafe_functions: Optional[List[str]] = None
    ) -> dict:
        """Execute Python code in a session's isolated environment

        The code is executed in the session's working directory with safety
        checks and dependency management. Python errors will be captured
        and returned in the output field.

        Args:
            session_id: The session ID returned from create_session
            code: Python code to execute
            ignore_dependencies: List of dependencies to ignore during validation
            ignore_unsafe_functions: List of unsafe functions to allow (e.g., ['open'])

        Returns:
            dict: {
                "success": bool,  # True if code executed without errors
                "output": str     # Code output or error message
            }
        """
        # Validate session exists
        if session_id not in sessions:
            return {
                "success": False,
                "output": "",
                "error": f"Session {session_id} not found"
            }

        try:
            session = sessions[session_id]

            # Execute code using session
            success, output = session.execute_code(
                python_code=code,
                ignore_dependencies=ignore_dependencies,
                ignore_unsafe_functions=ignore_unsafe_functions
            )

            log.info(f'[MCP] Code executed in session {session_id}: success={success}')

            return {
                "success": success,
                "output": output
            }
        except Exception as e:
            log.error(f'[MCP] Error executing code in session {session_id}: {e}')
            return {
                "success": False,
                "output": str(e),
                "error": str(e)
            }

    # Tool 3: Upload File
    @mcp.tool()
    def upload_file(
        session_id: str,
        filename: str,
        content_base64: str
    ) -> dict:
        """Upload a file to a session's source directory

        Files are uploaded to the session's source directory where they can
        be accessed by executed code. The file content must be base64 encoded.

        Args:
            session_id: The session ID
            filename: Name of the file (no path separators allowed)
            content_base64: Base64-encoded file content

        Returns:
            dict: {
                "success": bool,
                "destination": str,  # Full path where file was saved
                "message": str
            }
        """
        # Validate session exists
        if session_id not in sessions:
            return {
                "success": False,
                "error": f"Session {session_id} not found"
            }

        # Security check: Validate filename (no path traversal)
        if ".." in filename or "/" in filename or "\\" in filename:
            return {
                "success": False,
                "error": "Invalid filename: path separators and traversal attempts are not allowed"
            }

        # Additional validation: ensure filename is safe
        if not filename or filename.startswith('.'):
            return {
                "success": False,
                "error": "Invalid filename: cannot be empty or start with a dot"
            }

        try:
            session = sessions[session_id]

            # Decode base64 content
            try:
                content = base64.b64decode(content_base64)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to decode base64 content: {str(e)}"
                }

            # Create temporary file to hold the content
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                try:
                    # Write decoded content to temp file
                    tmp_file.write(content)
                    tmp_file.flush()

                    # Copy file to session using backend API
                    destination = session.copy_file_to(
                        local_path=tmp_file.name,
                        dest_file_name=filename
                    )

                    # Verify destination is within source path
                    source_path = Path(session.source_path()).resolve()
                    dest_path = Path(destination).resolve()

                    try:
                        dest_path.relative_to(source_path)
                    except ValueError:
                        return {
                            "success": False,
                            "error": "Internal error: file was not placed in the correct directory"
                        }

                    log.info(f'[MCP] Uploaded file {filename} to session {session_id}')

                    return {
                        "success": True,
                        "destination": destination,
                        "message": f"File '{filename}' uploaded successfully"
                    }
                finally:
                    # Clean up temporary file
                    os.unlink(tmp_file.name)

        except Exception as e:
            log.error(f'[MCP] Failed to upload file to session {session_id}: {e}')
            return {
                "success": False,
                "error": f"Failed to upload file: {str(e)}"
            }

    # Tool 4: Download File
    @mcp.tool()
    def download_file(
        session_id: str,
        src_path: str
    ) -> dict:
        """Download a file from a session's artifact directory

        Files can only be downloaded from the session's artifact directory
        for security reasons. The file content is returned base64-encoded.

        Args:
            session_id: The session ID
            src_path: Path to file within artifact directory (can be relative or absolute)

        Returns:
            dict: {
                "success": bool,
                "filename": str,
                "content_base64": str,
                "size_bytes": int
            }
        """
        # Validate session exists
        if session_id not in sessions:
            return {
                "success": False,
                "error": f"Session {session_id} not found"
            }

        session = sessions[session_id]

        # Security check: Ensure requested path is within artifact directory
        artifact_path = Path(session.artifact_path()).resolve()
        requested_path = Path(src_path)

        # Check for path traversal attempts
        if ".." in src_path:
            return {
                "success": False,
                "error": f"Path traversal attempts are not allowed: {src_path}"
            }

        # Validate path is within artifact directory
        try:
            if requested_path.is_absolute():
                requested_path.relative_to(artifact_path)
        except ValueError:
            return {
                "success": False,
                "error": f"Access denied: path must be within session's artifact directory"
            }

        try:
            # Create temporary directory for download
            with tempfile.TemporaryDirectory() as tmp_dir:
                # Copy file from session to temp directory
                session.copy_file_from(
                    src_path=src_path,
                    local_dest_path=tmp_dir
                )

                # Get the filename
                src_filename = os.path.basename(src_path)
                downloaded_file_path = os.path.join(tmp_dir, src_filename)

                # Check if file exists
                if not os.path.exists(downloaded_file_path):
                    return {
                        "success": False,
                        "error": "File was not found after copy operation"
                    }

                # Read file and encode as base64
                with open(downloaded_file_path, 'rb') as f:
                    file_content = f.read()

                content_base64 = base64.b64encode(file_content).decode('utf-8')

                log.info(f'[MCP] Downloaded file {src_filename} from session {session_id}')

                return {
                    "success": True,
                    "filename": src_filename,
                    "content_base64": content_base64,
                    "size_bytes": len(file_content)
                }

        except Exception as e:
            log.error(f'[MCP] Failed to download file from session {session_id}: {e}')
            return {
                "success": False,
                "error": f"Failed to download file: {str(e)}"
            }

    # Tool 5: List Sessions
    @mcp.tool()
    def list_sessions() -> dict:
        """List all active session IDs

        Returns a list of all currently active sessions (created via REST API
        or MCP). Sessions are shared between both protocols.

        Returns:
            dict: {
                "session_ids": List[str],
                "count": int
            }
        """
        session_ids = list(sessions.keys())
        log.info(f'[MCP] Listed {len(session_ids)} active sessions')

        return {
            "session_ids": session_ids,
            "count": len(session_ids)
        }

    # Tool 6: Get Session Info
    @mcp.tool()
    def get_session_info(session_id: str) -> dict:
        """Get information about a session

        Returns the session's working directory paths (source and artifact).

        Args:
            session_id: The session ID

        Returns:
            dict: {
                "session_id": str,
                "source_path": str,
                "artifact_path": str
            }
        """
        # Validate session exists
        if session_id not in sessions:
            return {
                "success": False,
                "error": f"Session {session_id} not found"
            }

        session = sessions[session_id]

        return {
            "session_id": session_id,
            "source_path": session.source_path(),
            "artifact_path": session.artifact_path()
        }

    # Tool 7: Close Session
    @mcp.tool()
    def close_session(session_id: str) -> dict:
        """Close a session and clean up resources

        Closes the session and removes all associated files. The session_id
        will no longer be valid after this operation.

        Args:
            session_id: The session ID to close

        Returns:
            dict: {
                "success": bool,
                "message": str
            }
        """
        # Validate session exists
        if session_id not in sessions:
            return {
                "success": False,
                "error": f"Session {session_id} not found"
            }

        try:
            session = sessions[session_id]
            backend.close_session(session)
            del sessions[session_id]

            log.info(f'[MCP] Closed session {session_id}')

            return {
                "success": True,
                "message": f"Session {session_id} closed successfully"
            }
        except Exception as e:
            log.error(f'[MCP] Failed to close session {session_id}: {e}')
            return {
                "success": False,
                "error": f"Failed to close session: {str(e)}"
            }

    # Return the ASGI app for mounting
    # In FastMCP 2.x, use http_app() to get the ASGI application
    return mcp.http_app(path='/')
