"""Tests for AuditSink — file-backed JSONL audit trail."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.agent.audit_sink import AuditSink


@pytest.fixture
def sink_factory(tmp_path: Path):
    """Build a sink rooted at a temp dir; caller supplies a UTC date
    to pin deterministic rotation."""
    def _make(date: dt.date | None = None) -> AuditSink:
        clock = (lambda: date) if date else None
        return AuditSink(logs_dir=tmp_path, utc_date=clock)
    return _make


# ---------- basic emission ------------------------------------------------

def test_emit_creates_logs_dir_if_missing(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    assert not logs_dir.exists()
    sink = AuditSink(logs_dir=logs_dir)
    sink.emit({"event": "tool_call", "ts": "2026-04-17T00:00:00Z"})
    assert logs_dir.exists()
    assert logs_dir.is_dir()


def test_emit_writes_one_json_line(sink_factory):
    sink = sink_factory(date=dt.date(2026, 4, 17))
    sink.emit({
        "event": "tool_call",
        "ts": "2026-04-17T14:23:45Z",
        "tool_name": "search_documents",
    })
    lines = sink.log_path().read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["tool_name"] == "search_documents"


def test_emit_appends_not_overwrites(sink_factory):
    sink = sink_factory(date=dt.date(2026, 4, 17))
    sink.emit({"event": "a", "ts": "2026-04-17T10:00:00Z"})
    sink.emit({"event": "b", "ts": "2026-04-17T11:00:00Z"})
    sink.emit({"event": "c", "ts": "2026-04-17T12:00:00Z"})
    lines = sink.log_path().read_text().splitlines()
    assert len(lines) == 3
    events = [json.loads(line)["event"] for line in lines]
    assert events == ["a", "b", "c"]


def test_emit_multiple_sink_instances_share_file(sink_factory):
    """Two AuditSink instances writing the same day file should
    both append, not clobber."""
    sink_a = sink_factory(date=dt.date(2026, 4, 17))
    sink_a.emit({"event": "a", "ts": "2026-04-17T10:00:00Z"})

    sink_b = sink_factory(date=dt.date(2026, 4, 17))
    sink_b.emit({"event": "b", "ts": "2026-04-17T11:00:00Z"})

    lines = sink_a.log_path().read_text().splitlines()
    assert len(lines) == 2


# ---------- day rotation --------------------------------------------------

def test_log_path_includes_utc_date(sink_factory):
    sink = sink_factory(date=dt.date(2026, 4, 17))
    assert sink.log_path().name == "audit-2026-04-17.jsonl"


def test_different_dates_go_to_different_files(tmp_path: Path):
    sink_day_a = AuditSink(logs_dir=tmp_path,
                           utc_date=lambda: dt.date(2026, 4, 17))
    sink_day_b = AuditSink(logs_dir=tmp_path,
                           utc_date=lambda: dt.date(2026, 4, 18))

    sink_day_a.emit({"event": "monday", "ts": "2026-04-17T23:59Z"})
    sink_day_b.emit({"event": "tuesday", "ts": "2026-04-18T00:01Z"})

    file_a = tmp_path / "audit-2026-04-17.jsonl"
    file_b = tmp_path / "audit-2026-04-18.jsonl"
    assert file_a.exists() and file_b.exists()

    assert json.loads(file_a.read_text())["event"] == "monday"
    assert json.loads(file_b.read_text())["event"] == "tuesday"


# ---------- JSON safety ---------------------------------------------------

def test_emit_rejects_non_serializable_values(sink_factory):
    """Non-JSON-serializable values should raise, not silently drop."""
    sink = sink_factory(date=dt.date(2026, 4, 17))

    class _Unjsonable:
        pass

    with pytest.raises(TypeError):
        sink.emit({"event": "bad", "payload": _Unjsonable()})


def test_emit_preserves_nested_structures(sink_factory):
    sink = sink_factory(date=dt.date(2026, 4, 17))
    sink.emit({
        "event": "tool_call",
        "ts": "2026-04-17T10:00:00Z",
        "args_snapshot": {"nested": {"deep": [1, 2, 3]}},
    })
    record = json.loads(sink.log_path().read_text().strip())
    assert record["args_snapshot"]["nested"]["deep"] == [1, 2, 3]


def test_emit_compact_single_line_json(sink_factory):
    """Each event occupies exactly one line; no pretty-printed newlines
    break line-oriented parsers like jq."""
    sink = sink_factory(date=dt.date(2026, 4, 17))
    sink.emit({
        "event": "tool_call",
        "ts": "2026-04-17T10:00:00Z",
        "big_string": "line1\nline2",  # embedded newline must be escaped
    })
    lines = sink.log_path().read_text().splitlines()
    assert len(lines) == 1
    # The embedded newline survives JSON escaping, not as a raw newline
    assert "line1\\nline2" in lines[0]


# ---------- date source ---------------------------------------------------

def test_default_utc_date_source_is_usable(tmp_path: Path):
    """If the caller doesn't supply a date function, the sink uses
    the current UTC date."""
    sink = AuditSink(logs_dir=tmp_path)
    sink.emit({"event": "now", "ts": "whatever"})
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    expected = tmp_path / f"audit-{today}.jsonl"
    assert expected.exists()
