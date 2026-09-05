"""One base exception for every failure this package anticipates.

It inherits from the MCP SDK's ``ToolError`` on purpose. The SDK distinguishes an
*anticipated* tool failure from a crash: a ``ToolError`` is returned to the client with
its own message intact, while any other exception is reported only as
``Error executing tool <name>`` and the text is withheld.

A refused submission is the most important message this server ever produces — the model
and the user both need to read *which* gate refused and why. So every domain error here
is anticipated by construction, and only a genuine bug shows up as a crash.

It also inherits from ``RuntimeError`` so that callers outside MCP (the CLI, tests,
library users) can keep catching it the ordinary way.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class FdsMcpError(ToolError, RuntimeError):
    """Base class for every anticipated failure in fds-mcp."""


__all__ = ["FdsMcpError", "ToolError"]
