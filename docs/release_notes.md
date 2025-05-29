## V0.2.9(05-29-2025)
- Added a command to explicitly chown files copied using `put_archive`
- Cleaned up hard-coded user and workdir references.

## V0.2.8(05-28-2025)
- Cache Docker container instead of calling `get_container` each time.
- Added support for UID/GID setting.

## V0.2.7(05-24-2025)
- User /home/pythonuser instead of /code in Dockerfile.

## V0.2.6(05-15-2025)
- Added ability to customize installation commands.
- Optionally ignore certain dependencies.
- Optionally ignore checking certain unsafe functions.

## V0.2.5(10-21-2024)
- Added uv as the dependency manager
- improved performance and execution time through uv

## V0.2.3(04-20-2024)
- Added llama-3 example 
- Cleaned up API dependencies 
- Added Caching to the API and increased timeout

## V0.2.1 (04-17-2024)
- AgenRun now manages cached dependencies 
- Improved performance and execution time
- Dedicated Documentation site


## v0.1.1 (04-10-2024)
-  More documentation and examples
-  agentrun-api and agentrun combined Repo 
-  Cleaning up is now on a seperate thread. Performance improvement.
-  Benchmarks tests 
