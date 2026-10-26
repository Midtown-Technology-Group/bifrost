"""
File content search for browser-based code editor.
Provides fast full-text search with regex support.
Platform admin resource - no org scoping.

Search queries the database directly via the ``file_index`` table for code,
modules, and any other text files persisted under ``_repo/``. Form and agent
content is no longer stored as per-UUID YAML — it lives in the manifest and is
not part of the editor search surface.
"""

import asyncio
import re
import time
import logging
from typing import List

import regex as bounded_regex
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.editor import SearchRequest, SearchResponse, SearchResult
from src.models.orm.file_index import FileIndex

logger = logging.getLogger(__name__)

# Maximum results per entity type to prevent overwhelming queries
MAX_RESULTS_PER_TYPE = 500
MAX_REGEX_PATTERN_LENGTH = 512
REGEX_SEARCH_TIMEOUT_SECONDS = 0.05
MAX_SEARCH_SECONDS = 1.0
MAX_LITERAL_QUERY_LENGTH = 4096
MAX_OVERLAY_FILES = 10_000
MAX_FILE_CHARACTERS = 1_000_000
MAX_REQUEST_CHARACTERS = 8_000_000
MAX_OUTPUT_CHARACTERS = 2_000_000
REQUEST_TIME_BUDGET_ERROR = "Search exceeded the request time budget"


def _validate_regex_pattern(pattern: str) -> None:
    """Bound compilation and preserve the supported stdlib regex syntax."""
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        raise ValueError(
            f"Regex pattern exceeds {MAX_REGEX_PATTERN_LENGTH} characters"
        )
    re.compile(pattern)


def _search_content(
    content: str,
    path: str,
    query: str,
    case_sensitive: bool,
    is_regex: bool,
    *,
    deadline: float | None = None,
    max_results: int = 10_001,
    max_output_characters: int = MAX_OUTPUT_CHARACTERS,
    compiled_pattern=None,
) -> List[SearchResult]:
    """
    Search content string for matches.

    Args:
        content: Text content to search
        path: File path for results
        query: Search query (text or regex pattern)
        case_sensitive: Whether to match case-sensitively
        is_regex: Whether query is a regex pattern

    Returns:
        List of SearchResult objects
    """
    results: List[SearchResult] = []
    if not is_regex and len(query) > MAX_LITERAL_QUERY_LENGTH:
        raise ValueError("Search query exceeds the character budget")
    if len(content) > MAX_FILE_CHARACTERS:
        raise ValueError("Search file exceeds the character budget")
    if deadline is None:
        deadline = time.monotonic() + MAX_SEARCH_SECONDS
    output_characters = 0

    try:
        if is_regex:
            _validate_regex_pattern(query)

        # Compile regex with appropriate flags. Literal search stays on the
        # stdlib engine with re.escape(); explicit regex mode uses a
        # timeout-capable engine to bound user-provided pattern execution.
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = compiled_pattern
        if regex is None:
            if is_regex:
                regex = bounded_regex.compile(query, flags)
            else:
                regex = re.compile(re.escape(query), flags)

        # Split into lines
        lines = content.split('\n')

        # Search each line
        for line_num, line in enumerate(lines, start=1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(REQUEST_TIME_BUDGET_ERROR)
            # Find all matches in this line
            if is_regex:
                matches = regex.finditer(
                    line,
                    timeout=min(REGEX_SEARCH_TIMEOUT_SECONDS, remaining),
                )
            else:
                matches = regex.finditer(line)

            for match in matches:
                if time.monotonic() >= deadline:
                    raise ValueError(REQUEST_TIME_BUDGET_ERROR)
                # Get context lines (previous and next)
                context_before = lines[line_num - 2] if line_num > 1 else None
                context_after = lines[line_num] if line_num < len(lines) else None

                output_characters += len(line) + len(context_before or "") + len(context_after or "")
                if output_characters > max_output_characters:
                    raise ValueError("Search exceeded the output character budget")
                results.append(SearchResult(
                    file_path=path,
                    line=line_num,
                    column=match.start(),
                    match_text=line,
                    context_before=context_before,
                    context_after=context_after
                ))
                if len(results) >= max_results:
                    return results

    except (bounded_regex.error, re.error) as e:
        logger.warning(f"Error searching {path}: {e}")

    return results


async def search_files_db(
    db: AsyncSession,
    request: SearchRequest,
    root_path: str = "",
    *,
    immutable_overlay: dict[str, bytes] | None = None,
    workspace_release_id: str | None = None,
    deadline: float | None = None,
) -> SearchResponse:
    """
    Search files for content matching the query using database queries.

    Searches the file_index for workflow Python code, module Python code, and
    any other indexed text content.

    Args:
        db: Database session
        request: SearchRequest with query and options
        root_path: Path prefix filter (empty = all files)

    Returns:
        SearchResponse with results and metadata

    Raises:
        ValueError: If query is invalid regex
    """
    start_time = time.monotonic()
    if deadline is None:
        deadline = start_time + MAX_SEARCH_SECONDS

    if not request.is_regex and len(request.query) > MAX_LITERAL_QUERY_LENGTH:
        raise ValueError("Search query exceeds the character budget")

    # Validate regex if enabled
    if request.is_regex:
        try:
            _validate_regex_pattern(request.query)
            flags = 0 if request.case_sensitive else re.IGNORECASE
            bounded_regex.compile(request.query, flags)
        except ValueError as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}") from e
        except (bounded_regex.error, re.error) as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}") from e

    all_results: List[SearchResult] = []
    files_searched = 0
    input_characters = 0
    output_characters = 0
    incomplete = False
    input_exhausted = False
    flags = 0 if request.case_sensitive else re.IGNORECASE
    if request.is_regex:
        compiled_pattern = bounded_regex.compile(request.query, flags)
    else:
        compiled_pattern = re.compile(re.escape(request.query), flags)

    def search_content(content: str, path: str) -> bool:
        nonlocal files_searched, input_characters, output_characters, incomplete, input_exhausted
        if input_characters + len(content) > MAX_REQUEST_CHARACTERS:
            incomplete = True
            input_exhausted = True
            return False
        input_characters += len(content)
        files_searched += 1
        if len(content) > MAX_FILE_CHARACTERS:
            # A large unrelated file must not prevent searching smaller files.
            # Its bounded database sentinel still counts toward the request cap.
            incomplete = True
            return True
        try:
            results = _search_content(
                content, path, request.query, request.case_sensitive, request.is_regex,
                deadline=deadline, max_results=request.max_results + 1 - len(all_results),
                max_output_characters=MAX_OUTPUT_CHARACTERS - output_characters,
                compiled_pattern=compiled_pattern,
            )
        except TimeoutError as exc:
            if time.monotonic() >= deadline:
                raise ValueError(REQUEST_TIME_BUDGET_ERROR) from exc
            # Omit the unfinished file rather than presenting its partial matches
            # as a complete search. Other files can still use the remaining budget.
            incomplete = True
            return True
        output_characters += sum(len(r.match_text) + len(r.context_before or "") +
                                 len(r.context_after or "") for r in results)
        all_results.extend(results)
        return True

    # Build file pattern filter if specified
    like_pattern = None
    if request.include_pattern:
        # Convert glob pattern to SQL LIKE pattern
        # e.g., "**/*.py" -> "%.py", "workflows/*.py" -> "workflows/%.py"
        like_pattern = request.include_pattern.replace("**/*", "%").replace("**", "%").replace("*", "%")

    # 1. Search all code files via file_index (workflows, modules, all Python)
    fi_conditions = [
        FileIndex.content.isnot(None),
    ]
    overlay = immutable_overlay or {}
    if len(overlay) > MAX_OVERLAY_FILES:
        raise ValueError("Search overlay exceeds the file budget")
    if overlay:
        fi_conditions.append(FileIndex.path.not_in(sorted(overlay)))
    if root_path:
        fi_conditions.append(FileIndex.path.like(f"{root_path}%"))
    if like_pattern:
        fi_conditions.append(FileIndex.path.like(like_pattern))
    code_stmt = (
        # Bound a single row before it crosses the database connection; the extra
        # character identifies an oversized file without transferring it in full.
        select(FileIndex.path, func.left(FileIndex.content, MAX_FILE_CHARACTERS + 1).label("content"))
        .where(*fi_conditions)
        .limit(MAX_RESULTS_PER_TYPE)
    )
    for path, raw in sorted(overlay.items()):
        if time.monotonic() >= deadline:
            raise ValueError(REQUEST_TIME_BUDGET_ERROR)
        if root_path and not path.startswith(root_path):
            continue
        if like_pattern:
            sql_pattern = re.escape(like_pattern).replace("%", ".*")
            if re.fullmatch(sql_pattern, path) is None:
                continue
        if len(raw) > MAX_FILE_CHARACTERS * 4:
            incomplete = True
            files_searched += 1
            if files_searched >= MAX_RESULTS_PER_TYPE:
                break
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not search_content(content, path):
            break
        if len(all_results) > request.max_results or files_searched >= MAX_RESULTS_PER_TYPE:
            break

    if (not input_exhausted and
            len(all_results) <= request.max_results and files_searched < MAX_RESULTS_PER_TYPE):
        # Fetch one content row at a time; eager fetching can materialize hundreds
        # of large files before the request budget is checked.
        try:
            async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                code_result = await db.stream(code_stmt.execution_options(yield_per=1))
                try:
                    async for row in code_result:
                        if row.content:
                            if not search_content(row.content, row.path):
                                break
                        if len(all_results) > request.max_results or files_searched >= MAX_RESULTS_PER_TYPE:
                            break
                finally:
                    await code_result.close()
        except TimeoutError as exc:
            raise ValueError(REQUEST_TIME_BUDGET_ERROR) from exc

    # Truncate results if needed
    truncated = (incomplete or len(all_results) > request.max_results or
                 files_searched >= MAX_RESULTS_PER_TYPE)
    results = all_results[:request.max_results]

    # Calculate search time
    search_time_ms = int((time.monotonic() - start_time) * 1000)

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        files_searched=files_searched,
        results=results,
        truncated=truncated,
        search_time_ms=search_time_ms,
        source_authority=("workspace-release-v1" if overlay else "repo-v1"),
        workspace_release_id=workspace_release_id,
    )
