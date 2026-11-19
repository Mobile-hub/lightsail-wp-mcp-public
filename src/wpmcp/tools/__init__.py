"""Tool registry: every tool the server exposes, with the scope it needs and its annotations.

A tool that is not in the registry does not exist for clients. Each one is typed and does
one thing; there is no generic "run this request" tool, by design (see docs/security.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..auth.bearer import PRINCIPAL_KEY
from ..auth.oauth import Principal
from ..config import SCOPE_READ, SCOPES


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., Any]
    title: str
    scope: str = SCOPE_READ
    # Changes something visible to site visitors or hard to notice (asks for confirmation).
    destructive: bool = False
    # Reaches beyond the WordPress site itself.
    open_world: bool = False
    # Changes what visitors see: counts against the live budget too.
    live: bool = False
    # Repeating the call with the same arguments changes nothing more (reads; changes
    # guarded by `expected_version`, which a repeat fails). None: read tools say True.
    idempotent: bool | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.fn.__name__

    @property
    def read_only(self) -> bool:
        return self.scope == SCOPE_READ

    @property
    def annotations(self) -> ToolAnnotations:
        return ToolAnnotations(
            title=self.title,
            readOnlyHint=self.read_only,
            destructiveHint=False if self.read_only else self.destructive,
            openWorldHint=self.open_world,
            idempotentHint=True if self.read_only else bool(self.idempotent),
        )


class Registry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec) -> None:
        if spec.scope not in SCOPES:
            raise ValueError(f"tool {spec.name}: unknown scope {spec.scope!r}")
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name} is registered twice")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def __iter__(self):
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)


def principal_of(ctx: Context) -> Principal:
    """The authenticated caller of the current tool call (set by the bearer gate)."""
    request = ctx.request_context.request
    principal = request.scope.get(PRINCIPAL_KEY) if request is not None else None
    if principal is None:
        raise PermissionError("unauthenticated request")
    return principal
