#!/bin/bash
CODE="print('hello, world!')"
curl -X POST \
    http://localhost:8000/v1/run/ \
    -H "Content-Type: application/json" \
    -d "{\"code\": \"${CODE}\"}"
