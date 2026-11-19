"""Site abilities: abilities of WordPress core or other plugins that the site owner listed
for wpmcp on the WordPress side (``WPMCP_BRIDGE_ABILITIES`` or the
``wpmcp_bridge_abilities`` filter; see the bridge's ``site-abilities.php``).

Each listed ability becomes one typed tool, named after it (``core/get-user-info`` →
``core_get_user_info``), with the ability's own input schema. A read-only ability needs
``wp:read``; any other needs ``wp:write`` and counts as a live, destructive change, since
nothing here knows whether it can be undone. Arguments are checked against the schema's
properties like every other tool, and the bridge runs the ability as the integration user,
whose fixed capabilities still apply. There is no tool that runs an ability by name.

The list is read from the bridge when a client first lists or calls tools, kept for ten
minutes, and retried after a minute if WordPress could not be reached.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.types import ToolAnnotations

from ..config import SCOPE_READ, SCOPE_WRITE
from ..wp.client import WordPressClient, WordPressError

log = logging.getLogger(__name__)

TTL = 600.0
RETRY = 60.0
MAX_ABILITIES = 20
MAX_DESCRIPTION = 1000
ABILITY_NAME = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": True}


@dataclass(frozen=True)
class AbilityTool:
    """One listed ability, as a tool. Has the attributes ``WpMcp.call_tool`` checks."""

    ability: str
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    takes_input: bool
    read_only: bool
    idempotent: bool

    @property
    def scope(self) -> str:
        return SCOPE_READ if self.read_only else SCOPE_WRITE

    @property
    def live(self) -> bool:
        return not self.read_only

    @property
    def known_arguments(self) -> set[str]:
        return set(self.input_schema.get("properties", {}))

    @property
    def annotations(self) -> ToolAnnotations:
        return ToolAnnotations(
            title=self.title,
            readOnlyHint=self.read_only,
            destructiveHint=not self.read_only,
            idempotentHint=self.read_only or self.idempotent,
            openWorldHint=False,
        )

    def definition(self) -> dict[str, Any]:
        """The tools/list entry (without securitySchemes, which WpMcp adds)."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": OUTPUT_SCHEMA,
            "annotations": self.annotations.model_dump(exclude_none=True),
        }


def tool_from(item: Any) -> AbilityTool | None:
    """An entry of the bridge's /abilities answer as a tool, or None if it cannot be one."""
    if not isinstance(item, dict) or not ABILITY_NAME.match(str(item.get("name", ""))):
        return None
    ability = item["name"]
    schema = item.get("input_schema") or None
    if schema is None:
        input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    elif isinstance(schema, dict) and schema.get("type") == "object":
        input_schema = schema
    else:
        # A tool takes an object of arguments; an ability with a scalar input cannot be one.
        log.warning("site ability %s skipped: its input is not an object", ability)
        return None
    hints = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    read_only = hints.get("readonly") is True
    label = str(item.get("label") or ability)[:200]
    description = f"Site ability {ability} ({label}): {item.get('description') or ''}".strip()
    if not read_only:
        description += (
            " It changes the site and may not be undoable: only with the user's explicit "
            "go-ahead for this change."
        )
    return AbilityTool(
        ability=ability,
        name=ability.replace("/", "_").replace("-", "_"),
        title=label,
        description=description[:MAX_DESCRIPTION],
        input_schema=input_schema,
        takes_input=schema is not None,
        read_only=read_only,
        idempotent=hints.get("idempotent") is True,
    )


def without_content_keys(value: Any) -> Any:
    """ChatGPT shows a result with a field named "content" as {}: such fields become
    "body" (or "content_value" where "body" is taken), at any depth."""
    if isinstance(value, dict):
        renamed: dict[str, Any] = {}
        for key, item in value.items():
            if key == "content":
                key = "content_value" if "body" in value else "body"
            renamed[key] = without_content_keys(item)
        return renamed
    if isinstance(value, list):
        return [without_content_keys(item) for item in value]
    return value


class SiteAbilities:
    def __init__(
        self,
        wp: WordPressClient,
        reserved: Callable[[str], bool],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._wp = wp
        self._reserved = reserved
        self._clock = clock
        self._tools: dict[str, AbilityTool] = {}
        self._loaded_at: float | None = None
        self._failed_at: float | None = None

    async def tools(self) -> dict[str, AbilityTool]:
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < TTL:
            return self._tools
        if self._failed_at is not None and now - self._failed_at < RETRY:
            return self._tools
        try:
            answer = await self._wp.get("/abilities")
        except WordPressError as exc:
            log.warning("site abilities not loaded: %s", exc)
            self._failed_at = now
            return self._tools
        tools: dict[str, AbilityTool] = {}
        items = answer.get("items", []) if isinstance(answer, dict) else []
        for item in items[:MAX_ABILITIES]:
            tool = tool_from(item)
            if tool is None:
                continue
            if self._reserved(tool.name) or tool.name in tools:
                log.warning(
                    "site ability %s skipped: tool name %s is taken", tool.ability, tool.name
                )
                continue
            tools[tool.name] = tool
        self._tools, self._loaded_at, self._failed_at = tools, now, None
        return tools

    async def run(self, tool: AbilityTool, arguments: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"name": tool.ability}
        if tool.takes_input or arguments:
            body["input"] = arguments
        result = await self._wp.post("/abilities/run", body)
        return without_content_keys(result)
