# Container image for the Cairn MCP server (stdio transport).
# Used by registries like Glama to build the server and verify it responds to
# MCP introspection. For normal local use you don't need Docker — see the
# README Quickstart (`uvx cairn-mcp-server`).

FROM python:3.11-slim

WORKDIR /app

# Install the stdlib-only engine and the MCP server from source (monorepo).
# Add the optional document-import parsers with: pip install ./core[convert]
COPY core ./core
COPY mcp-server ./mcp-server
RUN pip install --no-cache-dir ./core ./mcp-server

# Cairn operates on a workspace directory; mount your files here at runtime.
ENV CAIRN_WORKSPACE=/workspace
RUN mkdir -p /workspace
VOLUME ["/workspace"]

# stdio MCP server — run with:  docker run -i --rm -v /path/to/notes:/workspace <image>
ENTRYPOINT ["cairn-mcp-server"]
