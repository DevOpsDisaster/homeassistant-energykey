FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

USER root

# The upstream image currently contains a stale Yarn apt source. This project
# does not use Yarn, and Dev Container features run apt-get update when they
# install, so remove the unused source before features are applied.
RUN rm -f /etc/apt/sources.list.d/yarn.list

USER vscode

COPY --chown=vscode:vscode requirements-dev.txt /tmp/requirements-dev.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
    -r /tmp/requirements-dev.txt
