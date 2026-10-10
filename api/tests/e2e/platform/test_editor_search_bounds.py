"""Exercise bounded editor search against PostgreSQL's real streaming cursor."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.contracts.editor import SearchRequest
from src.models.orm.file_index import FileIndex
from src.services.editor.search import MAX_FILE_CHARACTERS, search_files_db

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_streaming_search_preserves_context_and_truncation(db_session):
    prefix = f"search_bounds_{uuid4()}/"
    db_session.add(FileIndex(path=prefix + "a.py", content="before\naaaa\nafter"))
    await db_session.flush()
    result = await search_files_db(db_session, SearchRequest(query="a", max_results=2), root_path=prefix)
    assert result.truncated is True
    assert result.files_searched == 1
    assert [(row.line, row.column) for row in result.results] == [(2, 0), (2, 1)]
    assert result.results[0].context_before == "before"
    assert result.results[0].context_after == "after"
    # Closing an early-exited cursor leaves the transaction usable.
    assert await db_session.scalar(select(1)) == 1


async def test_oversized_database_file_keeps_other_results_and_session_usable(db_session):
    prefix = f"search_bounds_{uuid4()}/"
    db_session.add(FileIndex(path=prefix + "large.txt", content="a" * (MAX_FILE_CHARACTERS + 100)))
    db_session.add(FileIndex(path=prefix + "note.txt", content="needle"))
    await db_session.flush()
    result = await search_files_db(db_session, SearchRequest(query="needle"), root_path=prefix)
    assert result.truncated is True
    assert [row.file_path for row in result.results] == [prefix + "note.txt"]
    assert await db_session.scalar(select(1)) == 1


async def test_regex_timeout_reports_incomplete_results_and_closes_cursor(db_session):
    prefix = f"search_bounds_{uuid4()}/"
    db_session.add(FileIndex(path=prefix + "slow.txt", content="a" * 100_000 + "!"))
    db_session.add(FileIndex(path=prefix + "match.txt", content="a"))
    await db_session.flush()
    result = await search_files_db(
        db_session, SearchRequest(query="(a+)+$", is_regex=True), root_path=prefix,
    )
    assert result.truncated is True
    assert result.files_searched == 2
    assert [row.file_path for row in result.results] == [prefix + "match.txt"]
    assert await db_session.scalar(select(1)) == 1
