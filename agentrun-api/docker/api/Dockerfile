FROM python:3.12.2-slim-bullseye

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /work

# copy packages to install ...
COPY ./requirements.txt /work/
# if you wish to use the locally installed version of agentrun_plus, then do the following:
# - From the root folder run: `rm -rf agentrun-api/dist && python3 -m build --outdir=agentrun-api/dist`.
# - Edit agentrun-api/pip.conf to add this line under [global]: `find-links: /work/agentrun/dist`
# - The following settings will ensure the installer will find the agentrun_plus
#   distribution under /work/agentrun/dist/
COPY ./pip.conf /work/
# COPY ./dist /work/agentrun/dist
RUN PIP_CONFIG_FILE=/work/pip.conf pip install -r requirements.txt

WORKDIR /code
