# syntax=docker/dockerfile:1.4
# Build context is the scaffold root:
#   docker build -f adapters/docker/Dockerfile -t honeypot-scaffold .
#
#   /workdir         uid 1000, agent-writable
#   /grader          root:root 0700
#   /task.py         root:root 0600
#   /variants.json   root:root 0600
FROM python:3.13-slim

RUN useradd --uid 1000 --create-home --shell /bin/bash agent

# The directory itself must be agent-owned, not just its contents.
RUN mkdir -p /workdir && chown agent:agent /workdir
WORKDIR /workdir
COPY --chown=agent:agent agent_data/ /workdir/

COPY --chown=root:root --chmod=700 grader/ /grader/
RUN chmod 700 /grader && chmod -R go-rwx /grader

COPY --chown=root:root --chmod=600 task.py /task.py
COPY --chown=root:root --chmod=600 variants.json /variants.json

# Fail the build rather than ship a readable grader or an unwritable workdir.
RUN set -eu; \
    for sealed in /grader/grader.py /grader/grader_data/answers.json /task.py /variants.json; do \
      if su agent -c "cat $sealed" >/dev/null 2>&1; then \
        echo "FATAL: agent can read $sealed" >&2; exit 1; fi; \
    done; \
    su agent -c 'touch /workdir/.probe && rm /workdir/.probe'

USER agent
CMD ["sleep", "infinity"]
