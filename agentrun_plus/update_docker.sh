#!/bin/bash

set -e

API="agentrun-api-1"
RUNNER="agentrun-python-runner-1"

# copy helper files for runner.
docker cp agentrun_plus/code_runner/api.py $RUNNER:/home/pythonuser/api.py

# copy helper files for api.
docker cp agentrun_plus/api/api.py $API:/code/api.py
docker cp agentrun_plus/api/backend.py $API:/code/backend.py
docker cp agentrun_plus/api/__init__.py $API:/code/__init__.py
docker cp agentrun_plus/code_runner/api.py $API:/code/code_runner/api.py

# copy main as last to force reload.
docker cp agentrun_plus/code_runner/main.py $RUNNER:/home/pythonuser/main.py
docker cp agentrun_plus/api/main.py $API:/code/main.py
