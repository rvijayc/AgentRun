from typing import Optional

import requests
from pydantic import BaseModel, Field

# -------------------------------------------------------------
# REST API Pydantic Interface.
# -------------------------------------------------------------
class FileCopyRequest(BaseModel):
    source: str = Field(..., min_length=1, description="Source file path")
    destination: str = Field(..., min_length=1, description="Destination file path")

class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Shell command to execute")
    working_dir: Optional[str] = Field(None, description="Working directory for command execution")
    timeout: int = Field(30, ge=1, le=300, description="Timeout in seconds")

class CommandResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    return_code: int
    execution_time: float

class PythonCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Python code to execute")
    working_dir: Optional[str] = Field(None, description="Working directory for command execution")
    timeout: int = Field(30, ge=1, le=300, description="Timeout in seconds")

class PythonCodeResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    result: Optional[str] = None
    execution_time: float

class FileUploadRequest(BaseModel):
    destination: str = Field(..., min_length=1, description="Destination path for uploaded file")
    
class FileOperationResponse(BaseModel):
    success: bool
    message: str
    file_path: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    sandbox_dir: str
    python_version: str
    working_directory: str

class FileInfo(BaseModel):
    name: str
    type: str  # "file" or "directory"
    size: Optional[int] = None
    path: str

class FileListResponse(BaseModel):
    files: list[FileInfo]
    directory: str

# -------------------------------------------------------------
# A helper class for user by clients using the runner's API.
# -------------------------------------------------------------

class RunnerClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        
    def execute_command(self, request: CommandRequest) -> CommandResponse:
        """Execute a unix command"""
        response = self.session.post(
            f"{self.base_url}/execute-command", 
            json=request.model_dump()
        )
        response.raise_for_status()
        return CommandResponse(**response.json())
    
    def execute_python(self, request: PythonCodeRequest) -> PythonCodeResponse:
        """Execute Python code"""
        response = self.session.post(
            f"{self.base_url}/execute-python", 
            json=request.model_dump()
        )
        response.raise_for_status()
        return PythonCodeResponse(**response.json())
    
    def upload_file(self, local_path: str, upload_request: FileUploadRequest) -> FileOperationResponse:
        """Upload a local file to the sandbox"""
        with open(local_path, 'rb') as f:
            files = {'file': f}
            data = upload_request.model_dump()
            response = self.session.post(
                f"{self.base_url}/upload-file", 
                files=files, 
                data=data
            )
        response.raise_for_status()
        return FileOperationResponse(**response.json())
    
    def download_file(self, file_path: str, local_destination: str) -> bool:
        """Download a file from the sandbox"""
        response = self.session.get(
            f"{self.base_url}/download-file", 
            params={'file_path': file_path}
        )
        if response.status_code == 200:
            with open(local_destination, 'wb') as f:
                f.write(response.content)
            return True
        response.raise_for_status()
        return False
    
    def copy_file(self, copy_request: FileCopyRequest) -> FileOperationResponse:
        """Copy a file within the sandbox"""
        response = self.session.post(
            f"{self.base_url}/copy-file", 
            params=copy_request.model_dump()
        )
        response.raise_for_status()
        return FileOperationResponse(**response.json())
    
    def list_files(self, directory: str = "") -> FileListResponse:
        """List files in a directory"""
        response = self.session.get(
            f"{self.base_url}/list-files", 
            params={'directory': directory}
        )
        response.raise_for_status()
        return FileListResponse(**response.json())
    
    def delete_file(self, file_path: str) -> FileOperationResponse:
        """Delete a file or directory"""
        response = self.session.delete(
            f"{self.base_url}/delete-file", 
            params={'file_path': file_path}
        )
        response.raise_for_status()
        return FileOperationResponse(**response.json())
    
    def health_check(self) -> HealthResponse:
        """Check server health"""
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return HealthResponse(**response.json())

