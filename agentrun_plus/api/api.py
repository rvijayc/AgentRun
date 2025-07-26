import os
from typing import Optional, List
from dataclasses import dataclass
from urllib.parse import urljoin
import json

import requests
from pydantic import BaseModel

# -------------------------------------
# Pydantic Models for REST API.
# -------------------------------------
class SessionCreateResponse(BaseModel):
    session_id: str
    workdir: str
    source_path: str
    artifact_path: str

class ExecuteCodeRequest(BaseModel):
    python_code: str
    ignore_dependencies: Optional[List[str]] = None
    ignore_unsafe_functions: Optional[List[str]] = None

class ExecuteCodeResponse(BaseModel):
    output: str
    success: bool

class CopyFileToResponse(BaseModel):
    message: str
    destination_path: str

class CopyFileFromRequest(BaseModel):
    src_path: str
    filename: str

class SessionInfoResponse(BaseModel):
    session_id: str
    source_path: str
    artifact_path: str

@dataclass
class SessionInfo:
    """Helper class to track test sessions"""
    session_id: str
    workdir: str
    source_path: str
    artifact_path: str

# -------------------------------------
# Helper Class for REST API Usage.
# -------------------------------------

class AgentRunAPIClient:
    """Client for interacting with AgentRun API"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.debug = os.getenv("AGENTRUN_DEBUG", "false").lower() == "true"
    
    def _url(self, path: str) -> str:
        """Construct full URL from path"""
        return urljoin(self.base_url + '/', path.lstrip('/'))
    
    def _handle_response(self, response: requests.Response, operation: str) -> None:
        """Handle HTTP response and provide detailed error information"""
        if self.debug:
            print(f"\n[DEBUG] {operation}")
            print(f"URL: {response.url}")
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")
            
        if response.status_code >= 400:
            print(f"\n[ERROR] {operation} failed")
            print(f"Status Code: {response.status_code}")
            print(f"URL: {response.url}")
            
            # Try to get error details from response
            try:
                error_data = response.json()
                print(f"Error Response: {json.dumps(error_data, indent=2)}")
            except:
                print(f"Raw Response: {response.text[:500]}")
            
            # For 500 errors, print additional debugging info
            if response.status_code == 500:
                print("\n[DEBUGGING TIPS]")
                print("1. Check if the backend container 'python_runner' is running:")
                print("   docker ps | grep python_runner")
                print("2. Check API server logs:")
                print("   docker logs <api-container-name>")
                print("3. Verify the backend AgentRun class is properly initialized")
                print("4. Check if all required dependencies are installed in the API container")
       
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            custom_message = (
                f"HTTP Error: {response.status_code} {response.reason} | "
                f"Message: {response.json()['detail']}"  
            )
            raise requests.exceptions.HTTPError(custom_message, response=response)

        response.raise_for_status()
    
    def create_session(self) -> SessionInfo:
        """Create a new session"""
        response = self.session.post(self._url("/sessions"))
        self._handle_response(response, "Create Session")
        data = response.json()
        return SessionInfo(**data)
    
    def get_session_info(self, session_id: str) -> dict:
        """Get session information"""
        response = self.session.get(self._url(f"/sessions/{session_id}"))
        self._handle_response(response, f"Get Session Info [{session_id}]")
        return response.json()
    
    def close_session(self, session_id: str) -> dict:
        """Close a session"""
        response = self.session.delete(self._url(f"/sessions/{session_id}"))
        self._handle_response(response, f"Close Session [{session_id}]")
        return response.json()
    
    def list_sessions(self) -> dict:
        """List all active sessions"""
        response = self.session.get(self._url("/sessions"))
        self._handle_response(response, "List Sessions")
        return response.json()
    
    def execute_code(self, session_id: str, python_code: str, 
                     ignore_dependencies: Optional[List[str]] = None,
                     ignore_unsafe_functions: Optional[List[str]] = None) -> dict:
        """Execute Python code in a session"""
        payload = {
            "python_code": python_code,
            "ignore_dependencies": ignore_dependencies,
            "ignore_unsafe_functions": ignore_unsafe_functions
        }
        response = self.session.post(
            self._url(f"/sessions/{session_id}/execute"),
            json=payload
        )
        self._handle_response(response, f"Execute Code [{session_id}]")
        return response.json()
    
    def upload_file(self, session_id: str, file_path: str, filename: Optional[str] = None) -> dict:
        """Upload a file to a session"""
        if filename is None:
            filename = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f, 'application/octet-stream')}
            response = self.session.post(
                self._url(f"/sessions/{session_id}/copy-to"),
                files=files
            )
        self._handle_response(response, f"Upload File [{session_id}]")
        return response.json()
    
    def upload_file_content(self, session_id: str, content: bytes, filename: str) -> dict:
        """Upload file content to a session"""
        files = {'file': (filename, content, 'application/octet-stream')}
        response = self.session.post(
            self._url(f"/sessions/{session_id}/copy-to"),
            files=files
        )
        self._handle_response(response, f"Upload File Content [{session_id}]")
        return response.json()
    
    def download_file(self, session_id: str, src_path: str, dest_path: str, filename: Optional[str] = None) -> str:
        """Download a file from a session"""
        if filename is None:
            filename = os.path.basename(src_path)
        
        payload = {
            "src_path": src_path,
            "filename": filename
        }
        response = self.session.post(
            self._url(f"/sessions/{session_id}/copy-from"),
            json=payload
        )
        self._handle_response(response, f"Download File [{session_id}]")
        
        dest_path = os.path.join(dest_path, filename)
        with open(dest_path, 'wb') as f:
            f.write(response.content)
        
        return dest_path
    
    def get_health(self) -> dict:
        """Get health status"""
        response = self.session.get(self._url("/health"))
        self._handle_response(response, "Health Check")
        return response.json()
    
    def get_root(self) -> dict:
        """Get root endpoint info"""
        response = self.session.get(self._url("/"))
        self._handle_response(response, "Root Endpoint")
        return response.json()

