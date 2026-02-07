# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.00] - 2026-02-07

### Added

- **Model Context Protocol (MCP) Server Support**
  - Added MCP server endpoint at `/mcp` alongside existing REST API
  - Both protocols available simultaneously on the same server (port 8000)
  - MCP and REST API share the same backend and session state for full interoperability

- **MCP Tools** - 7 tools exposed via MCP protocol:
  1. `create_session()` - Create a new isolated session for code execution
  2. `execute_code(session_id, code, ...)` - Execute Python code in a session
  3. `upload_file(session_id, filename, content_base64)` - Upload files to session (base64-encoded)
  4. `download_file(session_id, src_path)` - Download files from session (base64-encoded)
  5. `list_sessions()` - List all active sessions across both protocols
  6. `get_session_info(session_id)` - Get session details and paths
  7. `close_session(session_id)` - Close and cleanup a session

- **New Files**
  - `agentrun_plus/api/mcp_server.py` - MCP server implementation using FastMCP 2.14.5
  - `tests/test_agentrun_mcp.py` - Comprehensive test suite with 23 tests covering:
    - Session management (6 tests)
    - Code execution (5 tests)
    - File operations (6 tests)
    - REST/MCP interoperability (5 tests)
    - Full integration workflow (1 test)
  - `CHANGELOG.md` - This changelog file

- **Dependencies**
  - Added `fastmcp==2.14.5` for MCP server implementation
  - Added optional `[api]` dependency group for API server components

### Changed

- **Dependency Management Improvements**
  - Moved API server dependencies (`fastapi`, `uvicorn`, `fastmcp`) to optional `[api]` group
  - Core package now only requires: `docker`, `RestrictedPython`, `loguru`
  - Users installing for library usage get minimal dependencies
  - Users installing for API server use `pip install agentrun_plus[api]`
  - Docker deployment unchanged (uses `requirements.txt`)

- **API Integration**
  - Modified `agentrun_plus/api/main.py`:
    - Mounted MCP app at `/mcp` endpoint
    - Integrated MCP app lifespan with FastAPI application
    - Updated root endpoint documentation to include MCP
    - Updated health check to report both protocols available

- **Documentation**
  - Updated `README.md` with comprehensive MCP documentation:
    - MCP endpoint details and available tools
    - Claude Desktop integration example
    - MCP Python client usage examples
    - File operations via MCP (base64 encoding/decoding)
    - REST/MCP interoperability examples
    - Updated pip installation instructions with `[api]` option

### Technical Details

- **Architecture**: Unified server design with shared state
  - Single FastAPI application serves both REST and MCP
  - `backend` and `sessions` objects passed by reference to MCP app
  - No synchronization needed (asyncio single-threaded event loop)

- **Transport**: FastMCP 2.14.5 with Streamable HTTP (modern, recommended)
  - Uses Server-Sent Events (SSE) format for responses
  - JSON-RPC 2.0 protocol for MCP tool calls
  - Stateless HTTP transport for better performance

- **Security**: Reuses existing REST API security patterns
  - Filename validation (no path traversal attempts)
  - Path validation (files restricted to artifact directory)
  - Session isolation (verify session exists before operations)
  - Base64 encoding for binary file transfer (JSON-safe)

- **Testing**: Full test coverage with custom MCP client
  - `MCPClient` helper class handles JSON-RPC 2.0 and SSE parsing
  - All tests pass (23/23 MCP tests, 25/25 REST tests)
  - Interoperability tests verify shared state between protocols

### Version Bump

- Version: 0.2.22 → 0.3.00 (minor version bump for new MCP feature)

## Previous Versions

See GitHub releases for version history prior to 0.3.00.

[0.3.00]: https://github.com/rvijayc/AgentRun/compare/v0.2.22...v0.3.00
