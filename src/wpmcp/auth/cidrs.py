"""Allowlist of client egress ranges (Anthropic, OpenAI) for the machine-only endpoints.

The list is a plain file, one CIDR per line (``#`` starts a comment), refreshed from
outside the process. It is not authentication: other users of the same products share
these addresses. It keeps the token and MCP endpoints away from the rest of the internet
and makes a stolen token useless from an arbitrary host.

Modes: ``off`` (no check), ``monitor`` (log misses, allow) and ``enforce`` (deny misses).
A file that becomes unreadable or empty keeps the last good list.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time

log = logging.getLogger("wpmcp.cidrs")
_RECHECK_SECONDS = 30.0


def parse(text: str) -> tuple:
    networks = []
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            networks.append(ipaddress.ip_network(entry, strict=False))
    return tuple(networks)


class Allowlist:
    def __init__(self, path: str = "", mode: str = "off") -> None:
        self.path, self.mode = path, mode
        self.networks: tuple = ()
        self._mtime = 0.0
        self._checked = 0.0
        if mode != "off":
            self._load(initial=True)

    def _load(self, initial: bool = False) -> None:
        try:
            mtime = os.stat(self.path).st_mtime
            if not initial and mtime == self._mtime:
                return
            with open(self.path, encoding="utf-8") as handle:
                networks = parse(handle.read())
            if not networks:
                raise ValueError("no networks in the file")
        except (OSError, ValueError) as exc:
            if initial and self.mode == "enforce":
                raise ValueError(f"cannot load the CIDR allowlist {self.path}: {exc}") from exc
            log.warning("keeping the previous CIDR allowlist (%s: %s)", self.path, exc)
            return
        self.networks, self._mtime = networks, mtime
        log.info("loaded %d networks from %s", len(networks), self.path)

    def _refresh(self, now: float) -> None:
        if now - self._checked >= _RECHECK_SECONDS:
            self._checked = now
            self._load()

    def contains(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in net for net in self.networks)

    def permits(self, ip: str, now: float | None = None) -> bool:
        """Whether a request from ``ip`` may proceed under the current mode."""
        if self.mode == "off":
            return True
        self._refresh(time.monotonic() if now is None else now)
        if self.contains(ip):
            return True
        if self.mode == "monitor":
            log.warning("monitor: %s is outside the CIDR allowlist", ip or "unknown")
            return True
        return False
