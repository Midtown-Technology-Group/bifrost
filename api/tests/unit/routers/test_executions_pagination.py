"""Keyset-cursor tests for the execution history endpoint.

History pagination must be keyset-based ("rows older than the last one I
saw"), not offset-based: on a busy instance new executions land constantly,
and an offset token re-serves the previous page's tail — users paginating
into the past see today's rows (and a "Today" header) on every page.
"""

import base64
import json
from datetime import datetime, timezone
from uuid import uuid4


def _legacy_v1_cursor(row_id, started_at=None) -> str:
    payload = {
        "v": 1,
        "s": started_at.isoformat() if started_at else None,
        "i": str(row_id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def test_cursor_round_trips_timeline_timestamp() -> None:
    from shared.sdk_execution_reads import (
        decode_history_cursor,
        encode_history_cursor,
    )

    timeline_at = datetime(2026, 7, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)
    row_id = uuid4()

    token = encode_history_cursor(timeline_at, row_id)
    decoded = decode_history_cursor(token)

    assert decoded == (timeline_at, row_id)
    assert json.loads(base64.urlsafe_b64decode(token.encode()))["v"] == 2


def test_v1_cursors_decode_as_an_explicit_restart() -> None:
    """Old started_at cursors must not be interpreted as v2 timelines."""
    from shared.sdk_execution_reads import decode_history_cursor
    from src.routers.executions import _decode_history_cursor

    row_id = uuid4()
    null_token = _legacy_v1_cursor(row_id)
    nonnull_token = _legacy_v1_cursor(
        row_id, datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)
    )

    assert decode_history_cursor(null_token) == (None, row_id)
    assert decode_history_cursor(nonnull_token) == (None, row_id)
    assert _decode_history_cursor(null_token) == (None, row_id)
    assert _decode_history_cursor(nonnull_token) == (None, row_id)


def test_v1_cursor_rejects_a_malformed_nonnull_timestamp() -> None:
    """A legacy restart token must still validate the fields it carried."""
    from shared.sdk_execution_reads import decode_history_cursor
    from src.routers.executions import _decode_history_cursor

    token = base64.urlsafe_b64encode(
        json.dumps({"v": 1, "s": "not-a-timestamp", "i": str(uuid4())}).encode()
    ).decode()

    assert decode_history_cursor(token) is None
    assert _decode_history_cursor(token) is None


def test_cursor_is_opaque_not_a_bare_offset() -> None:
    """A numeric token is the legacy offset format, not a keyset cursor."""
    from shared.sdk_execution_reads import encode_history_cursor

    token = encode_history_cursor(None, uuid4())
    assert not token.isdigit()


def test_decode_rejects_garbage_and_legacy_offsets() -> None:
    """Legacy numeric offsets and junk must decode to None (caller falls back)."""
    from shared.sdk_execution_reads import decode_history_cursor

    assert decode_history_cursor("25") is None
    assert decode_history_cursor("not-a-token") is None
    assert decode_history_cursor("") is None
