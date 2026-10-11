"""
Server-side TSX/JSX compiler and Tailwind CSS generator for Bifrost app files.

Uses Node.js subprocesses:
- @babel/standalone to compile app source files (same pipeline as client)
- @tailwindcss/node to generate per-app Tailwind CSS from class candidates
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

COMPILE_SCRIPT = Path(__file__).parent / "compile.js"
TAILWIND_SCRIPT = Path(__file__).parent / "tailwind.js"

# App compilation accepts source from editor, validation, preview-sync, and MCP
# paths. Keep one set of process limits here so every caller is protected.
COMPILER_MAX_FILES = 256
COMPILER_MAX_SOURCE_BYTES = 1024 * 1024
COMPILER_MAX_BATCH_SOURCE_BYTES = 8 * 1024 * 1024
COMPILER_MAX_CONCURRENCY = 2
COMPILER_QUEUE_TIMEOUT_SECONDS = 5.0
COMPILER_TIMEOUT_SECONDS = 30.0
COMPILER_MAX_OLD_SPACE_MB = 256

TAILWIND_MAX_SOURCE_BYTES = 8 * 1024 * 1024
TAILWIND_MAX_CANDIDATES = 10_000
TAILWIND_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
TAILWIND_MAX_OUTPUT_BYTES = 8 * 1024 * 1024

_COMPILER_SLOTS = asyncio.Semaphore(COMPILER_MAX_CONCURRENCY)


@dataclass
class CompileResult:
    """Result of compiling a single file."""
    path: str
    success: bool
    compiled: str | None = None
    error: str | None = None
    default_export: str | None = None
    named_exports: list[str] = field(default_factory=list)
    retryable: bool = False


class AppCompilerService:
    """Compile TSX/JSX source files via Node.js subprocess."""

    @staticmethod
    def _failure_results(
        files: list[dict], error: str, *, retryable: bool = False
    ) -> list[CompileResult]:
        """Return one stable failure result for each requested input file."""
        return [
            CompileResult(
                path=str(item.get("path", "")),
                success=False,
                error=error,
                retryable=retryable,
            )
            for item in files
        ]

    @staticmethod
    def _validate_batch(files: list[dict]) -> str | None:
        """Reject compiler work that exceeds the shared process input bounds."""
        if len(files) > COMPILER_MAX_FILES:
            return f"Compilation batch exceeds the {COMPILER_MAX_FILES}-file limit"

        total_source_bytes = 0
        for item in files:
            path = str(item.get("path", ""))
            source = item.get("source")
            if not isinstance(source, str):
                return f"Compiler source for '{path}' must be text"
            source_bytes = len(source.encode("utf-8"))
            if source_bytes > COMPILER_MAX_SOURCE_BYTES:
                return (
                    f"Compiler source for '{path}' exceeds the "
                    f"{COMPILER_MAX_SOURCE_BYTES}-byte limit"
                )
            total_source_bytes += source_bytes

        if total_source_bytes > COMPILER_MAX_BATCH_SOURCE_BYTES:
            return (
                "Compilation batch source exceeds the "
                f"{COMPILER_MAX_BATCH_SOURCE_BYTES}-byte limit"
            )
        return None

    async def compile_file(self, source: str, path: str = "component.tsx") -> CompileResult:
        """Compile a single file."""
        results = await self.compile_batch([{"path": path, "source": source}])
        return results[0]

    async def compile_batch(self, files: list[dict]) -> list[CompileResult]:
        """Compile multiple files in a single Node.js invocation."""
        if not files:
            return []

        validation_error = self._validate_batch(files)
        if validation_error:
            return self._failure_results(files, validation_error)

        input_data = json.dumps({"files": files})

        try:
            try:
                await asyncio.wait_for(
                    _COMPILER_SLOTS.acquire(),
                    timeout=COMPILER_QUEUE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                error = (
                    "Compiler capacity unavailable after "
                    f"{COMPILER_QUEUE_TIMEOUT_SECONDS:g} seconds"
                )
                logger.warning(error)
                return self._failure_results(files, error, retryable=True)

            try:
                proc = await asyncio.create_subprocess_exec(
                    "node",
                    f"--max-old-space-size={COMPILER_MAX_OLD_SPACE_MB}",
                    str(COMPILE_SCRIPT),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=input_data.encode()),
                        timeout=COMPILER_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    error = (
                        "Compilation exceeded the "
                        f"{COMPILER_TIMEOUT_SECONDS:g}-second limit"
                    )
                    logger.warning(error)
                    return self._failure_results(files, error, retryable=True)
                except asyncio.CancelledError:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    raise
                except Exception:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    raise
            finally:
                _COMPILER_SLOTS.release()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip() or "Node process exited with error"
                logger.error(f"Compiler process failed: {error_msg}")
                return self._failure_results(files, error_msg, retryable=True)

            output = json.loads(stdout.decode())

            if "error" in output:
                return self._failure_results(files, output["error"], retryable=True)

            output_results = output.get("results")
            expected_paths = [str(item.get("path", "")) for item in files]
            if (
                not isinstance(output_results, list)
                or [item.get("path") for item in output_results] != expected_paths
            ):
                return self._failure_results(
                    files,
                    "Compiler returned an incomplete response",
                    retryable=True,
                )

            results = []
            for item in output_results:
                if item.get("error"):
                    results.append(CompileResult(
                        path=item["path"],
                        success=False,
                        error=item["error"],
                    ))
                else:
                    results.append(CompileResult(
                        path=item["path"],
                        success=True,
                        compiled=item["compiled"],
                        default_export=item.get("defaultExport"),
                        named_exports=item.get("namedExports", []),
                    ))
            return results

        except FileNotFoundError:
            logger.error("Node.js not found — cannot compile app files")
            return self._failure_results(files, "Node.js not available", retryable=True)
        except Exception as e:
            logger.exception("Compilation failed")
            return self._failure_results(files, str(e), retryable=True)


# Extract all string literals from compiled JS output, then split into tokens.
# Over-extraction is fine — Tailwind silently ignores unknown candidates.
# This is intentionally broad to handle both JSX (className="...") and compiled
# output (className: "...") without fragile pattern matching.
_STRING_LITERAL = re.compile(r'"([^"]{1,500})"')
# Split tokens on whitespace ONLY. Splitting on commas breaks Tailwind v4
# arbitrary values that legitimately contain commas — e.g.
# `lg:grid-cols-[minmax(0,1fr)_360px]` or `bg-[rgb(0,0,0)]`.
_TOKEN_SPLIT = re.compile(r"\s+")
# Tailwind classes contain hyphens, brackets, parens, colons, slashes, commas.
# Tailwind v4 arbitrary values heavily use `()` (var(), minmax(), calc()) and
# `,` (rgb commas, minmax args), so both must pass the candidate filter.
# Single-word utilities (flex, grid, hidden, etc.) also need to pass through.
_LOOKS_LIKE_CLASS = re.compile(
    r"^!?-?[a-z][a-z0-9:\-/\[\](),.=#%_*>~&+]*$",
    re.IGNORECASE,
)


class AppTailwindService:
    """Generate Tailwind CSS for app source files via @tailwindcss/node."""

    @staticmethod
    def extract_candidates(sources: list[str]) -> list[str]:
        """Extract Tailwind class candidates from source strings.

        Scans all string literals and splits into whitespace-separated tokens.
        Over-extraction is harmless — Tailwind ignores unknown candidates.
        """
        candidates: set[str] = set()
        source_bytes = 0
        for source in sources:
            if not isinstance(source, str):
                raise TypeError("Tailwind sources must be text")
            source_bytes += len(source.encode("utf-8"))
            if source_bytes > TAILWIND_MAX_SOURCE_BYTES:
                raise ValueError(
                    "Tailwind sources exceed the "
                    f"{TAILWIND_MAX_SOURCE_BYTES}-byte limit"
                )
            for match in _STRING_LITERAL.finditer(source):
                for token in _TOKEN_SPLIT.split(match.group(1)):
                    token = token.strip()
                    if token and _LOOKS_LIKE_CLASS.match(token):
                        candidates.add(token)
                        if len(candidates) > TAILWIND_MAX_CANDIDATES:
                            raise ValueError(
                                "Tailwind candidates exceed the "
                                f"{TAILWIND_MAX_CANDIDATES}-candidate limit"
                            )
        return sorted(candidates)

    @staticmethod
    async def generate_css(sources: list[str]) -> str | None:
        """Extract candidates from sources and generate Tailwind CSS.

        Returns the generated CSS string, or None on failure.

        Candidates-only mode: produces utility CSS for the class names
        found in `sources`. Does NOT process user CSS files — see
        `generate_css_pipeline` for the pipeline mode that supports
        @apply / @layer / @theme.
        """
        try:
            candidates = AppTailwindService.extract_candidates(sources)
        except (TypeError, ValueError) as error:
            logger.warning("Tailwind CSS generation rejected: %s", error)
            return None
        if not candidates:
            return None

        return await AppTailwindService._invoke({"candidates": candidates})

    @staticmethod
    async def generate_css_pipeline(
        code_sources: list[str],
        user_css: list[tuple[str, str]],
        config_path: str | None = None,
    ) -> str | None:
        """Run the full Tailwind v4 pipeline against app source.

        Args:
            code_sources: contents of .tsx/.ts/.jsx/.js files, scanned for
                Tailwind class candidates.
            user_css: list of (filename, content) tuples for the app's
                .css files. Concatenated into the Tailwind input so
                @apply / @layer / @theme directives in user CSS are
                processed against the utility layer.
            config_path: ignored for compatibility. Per-app Tailwind config
                files are not loaded because they execute server-side JavaScript.

        Returns the generated CSS string (utilities + processed user CSS),
        or None on failure.
        """
        try:
            candidates = AppTailwindService.extract_candidates(code_sources)
        except (TypeError, ValueError) as error:
            logger.warning("Tailwind CSS generation rejected: %s", error)
            return None
        payload: dict[str, object] = {
            "candidates": candidates,
            "user_css": [
                {"path": p, "content": c} for p, c in user_css
            ],
        }
        # Skip the subprocess only when there's literally nothing to do —
        # no candidates AND no user CSS. (User CSS alone with no candidates
        # is still a real input; e.g. a stylesheet declaring CSS variables.)
        if not candidates and not user_css:
            return None

        return await AppTailwindService._invoke(payload)

    @staticmethod
    async def _invoke(payload: dict[str, object]) -> str | None:
        """Send a payload to tailwind.js, return the css or None on error."""
        input_data = json.dumps(payload)
        input_bytes = input_data.encode()
        if len(input_bytes) > TAILWIND_MAX_PAYLOAD_BYTES:
            logger.warning(
                "Tailwind payload exceeds the %d-byte limit",
                TAILWIND_MAX_PAYLOAD_BYTES,
            )
            return None

        try:
            try:
                await asyncio.wait_for(
                    _COMPILER_SLOTS.acquire(),
                    timeout=COMPILER_QUEUE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "Tailwind capacity unavailable after %g seconds",
                    COMPILER_QUEUE_TIMEOUT_SECONDS,
                )
                return None

            try:
                proc = await asyncio.create_subprocess_exec(
                    "node",
                    f"--max-old-space-size={COMPILER_MAX_OLD_SPACE_MB}",
                    str(TAILWIND_SCRIPT),
                    str(TAILWIND_MAX_OUTPUT_BYTES),
                    str(TAILWIND_MAX_PAYLOAD_BYTES),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=input_bytes),
                        timeout=COMPILER_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    logger.warning(
                        "Tailwind CSS generation exceeded the %g-second limit",
                        COMPILER_TIMEOUT_SECONDS,
                    )
                    return None
                except asyncio.CancelledError:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    raise
                except Exception:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    raise
            finally:
                _COMPILER_SLOTS.release()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip() or "tailwind.js exited with error"
                logger.error(f"Tailwind CSS generation failed: {error_msg}")
                return None

            if len(stdout) > TAILWIND_MAX_OUTPUT_BYTES:
                logger.error("Tailwind CSS output exceeded its byte limit")
                return None

            output = json.loads(stdout.decode())
            if output.get("error"):
                logger.error(f"Tailwind CSS generation error: {output['error']}")
                return None

            css = output.get("css", "")
            return css if css else None

        except FileNotFoundError:
            logger.error("Node.js not found — cannot generate Tailwind CSS")
            return None
        except Exception:
            logger.exception("Tailwind CSS generation failed")
            return None
