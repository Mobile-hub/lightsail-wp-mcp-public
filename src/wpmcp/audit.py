"""Audit trail: one JSON object per line on the ``wpmcp.audit`` logger.

In production the service logs to journald, which the service user cannot rewrite.
Record who did what to which object; never record argument contents, passwords,
codes or tokens.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("wpmcp.audit")


def audit(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    record = {"event": event, **{k: v for k, v in fields.items() if v is not None}}
    log.log(level, json.dumps(record, sort_keys=True, default=str))
