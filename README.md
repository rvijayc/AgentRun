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
- **Comes with a REST API**: Hate setting up docker? AgentRun comes with already configured docker setup for self-hosting.
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

### pip install 

Install AgentRunPlus with a single command via pip (you will need to configure your own Docker setup):

```bash
pip install agentrun_plus
```

Here is a simple example of creating a session and executing some python code.

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

