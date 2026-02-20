from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import Response
from typing import Dict
from uuid import uuid4
import mimetypes
import os
import requests
import tempfile
from pathlib import Path
import logging
import sys

# Base URL used to construct self-referencing REST URLs returned to callers.
# Set AGENTRUN_BASE_URL in the environment when the server is reachable at a
# non-default address (e.g. a public hostname or a different port).
#
# Docker Compose example (docker-compose.base.yml under api: environment:):
#   - AGENTRUN_BASE_URL=http://192.168.1.100:8000
#
# Kubernetes example (k8s/configmap.yaml under data:):
#   AGENTRUN_BASE_URL: "https://agentrun.example.com"
#   (then reference it as an env var in api-deployment.yaml)
AGENTRUN_BASE_URL = os.environ.get("AGENTRUN_BASE_URL", "http://localhost:8000").rstrip("/")

# Import the backend classes (assuming they're available)
from backend import AgentRun, AgentRunSession
from api import (
        SessionCreateResponse,
        ExecuteCodeRequest,
        ExecuteCodeResponse,
        CopyFileToResponse,
        CopyFileFromRequest,
        SessionInfoResponse
)
from mcp_server import create_mcp_app

# Initialize the backend (before lifespan and MCP app creation)
backend = AgentRun(container_url='http://python-runner:5000')

# Store active sessions (before lifespan)
sessions: Dict[str, AgentRunSession] = {}

# Create MCP app (before lifespan - we need mcp_app.lifespan)
mcp_app = create_mcp_app(backend, sessions, base_url=AGENTRUN_BASE_URL)

# For now, use MCP app's lifespan directly
# TODO: Combine with API cleanup logic
# Initialize FastAPI app with MCP lifespan
app = FastAPI(
    title="AgentRun API",
    version="1.0.0",
    lifespan=mcp_app.lifespan
)

# Mount MCP app (shares backend and sessions with REST API)
app.mount("/mcp", mcp_app)

# Create a logger for app specific messages.
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
# Only add handler if none exists (prevents duplicates on module reload)
if not log.handlers:
    stream_handler = logging.StreamHandler(sys.stdout)
    log.addHandler(stream_handler)

# -------------------------------------
# REST API Endpoints
# -------------------------------------

@app.get("/")
def root():
    """Root endpoint with API information"""
    return {
        "service": "AgentRun API",
        "version": "1.0.0",
        "protocols": {
            "REST": "Traditional REST API endpoints",
            "MCP": "Model Context Protocol server at /mcp"
        },
        "endpoints": {
            "POST /sessions": "Create a new session",
            "GET /sessions/{session_id}": "Get session information",
            "DELETE /sessions/{session_id}": "Close a session",
            "POST /sessions/{session_id}/execute": "Execute Python code",
            "POST /sessions/{session_id}/copy-to": "Upload a file to session src/ (multipart/form-data, field: 'file')",
            "POST /sessions/{session_id}/copy-from": "Download a file from session artifacts/ (JSON body)",
            "GET /sessions/{session_id}/artifacts/{filename}": "Download an artifact file directly (curl-friendly)",
            "GET /packages": "Get installed Python packages",
            "MCP /mcp": "MCP server endpoint (Streamable HTTP transport)"
        }
    }

@app.post("/sessions", response_model=SessionCreateResponse)
def create_session():
    """Create a new session with a unique working directory"""
    # Generate unique session ID and workdir
    session_id = uuid4().hex
    workdir = session_id  # Using same value for both
    log.info(f'Creating session {session_id} ...')
    
    try:
        # Create session using backend
        session = backend.create_session(workdir=workdir)
        
        # Store session reference
        sessions[session_id] = session
        
        return SessionCreateResponse(
            session_id=session_id,
            workdir=workdir,
            source_path=session.source_path(),
            artifact_path=session.artifact_path(),
            upload_url=f"{AGENTRUN_BASE_URL}/sessions/{session_id}/copy-to",
            artifacts_url=f"{AGENTRUN_BASE_URL}/sessions/{session_id}/artifacts",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {str(e)}")

@app.get("/sessions/{session_id}", response_model=SessionInfoResponse)
def get_session_info(session_id: str):
    """Get information about an existing session"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    return SessionInfoResponse(
        session_id=session_id,
        source_path=session.source_path(),
        artifact_path=session.artifact_path()
    )

@app.delete("/sessions/{session_id}")
def close_session(session_id: str):
    """Close a session and clean up resources"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        session = sessions[session_id]
        backend.close_session(session)
        del sessions[session_id]
        return {"message": f"Session {session_id} closed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to close session: {str(e)}")

@app.post("/sessions/{session_id}/execute", response_model=ExecuteCodeResponse)
def execute_code(session_id: str, request: ExecuteCodeRequest):
    """Execute Python code in the session's working directory
    
    Note: This endpoint returns success=True if the code execution was initiated successfully,
    regardless of whether the Python code itself raises exceptions. Python errors/exceptions
    will appear in the output field.
    """
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    success, output = session.execute_code(
        python_code=request.python_code,
        ignore_dependencies=request.ignore_dependencies,
        ignore_unsafe_functions=request.ignore_unsafe_functions
    )
    log.info(str(output))
    return ExecuteCodeResponse(output=output, success=success)

@app.post("/sessions/{session_id}/copy-to", response_model=CopyFileToResponse)
async def copy_file_to_session(
    session_id: str,
    file: UploadFile = File(...)
):
    """Copy a file to the session's source directory"""
    if session_id not in sessions:
        for idx, k in enumerate(sessions.keys()):
            log.info(f'[{idx}] {k}')
        raise HTTPException(status_code=404, detail="Session {session_id} not found")
    
    session = sessions[session_id]
    
    # Security check: Validate the filename
    filename = file.filename
    if not isinstance(filename, str):
        raise HTTPException(status_code=500, detail=f'Invalid filename {filename}!')
    
    # Check for path traversal attempts in filename
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=403,
            detail="Access denied: Invalid filename. Path separators and traversal attempts are not allowed"
        )
    
    # Additional validation: ensure filename is safe
    if not filename or filename.startswith('.'):
        raise HTTPException(
            status_code=400,
            detail="Invalid filename: Filename cannot be empty or start with a dot"
        )
    
    # Create a temporary file to save the upload
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        try:
            # Save uploaded file to temporary location
            content = await file.read()
            tmp_file.write(content)
            tmp_file.flush()
            
            # Copy file to session using backend API
            # The backend should place it in the source directory
            destination = session.copy_file_to(
                    local_path=tmp_file.name,
                    dest_file_name=file.filename
            )
            
            # Verify the destination is within the source path
            source_path = Path(session.source_path()).resolve()
            dest_path = Path(destination).resolve()
            
            try:
                dest_path.relative_to(source_path)
            except ValueError:
                # This shouldn't happen if backend is implemented correctly,
                # but we check anyway for defense in depth
                raise HTTPException(
                    status_code=500,
                    detail="Internal error: File was not placed in the correct directory"
                )
            
            return CopyFileToResponse(
                message=f"File '{file.filename}' copied successfully",
                destination_path=destination
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to copy file: {str(e)}")
        finally:
            # Clean up temporary file
            os.unlink(tmp_file.name)

@app.post("/sessions/{session_id}/copy-from")
def copy_file_from_session(session_id: str, request: CopyFileFromRequest):
    """Copy a file from the session's artifact directory"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    # Security check: Ensure the requested path is within the artifact directory
    artifact_path = Path(session.artifact_path()).resolve()
    requested_path = Path(request.src_path)
    log.info(f'Artifact Base: {artifact_path}, Requested Path: {requested_path}')
    
    # Check if the requested path is a subdirectory of artifact_path
    try:
        # This will raise ValueError if requested_path is not relative to artifact_path
        if requested_path.is_absolute():
            requested_path.relative_to(artifact_path)
    except ValueError:
        # Path is outside the artifact directory
        msg = f"Access denied: Path {requested_path} must be within the session's artifact directory {artifact_path}"
        log.error(msg)
        raise HTTPException(
            status_code=403,
            detail=msg
        )
    
    # Additional check for path traversal attempts
    if ".." in request.src_path:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: Path traversal attempts {request.src_path} are not allowed"
        )
    
    # Create temporary directory for download
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            # Copy file from session to temporary directory
            # copy_file_from expects a directory, not a file path
            session.copy_file_from(
                src_path=request.src_path,
                local_dest_path=tmp_dir
            )
            
            # The file should now be in tmp_dir with its original name
            # Extract the filename from the source path
            src_filename = os.path.basename(request.src_path)
            downloaded_file_path = os.path.join(tmp_dir, src_filename)
            
            # Check if the file was successfully copied
            if not os.path.exists(downloaded_file_path):
                raise HTTPException(
                    status_code=500,
                    detail=f"File was not found after copy operation"
                )
            
            # Read the file content into memory before the temp directory is deleted
            with open(downloaded_file_path, 'rb') as f:
                file_content = f.read()
            
            # Return the file content as a response
            return Response(
                content=file_content,
                media_type='application/octet-stream',
                headers={
                    "Content-Disposition": f'attachment; filename="{request.filename}"'
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to copy file: {str(e)}")

@app.get("/sessions/{session_id}/artifacts")
def list_artifacts(session_id: str):
    """List files in a session's artifacts/ directory with download URLs and sizes."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = sessions[session_id]
    try:
        files = session.list_artifact_files()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list artifacts: {str(e)}")
    artifacts_url = f"{AGENTRUN_BASE_URL}/sessions/{session_id}/artifacts"
    return {
        "artifacts": [
            {**f, "download_url": f"{artifacts_url}/{f['name']}"}
            for f in files
        ],
        "count": len(files),
        "artifacts_url": artifacts_url,
    }


@app.get("/sessions/{session_id}/src")
def list_src(session_id: str):
    """List files in a session's src/ directory (uploaded input files)."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = sessions[session_id]
    try:
        files = session.list_src_files()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list src files: {str(e)}")
    return {"files": files, "count": len(files)}


@app.get("/sessions/{session_id}/artifacts/{filename}")
def download_artifact(session_id: str, filename: str):
    """Download a specific artifact file from a session's artifacts/ directory.

    This is a curl-friendly GET alternative to POST /copy-from.
    The URL is returned in the create_session response as 'artifacts_url'
    and in list_artifacts results as 'download_url'.

    Usage:
        curl http://server:8000/sessions/{session_id}/artifacts/plot.png -o plot.png

    Args:
        session_id: Session ID (from create_session)
        filename: Name of the file inside artifacts/ (no path separators allowed)
    """
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate filename: no path traversal, no directory separators
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename: path separators and traversal attempts are not allowed")
    if filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename: cannot start with a dot")

    session = sessions[session_id]

    # Defense-in-depth: verify the resolved path stays inside artifacts/
    artifact_dir = Path(session.artifact_path()).resolve()
    file_path = (artifact_dir / filename).resolve()
    try:
        file_path.relative_to(artifact_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path is outside the artifact directory")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            session.copy_file_from(
                src_path=f"artifacts/{filename}",
                local_dest_path=tmp_dir
            )
            downloaded_path = os.path.join(tmp_dir, filename)
            if not os.path.exists(downloaded_path):
                raise HTTPException(status_code=404, detail=f"File '{filename}' not found in artifacts")
            with open(downloaded_path, "rb") as f:
                file_content = f.read()
        except HTTPException:
            raise
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"File '{filename}' not found in artifacts")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve file: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to retrieve file: {str(e)}")

    content_type, _ = mimetypes.guess_type(filename)
    return Response(
        content=file_content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/packages")
def get_packages():
    """Get the list of Python packages installed in the runner container"""
    try:
        packages = backend.get_installed_packages()
        return {
            "packages": packages,
            "count": len(packages)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get packages: {str(e)}")

@app.get("/sessions")
def list_sessions():
    """List all active sessions"""
    return {
        "active_sessions": list(sessions.keys()),
        "count": len(sessions)
    }

# Health check endpoint
@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_sessions": len(sessions),
        "protocols": ["rest", "mcp"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True, 
        log_level="debug"
    )
