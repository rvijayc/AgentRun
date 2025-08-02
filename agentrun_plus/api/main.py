from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import Response
from typing import Dict
from uuid import uuid4
import os
import tempfile
from pathlib import Path
import logging
import sys
from contextlib import asynccontextmanager

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - no specific startup logic needed for this app
    yield
    # Shutdown - clean up all sessions
    for session_id, session in sessions.items():
        try:
            backend.close_session(session)
        except Exception as e:
            print(f"Error closing session {session_id}: {e}")
    sessions.clear()

# Initialize FastAPI app
app = FastAPI(title="AgentRun API", version="1.0.0", lifespan=lifespan)

# Initialize the backend
backend = AgentRun(container_url='http://python_runner:5000')

# Store active sessions
sessions: Dict[str, AgentRunSession] = {}

# Create a logger for app specific messages.
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
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
        "endpoints": {
            "POST /sessions": "Create a new session",
            "GET /sessions/{session_id}": "Get session information",
            "DELETE /sessions/{session_id}": "Close a session",
            "POST /sessions/{session_id}/execute": "Execute Python code",
            "POST /sessions/{session_id}/copy-to": "Copy file to session",
            "POST /sessions/{session_id}/copy-from": "Copy file from session"
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
            artifact_path=session.artifact_path()
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
    
    try:
        output = session.execute_code(
            python_code=request.python_code,
            ignore_dependencies=request.ignore_dependencies,
            ignore_unsafe_functions=request.ignore_unsafe_functions
        )
        log.info(f'ignore_unsafe_functions = {request.ignore_unsafe_functions}')
        # Always returns success=True because the execution itself succeeded
        # Any Python errors/exceptions will be in the output
        return ExecuteCodeResponse(output=output, success=True)
    except Exception as e:
        # This only catches errors from the execution infrastructure itself,
        # not Python errors in the user's code
        return ExecuteCodeResponse(output=str(e), success=False)

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
    return {"status": "healthy", "active_sessions": len(sessions)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True, 
        log_level="debug"
    )
