"""Local throttle bookkeeping — security rule 6.

fragdenstaat.de enforces ``request_throttle`` for both the web form and the API
(``MakeRequestThrottle`` -> ``check_throttle``, fragdenstaat_de/settings/base.py:734):

    5 requests / 5 minutes, 6 / 6 hours, 10 / 24 hours, 20 / 7 days

Exceeding it yields HTTP 429 and an e-mail to the operators. According to the terms of
use (B.1.4) *attempting to circumvent* the limit locks the account for a month. So this
module brakes before the server has to: on a violation we abort with a clear message and
we never retry.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .errors import FdsMcpError
from .rules import MESSAGE_THROTTLE, REQUEST_THROTTLE


class ThrottleExceeded(FdsMcpError):
    """The local ledger says another submission would break a published rate limit."""


@dataclass
class ThrottleLedger:
    """A tiny append-only ledger of local submissions, kept as JSON."""

    path: Path | None = None
    limits: tuple[tuple[int, int], ...] = tuple(REQUEST_THROTTLE)
    kind: str = "request"

    def _file(self) -> Path:
        return self.path or config.throttle_path()

    # ---------------- storage ----------------

    def _load(self) -> dict[str, list[float]]:
        path = self._file()
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            return {}
        return {k: [float(t) for t in v] for k, v in data.items() if isinstance(v, list)}

    def _store(self, data: dict[str, list[float]]) -> None:
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def timestamps(self, *, now: float | None = None) -> list[float]:
        now = time.time() if now is None else now
        horizon = max(window for _count, window in self.limits)
        return sorted(t for t in self._load().get(self.kind, []) if t > now - horizon)

    # ---------------- the gate ----------------

    def violations(self, *, now: float | None = None) -> list[str]:
        """Return a message per limit that a further submission would break."""
        now = time.time() if now is None else now
        stamps = self.timestamps(now=now)
        out: list[str] = []
        for count, window in self.limits:
            recent = [t for t in stamps if t > now - window]
            if len(recent) + 1 > count:
                oldest = min(recent)
                wait = int(oldest + window - now)
                out.append(
                    f"{len(recent)} of max. {count} {self.kind}s in the last "
                    f"{_human(window)} — the next one would exceed the limit. "
                    f"Earliest possible in {_human(max(wait, 0))}."
                )
        return out

    def check(self, *, now: float | None = None) -> None:
        problems = self.violations(now=now)
        if problems:
            raise ThrottleExceeded(
                "fragdenstaat.de rate limit would be exceeded — aborting instead of "
                "retrying (terms of use B.1.4: circumvention attempts lock the account "
                "for a month).\n  - " + "\n  - ".join(problems)
            )

    def record(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        data = self._load()
        stamps = data.get(self.kind, [])
        stamps.append(now)
        horizon = max(window for _count, window in self.limits)
        data[self.kind] = sorted(t for t in stamps if t > now - horizon)
        self._store(data)

    def status(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        stamps = self.timestamps(now=now)
        return {
            "kind": self.kind,
            "ledger": str(self._file()),
            "windows": [
                {
                    "limit": count,
                    "window": _human(window),
                    "used": len([t for t in stamps if t > now - window]),
                }
                for count, window in self.limits
            ],
            "would_block": self.violations(now=now),
        }


def message_ledger(path: Path | None = None) -> ThrottleLedger:
    return ThrottleLedger(path=path, limits=tuple(MESSAGE_THROTTLE), kind="message")


def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds >= 60:
        return f"{seconds // 60}min"
    return f"{seconds}s"
