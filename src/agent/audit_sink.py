"""File-backed JSONL audit sink.

Each event is a single JSON line appended to
`logs/audit-YYYY-MM-DD.jsonl` (UTC date). The sink is append-only; it
never seeks, truncates, or rewrites existing content.

This is a separate persistence layer from `src/audit.py` (which uses
Python's logging module to stream to stderr or a configured handler).
The two layers coexist during Phase 4; consolidation is deferred.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable


class AuditSink:
    """Append-only JSONL audit sink with day-rotated file naming.

    Parameters
    ----------
    logs_dir
        Directory where `audit-YYYY-MM-DD.jsonl` files are written.
        Created lazily if it doesn't exist.
    utc_date
        Optional callable returning the UTC date to use for file
        naming. Defaults to "current UTC date at emission time".
        Tests pin this to a fixed date for determinism.
    """

    def __init__(
        self,
        *,
        logs_dir: Path,
        utc_date: Callable[[], dt.date] | None = None,
    ) -> None:
        self._logs_dir = Path(logs_dir)
        self._date_source = utc_date or _current_utc_date

    def log_path(self) -> Path:
        """Return the path to the current day's audit file."""
        date = self._date_source()
        return self._logs_dir / f"audit-{date.isoformat()}.jsonl"

    def emit(self, event: dict[str, Any]) -> None:
        """Serialize `event` to a single JSON line and append to the
        day file. Raises `TypeError` on non-JSON-serializable values
        (no silent drops — calling code is expected to normalize
        inputs before emission)."""
        line = json.dumps(event, separators=(",", ":"))
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path().open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")


def _current_utc_date() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
