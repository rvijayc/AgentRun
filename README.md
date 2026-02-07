# AgentRun: Run AI Generated Code Safely

[![PyPI](https://img.shields.io/pypi/v/agentrun.svg)](https://pypi.org/project/agentrun/)
[![Tests](https://github.com/jonathan-adly/agentrun/actions/workflows/test.yml/badge.svg)](https://github.com/jonathan-adly/agentrun/actions/workflows/test.yml)
[![Changelog](https://img.shields.io/github/v/release/jonathan-adly/agentrun?include_prereleases&label=changelog)](https://github.com/jonathan-adly/agentrun/releases)
![PyPI - Downloads](https://img.shields.io/pypi/dm/agentrun)
![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FJonathan-Adly%2FAgentRun%2Fdevelop%2Fpyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/jonathan-adly/agentrun/blob/main/LICENSE)
[![MkDocs](https://img.shields.io/badge/MkDocs-526CFE?logo=materialformkdocs&logoColor=fff)](https://jonathan-adly.github.io/AgentRun/)

>> This is a fork of the AgentRun library that has some added customizations and optimizations, specifically:
>>
>> - A "full service" implementation. Both the python runner and API images use a REST API with a full-set of services needed for running Python programs.
>> - Customize Install Commands.
>> - Fixed issues with "uv" installer.
>> - Improved management of cached dependencies.
>> - Option to allow certain unsafe functions (due to false positives).
>> - Option to ignore certain dependencies.

AgentRun is a Python library that makes it easy to run Python code safely from large language models (LLMs) with a single line of code. Built on top of the Docker Python SDK and RestrictedPython, it provides a simple, transparent, and user-friendly API to manage isolated code execution.

AgentRun automatically installs and uninstalls dependencies with optional caching, limits resource consumption, checks code safety, and sets execution timeouts. It has 100% test coverage with full static typing and only two dependencies.

- [Documentation](https://jonathan-adly.github.io/AgentRun/)

- [Get started in minutes](#getting-started)

> [!NOTE]
> Looking for a state of the art RAG API? Check out [ColiVara](https://github.com/tjmlabs/ColiVara), also from us.

## Why?

Giving code execution ability to LLMs is a massive upgrade. Consider the following user query: `what is 12345 * 54321?` or even something more ambitious like `what is the average daily move of Apple stock during the last week?`? With code execution it is possible for LLMs to answer both accurately by executing code.

However, executing untrusted code is dangerous and full of potential footguns. For instance, without proper safeguards, an LLM might generate harmful code like this:

```python
import os
# deletes all files and directories
os.system('rm -rf /')
```

This package gives code execution ability to **any LLM** in a single line of code, while preventing and guarding against dangerous code.


## Key Features

- **Safe code execution**: AgentRun checks the generated code for dangerous elements before execution
- **Isolated Environment**: Code is executed in a fully isolated docker container
- **Configurable Resource Management**: You can set how much compute resources the code can consume, with sane defaults
- **Timeouts**: Set time limits on how long a script can take to run
- **Dependency Management**: Complete control on what dependencies are allowed to install
- **Dependency Caching**: AgentRun gives you the ability to cache any dependency in advance in the docker container to optimize performance.
- **Automatic Cleanups**: AgentRun cleans any artifacts created by the generated code.
- **Dual Protocol Support**: Access via REST API or Model Context Protocol (MCP) for seamless LLM integration
- **REST API & MCP Server**: Both protocols available simultaneously on the same server, sharing session state
- **Transparent Exception Handling**: AgentRun returns the same exact output as running Python in your system - exceptions and tracebacks included. No cryptic docker messages.

If you want to use your own Docker configuration, install this package with pip and simply initialize AgentRun with a running Docker container. Additionally, you can use an already configured Docker Compose setup and API that is ready for self-hosting by cloning this repo.

Unless you are comfortable with Docker, **we highly recommend using the REST API with the already configured Docker as a standalone service.**

## Getting Started

Clone the github repository and start immediately with a standalone REST API.

```bash
git clone https://github.com/rvijayc/agentrun
cd agentrun_plus
./docker-compose.sh dev up --build
```

This starts the AgentRunPlus server with which you can communicate using the AgentRun REST API interface.

You can also interact with `curl` if you prefer, but it might get a bit cumbersome:

```shell
```

## Using the MCP (Model Context Protocol) Server

AgentRunPlus now supports the Model Context Protocol (MCP), allowing LLMs like Claude to use it as a server with tools for code execution. Both REST API and MCP endpoints are available simultaneously on the same server.

### MCP Endpoint

Once the server is running, the MCP endpoint is available at:

```
http://localhost:8000/mcp
```

### Available MCP Tools

AgentRunPlus exposes 7 MCP tools:

1. **create_session()** - Create a new isolated session
2. **execute_code(session_id, code, ...)** - Execute Python code in a session
3. **upload_file(session_id, filename, content_base64)** - Upload a file to session
4. **download_file(session_id, src_path)** - Download a file from session
5. **list_sessions()** - List all active sessions
6. **get_session_info(session_id)** - Get session details
7. **close_session(session_id)** - Close and cleanup a session

### Using with Claude Desktop

Add to your Claude Desktop MCP configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "agentrun": {
      "url": "http://localhost:8000/mcp",
      "transport": "http"
    }
  }
}
```

### Using with MCP Python Client

```python
from mcp import ClientSession
import asyncio
import base64

async def run_code_with_mcp():
    async with ClientSession(url="http://localhost:8000/mcp") as client:
        # Create a session
        result = await client.call_tool("create_session", {})
        session_id = result["session_id"]

        # Execute some Python code
        exec_result = await client.call_tool("execute_code", {
            "session_id": session_id,
            "code": """
import pandas as pd
import matplotlib.pyplot as plt

data = {'x': [1, 2, 3, 4], 'y': [10, 20, 25, 30]}
df = pd.DataFrame(data)
print(df.describe())
"""
        })

        print(exec_result["output"])

        # Close the session
        await client.call_tool("close_session", {"session_id": session_id})

asyncio.run(run_code_with_mcp())
```

### File Operations via MCP

```python
import base64

# Upload a file
with open("data.csv", "rb") as f:
    content = f.read()
    content_base64 = base64.b64encode(content).decode('utf-8')

upload_result = await client.call_tool("upload_file", {
    "session_id": session_id,
    "filename": "data.csv",
    "content_base64": content_base64
})

# Download a file
download_result = await client.call_tool("download_file", {
    "session_id": session_id,
    "src_path": f"{artifact_path}/output.png"
})

# Decode the downloaded file
file_content = base64.b64decode(download_result["content_base64"])
with open("output.png", "wb") as f:
    f.write(file_content)
```

### Interoperability with REST API

Sessions created via MCP are accessible via the REST API and vice versa. Both protocols share the same backend:

```python
# Create session via MCP
mcp_result = await mcp_client.call_tool("create_session", {})
session_id = mcp_result["session_id"]

# Execute code via REST API on the same session
from agentrun_plus import AgentRunAPIClient
api_client = AgentRunAPIClient('http://localhost:8000')
api_client.execute_code(session_id, 'print("Hello from REST!")')
```

### pip install

Install AgentRunPlus via pip:

**For library usage** (core AgentRun backend):
```bash
pip install agentrun_plus
```

**For running the API server** (includes REST API + MCP server):
```bash
pip install agentrun_plus[api]
```

**Note:** If using docker-compose (recommended), dependencies are managed automatically in the container.

Here is a simple example of using the API client to connect to a running server:

```Python
~/llm/AgentRun$ python3
Python 3.13.2 | packaged by Anaconda, Inc. | (main, Feb  6 2025, 18:56:02) [GCC 11.2.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> from agentrun_plus import AgentRunAPIClient
>>> client = AgentRunAPIClient('http://localhost:8000')
>>> session = client.create_session()
>>> client.execute_code(session.session_id, 'print("Hello World!")')
{'output': 'Hello World!\n', 'success': True}
>>> client.close_session(session.session_id)
{'message': 'Session 978c8bb329cd473d99cd3b596c7b557a closed successfully'}
>>>
```

