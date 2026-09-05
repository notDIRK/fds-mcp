# Runs the MCP server over stdio in a container.
#
# It exists mostly so directories such as Glama can start the server and read its tool
# list. That works with no credentials at all: the four account-free tools are enough to
# answer an introspection request.
#
#   docker build -t fds-mcp .
#   docker run --rm -i fds-mcp
#
# To use the token-bound tools, mount the config directory read-only:
#
#   docker run --rm -i -v "$HOME/.config/fds-mcp:/config:ro" -e FDS_MCP_HOME=/config fds-mcp
#
# Nothing is written inside the container; drafts belong on the host.

FROM python:3.12-slim

# No .pyc, unbuffered stdio -- the transport is stdout, so buffering would stall it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

# Never as root: this process talks to a public API and writes local files.
RUN useradd --create-home --uid 10001 fds
USER fds

# stdio transport: no port, no healthcheck, the client owns the lifecycle.
ENTRYPOINT ["fds-mcp", "serve"]
