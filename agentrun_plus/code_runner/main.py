from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.responses import FileResponse
import subprocess
import os
import shutil
import sys
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import traceback
import logging

from api import (
        CommandRequest, 
        CommandResponse,
        FileOperationResponse,
        PythonCodeRequest,
        PythonCodeResponse
)

# Create a logger for app specific messages.
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(
        logging.Formatter('%(levelname)s: %(message)s')
)
log.addHandler(stream_handler)

app = FastAPI(title="Sandbox Server", description="Secure sandbox for executing commands and Python code")

# Configure sandbox working directory
SANDBOX_DIR = os.getenv("SANDBOX_DIR", '/home/pythonuser')
os.makedirs(SANDBOX_DIR, exist_ok=True)

def safe_path(path: str) -> Path:
    """Ensure path is within sandbox directory"""
    if not path:
        return Path(SANDBOX_DIR)
    
    # Convert to absolute path within sandbox
    if not os.path.isabs(path):
        safe_path = Path(SANDBOX_DIR) / path
    else:
        safe_path = Path(path)
    
    # Resolve and check it's within sandbox
    try:
        resolved = safe_path.resolve()
        sandbox_resolved = Path(SANDBOX_DIR).resolve()
        
        # Ensure the path is within sandbox directory
        if not str(resolved).startswith(str(sandbox_resolved)):
            raise ValueError(f"Path {path} is outside sandbox directory")
            
        return resolved
    except Exception as e:
        raise ValueError(f"Invalid path {path}: {e}")

# -------------------------------------------------------------
# API Endpoints.
# -------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Sandbox Server is running", "sandbox_dir": SANDBOX_DIR}

@app.post("/execute-command", response_model=CommandResponse)
def execute_command(request: CommandRequest):
    """Execute arbitrary unix commands"""
    import time
    start_time = time.time()
    
    try:
        # Set working directory
        if request.working_dir:
            work_dir = safe_path(request.working_dir)
            os.makedirs(work_dir, exist_ok=True)
        else:
            work_dir = SANDBOX_DIR
            
        # Execute command
        result = subprocess.run(
            request.command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=request.timeout
        )
        
        execution_time = time.time() - start_time
        
        log.debug(f'{request.command} executed in {execution_time} seconds.')
        for line in result.stdout.splitlines():
            log.debug(f'[stdout]: {line}')
        for line in result.stderr.splitlines():
            log.debug(f'[stderr]: {line}')

        return CommandResponse(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
            execution_time=execution_time
        )
        
    except subprocess.TimeoutExpired:
        execution_time = time.time() - start_time
        return CommandResponse(
            success=False,
            stdout="",
            stderr=f"Command timed out after {request.timeout} seconds",
            return_code=-1,
            execution_time=execution_time
        )
    except Exception as e:
        execution_time = time.time() - start_time
        return CommandResponse(
            success=False,
            stdout="",
            stderr=f"Error executing command: {str(e)}",
            return_code=-1,
            execution_time=execution_time
        )

@app.post("/execute-python", response_model=PythonCodeResponse)
def execute_python(request: PythonCodeRequest):
    """Execute arbitrary Python code"""
    import time
    start_time = time.time()
    
    # Capture stdout and stderr
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    
    original_cwd = None
    try:
        # Change to sandbox directory for execution
        original_cwd = os.getcwd()
        # Set working directory
        if request.working_dir:
            work_dir = safe_path(request.working_dir)
            os.makedirs(work_dir, exist_ok=True)
        else:
            work_dir = SANDBOX_DIR
        os.chdir(work_dir)
        
        # Create a local namespace for execution
        local_vars = {
            '__name__': '__main__',
            '__file__': '<sandbox>',
            'sandbox_dir': SANDBOX_DIR
        }
        
        # Redirect stdout and stderr
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            # Execute the code
            exec(request.code, local_vars)
            
        execution_time = time.time() - start_time
        
        return PythonCodeResponse(
            success=True,
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            result="Code executed successfully",
            execution_time=execution_time
        )
        
    except Exception as e:
        execution_time = time.time() - start_time
        # Get full traceback
        error_traceback = traceback.format_exc()
        stderr_buffer.write(error_traceback)
        
        return PythonCodeResponse(
            success=False,
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            result=f"Error: {str(e)}",
            execution_time=execution_time
        )
    finally:
        # Restore original working directory
        if original_cwd:
            os.chdir(original_cwd)

@app.post("/upload-file", response_model=FileOperationResponse)
def upload_file(
    file: UploadFile = File(...),
    destination: str = Form(...)
):
    """Upload a local file to specified destination on server"""
    try:
        # Ensure destination is within sandbox
        dest_path = safe_path(destination)
        
        # Create destination directory if it doesn't exist
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save the uploaded file
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        log.info(f'Uploaded file: {dest_path}')
        return FileOperationResponse(
            success=True,
            message=f"File uploaded successfully to {dest_path}",
            file_path=str(dest_path)
        )
        
    except Exception as e:
        return FileOperationResponse(
            success=False,
            message=f"Error uploading file: {str(e)}",
        )

@app.get("/download-file")
def download_file(file_path: str):
    """Download a file from the server"""
    try:
        # Ensure file path is within sandbox
        safe_file_path = safe_path(file_path)
        log.info(f'Downloading {file_path} ({safe_file_path})')
        
        # Check if file exists
        if not safe_file_path.exists():
            log.error(f'[download-file] File does not exist: {safe_file_path}')
            raise HTTPException(status_code=404, detail="File not found")
            
        if not safe_file_path.is_file():
            log.error(f'[download-file] Path is not a file: {safe_file_path}')
            raise HTTPException(status_code=400, detail="Path is not a file")
            
        return FileResponse(
            path=str(safe_file_path),
            filename=safe_file_path.name,
            media_type='application/octet-stream'
        )
        
    except HTTPException as e:
        raise 
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")

@app.post("/copy-file", response_model=FileOperationResponse)
def copy_file(source: str, destination: str):
    """Copy a file from source to destination within the sandbox"""
    try:
        source_path = safe_path(source)
        dest_path = safe_path(destination)
        
        # Check if source exists
        if not source_path.exists():
            return FileOperationResponse(
                success=False,
                message=f"Source file {source} does not exist"
            )
            
        # Create destination directory if needed
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Copy file
        shutil.copy2(source_path, dest_path)
        
        return FileOperationResponse(
            success=True,
            message=f"File copied from {source_path} to {dest_path}",
            file_path=str(dest_path)
        )
        
    except Exception as e:
        return FileOperationResponse(
            success=False,
            message=f"Error copying file: {str(e)}"
        )

@app.get("/list-files")
def list_files(directory: str = ""):
    """List files in a directory within the sandbox"""
    try:
        dir_path = safe_path(directory)
        
        if not dir_path.exists():
            raise HTTPException(status_code=404, detail="Directory not found")
            
        if not dir_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
            
        files = []
        for item in dir_path.iterdir():
            files.append({
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
                "path": str(item.relative_to(Path(SANDBOX_DIR)))
            })
            
        return {"files": files, "directory": str(dir_path.relative_to(Path(SANDBOX_DIR)))}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")

@app.delete("/delete-file")
def delete_file(file_path: str):
    """Delete a file or directory within the sandbox"""
    try:
        target_path = safe_path(file_path)
        
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="File or directory not found")
            
        if target_path.is_file():
            target_path.unlink()
            message = f"File {file_path} deleted successfully"
        elif target_path.is_dir():
            shutil.rmtree(target_path)
            message = f"Directory {file_path} deleted successfully"
        else:
            raise HTTPException(status_code=400, detail="Invalid file type")
            
        return FileOperationResponse(
            success=True,
            message=message
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "sandbox_dir": SANDBOX_DIR,
        "python_version": sys.version,
        "working_directory": os.getcwd()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=5000, 
        reload=True, 
        log_level="debug"
    )
