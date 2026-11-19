"""Diagnostics about the connection itself."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from .. import __version__
from ..config import SCOPE_WRITE, SCOPES, Settings
from . import Registry, ToolSpec, principal_of


def register(registry: Registry, settings: Settings) -> None:
    def get_connection_info(ctx: Context) -> dict[str, Any]:
        """Describe this connection: which site it operates, which client is connected and
        whether it may change content or only read it."""
        principal = principal_of(ctx)
        return {
            "site": settings.site_name,
            "server_version": __version__,
            "client": principal.client_name,
            "scopes": [s for s in SCOPES if s in principal.scopes],
            "can_write": principal.can(SCOPE_WRITE),
        }

    registry.add(ToolSpec(get_connection_info, title="Show connection details"))
