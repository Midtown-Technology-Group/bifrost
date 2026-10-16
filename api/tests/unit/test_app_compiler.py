import pytest
from src.services import app_compiler
from src.services.app_compiler import (
    AppCompilerService,
    AppTailwindService,
    TailwindGenerationError,
)


@pytest.fixture
def compiler():
    return AppCompilerService()


class TestAppCompilerService:
    @pytest.mark.asyncio
    async def test_compile_batch_empty_returns_empty(self, compiler):
        assert await compiler.compile_batch([]) == []

    @pytest.mark.asyncio
    async def test_compile_simple_component(self, compiler):
        source = 'export default function Page() { return <div>Hello</div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert result.compiled is not None
        assert "__defaultExport__" in result.compiled
        assert result.error is None

    @pytest.mark.asyncio
    async def test_compile_with_bifrost_imports(self, compiler):
        source = '''import { Button, useState } from "bifrost";
export default function Page() {
  const [count, setCount] = useState(0);
  return <Button onClick={() => setCount(count + 1)}>{count}</Button>;
}'''
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert "var {" in result.compiled or "var { " in result.compiled

    @pytest.mark.asyncio
    async def test_compile_syntax_error(self, compiler):
        source = 'export default function Page() { return <div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_compile_batch(self, compiler):
        files = [
            {"path": "pages/index.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/about.tsx", "source": "export default function B() { return <div>B</div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_compile_batch_partial_failure(self, compiler):
        files = [
            {"path": "pages/good.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/bad.tsx", "source": "export default function B() { return <div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert results[0].success is True
        assert results[1].success is False

    @pytest.mark.asyncio
    async def test_compile_batch_reports_subprocess_exit_error(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=1,
                stdout=b"",
                stderr=b"babel unavailable",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
            {"path": "pages/b.tsx", "source": "export default 2"},
        ])

        assert [result.path for result in results] == ["pages/a.tsx", "pages/b.tsx"]
        assert all(result.success is False for result in results)
        assert {result.error for result in results} == {"babel unavailable"}

    @pytest.mark.asyncio
    async def test_compile_batch_reports_protocol_error(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"error": "invalid compiler response"}',
                stderr=b"",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
        ])

        assert results[0].success is False
        assert results[0].error == "invalid compiler response"
        assert results[0].retryable is True

    @pytest.mark.asyncio
    async def test_compile_batch_rejects_incomplete_response(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"results": []}',
                stderr=b"",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        result = await compiler.compile_file("export default 1", "pages/a.tsx")

        assert result.success is False
        assert result.error == "Compiler returned an incomplete response"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_compile_batch_handles_missing_node(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise FileNotFoundError("node")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        result = await compiler.compile_file("export default 1", "pages/a.tsx")

        assert result.success is False
        assert result.error == "Node.js not available"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_compile_batch_rejects_too_many_files_before_spawn(
        self, compiler, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("oversized batch must not spawn Node")

        monkeypatch.setattr(app_compiler, "COMPILER_MAX_FILES", 1)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
            {"path": "pages/b.tsx", "source": "export default 2"},
        ])

        assert len(results) == 2
        assert {result.error for result in results} == {
            "Compilation batch exceeds the 1-file limit"
        }
        assert all(result.retryable is False for result in results)

    @pytest.mark.asyncio
    async def test_compile_batch_rejects_non_text_source(self, compiler):
        result = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": b"not text"},
        ])

        assert result[0].error == "Compiler source for 'pages/a.tsx' must be text"
        assert result[0].retryable is False

    @pytest.mark.asyncio
    async def test_compile_batch_rejects_oversized_source_before_spawn(
        self, compiler, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("oversized source must not spawn Node")

        monkeypatch.setattr(app_compiler, "COMPILER_MAX_SOURCE_BYTES", 4)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "12345"},
        ])

        assert results[0].success is False
        assert results[0].error == (
            "Compiler source for 'pages/a.tsx' exceeds the 4-byte limit"
        )
        assert results[0].retryable is False

    @pytest.mark.asyncio
    async def test_compile_batch_measures_source_limit_in_utf8_bytes(
        self, compiler, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("oversized source must not spawn Node")

        monkeypatch.setattr(app_compiler, "COMPILER_MAX_SOURCE_BYTES", 3)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        result = await compiler.compile_file("éé", "pages/a.tsx")

        assert result.success is False
        assert "exceeds the 3-byte limit" in (result.error or "")

    @pytest.mark.asyncio
    async def test_compile_batch_rejects_oversized_batch_before_spawn(
        self, compiler, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("oversized batch must not spawn Node")

        monkeypatch.setattr(app_compiler, "COMPILER_MAX_BATCH_SOURCE_BYTES", 4)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "123"},
            {"path": "pages/b.tsx", "source": "456"},
        ])

        assert len(results) == 2
        assert all(result.success is False for result in results)
        assert {result.error for result in results} == {
            "Compilation batch source exceeds the 4-byte limit"
        }

    @pytest.mark.asyncio
    async def test_compile_batch_times_out_and_reaps_node(
        self, compiler, monkeypatch
    ):
        process = _HangingProcess()

        async def create_subprocess_exec(*args, **_kwargs):
            assert args[:2] == (
                "node",
                f"--max-old-space-size={app_compiler.COMPILER_MAX_OLD_SPACE_MB}",
            )
            return process

        monkeypatch.setattr(app_compiler, "COMPILER_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
        ])

        assert process.killed is True
        assert process.waited is True
        assert results[0].success is False
        assert results[0].error == "Compilation exceeded the 0.001-second limit"
        assert results[0].retryable is True

    @pytest.mark.asyncio
    async def test_compile_batch_bounds_capacity_wait(
        self, compiler, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("capacity timeout must not spawn Node")

        monkeypatch.setattr(
            app_compiler,
            "_COMPILER_SLOTS",
            app_compiler.asyncio.Semaphore(0),
        )
        monkeypatch.setattr(app_compiler, "COMPILER_QUEUE_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
        ])

        assert results[0].success is False
        assert results[0].error == (
            "Compiler capacity unavailable after 0.001 seconds"
        )
        assert results[0].retryable is True

    @pytest.mark.asyncio
    async def test_compile_batch_cancellation_reaps_node(self, compiler, monkeypatch):
        process = _FailingProcess(app_compiler.asyncio.CancelledError())

        async def create_subprocess_exec(*_args, **_kwargs):
            return process

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(app_compiler.asyncio.CancelledError):
            await compiler.compile_file("export default 1", "pages/a.tsx")

        assert process.killed is True
        assert process.waited is True


class TestAppTailwindService:
    def test_extract_candidates_keeps_tailwind_arbitrary_values(self):
        candidates = AppTailwindService.extract_candidates([
            'const c = "flex lg:grid-cols-[minmax(0,1fr)_360px] bg-[rgb(0,0,0)]";',
            'const ignored = "123 !!!";',
        ])

        assert "flex" in candidates
        assert "lg:grid-cols-[minmax(0,1fr)_360px]" in candidates
        assert "bg-[rgb(0,0,0)]" in candidates
        assert "123" not in candidates

    def test_extract_candidates_bounds_source_bytes_and_unique_candidates(
        self, monkeypatch
    ):
        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_SOURCE_BYTES", 3)
        with pytest.raises(ValueError, match="3-byte limit"):
            AppTailwindService.extract_candidates(['const c = "flex"'])

        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_SOURCE_BYTES", 1000)
        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_CANDIDATES", 1)
        with pytest.raises(ValueError, match="1-candidate limit"):
            AppTailwindService.extract_candidates(['const c = "flex grid"'])

    @pytest.mark.asyncio
    async def test_generate_css_skips_empty_candidates(self, monkeypatch):
        async def fail_invoke(_payload):
            raise AssertionError("should not invoke tailwind without candidates")

        monkeypatch.setattr(AppTailwindService, "_invoke", fail_invoke)

        assert await AppTailwindService.generate_css(['const label = "123 !!!";']) is None

    @pytest.mark.asyncio
    async def test_generate_css_rejects_non_text_source(self):
        assert await AppTailwindService.generate_css([b"not text"]) is None

    @pytest.mark.asyncio
    async def test_generate_css_invokes_tailwind_for_candidates(self, monkeypatch):
        async def fake_invoke(payload):
            assert payload == {"candidates": ["flex"]}
            return ".flex{}"

        monkeypatch.setattr(AppTailwindService, "_invoke", fake_invoke)

        assert await AppTailwindService.generate_css(['const c = "flex"']) == ".flex{}"

    @pytest.mark.asyncio
    async def test_generate_css_pipeline_invokes_for_user_css_without_candidates(
        self, monkeypatch
    ):
        calls = []

        async def fake_invoke(payload):
            calls.append(payload)
            return "/* css */"

        monkeypatch.setattr(AppTailwindService, "_invoke", fake_invoke)

        assert await AppTailwindService.generate_css_pipeline(
            ['const label = "123 !!!";'],
            [("theme.css", "@theme { --color-brand: red; }")],
        ) == "/* css */"
        assert calls == [
            {
                "candidates": [],
                "user_css": [
                    {"path": "theme.css", "content": "@theme { --color-brand: red; }"}
                ],
            }
        ]

        assert await AppTailwindService.generate_css_pipeline([], []) is None

    @pytest.mark.asyncio
    async def test_generate_css_pipeline_rejects_oversized_sources(
        self, monkeypatch
    ):
        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_SOURCE_BYTES", 1)

        with pytest.raises(TailwindGenerationError, match="1-byte limit"):
            await AppTailwindService.generate_css_pipeline(["large"], [])

    @pytest.mark.asyncio
    async def test_tailwind_invoke_handles_process_and_payload_errors(self, monkeypatch):
        async def nonzero_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=1, stdout=b"", stderr=b"tailwind failed")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            nonzero_subprocess,
        )
        with pytest.raises(TailwindGenerationError, match="tailwind failed"):
            await AppTailwindService._invoke({"candidates": ["flex"]})

        async def error_payload_subprocess(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"error": "bad css"}',
                stderr=b"",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            error_payload_subprocess,
        )
        with pytest.raises(TailwindGenerationError, match="bad css"):
            await AppTailwindService._invoke({"candidates": ["grid"]})

    @pytest.mark.asyncio
    async def test_tailwind_invoke_returns_css_or_none(self, monkeypatch):
        async def css_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=0, stdout=b'{"css": ".flex{}"}', stderr=b"")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            css_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["flex"]}) == ".flex{}"

        async def empty_css_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=0, stdout=b'{"css": ""}', stderr=b"")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            empty_css_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["hidden"]}) is None

    @pytest.mark.asyncio
    async def test_tailwind_invoke_rejects_oversized_payload_before_spawn(
        self, monkeypatch
    ):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("oversized payload must not spawn Node")

        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_PAYLOAD_BYTES", 8)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(TailwindGenerationError, match="payload exceeds"):
            await AppTailwindService._invoke({"candidates": ["flex"]})

    @pytest.mark.asyncio
    async def test_tailwind_invoke_bounds_capacity_wait(self, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise AssertionError("capacity timeout must not spawn Node")

        monkeypatch.setattr(
            app_compiler,
            "_COMPILER_SLOTS",
            app_compiler.asyncio.Semaphore(0),
        )
        monkeypatch.setattr(app_compiler, "COMPILER_QUEUE_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(
            TailwindGenerationError, match="capacity unavailable"
        ) as error:
            await AppTailwindService._invoke({"candidates": ["flex"]})
        assert error.value.retryable is True

    @pytest.mark.asyncio
    async def test_tailwind_invoke_rejects_oversized_output(self, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"css": ".flex{}"}',
                stderr=b"",
            )

        monkeypatch.setattr(app_compiler, "TAILWIND_MAX_OUTPUT_BYTES", 4)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(TailwindGenerationError, match="output exceeded"):
            await AppTailwindService._invoke({"candidates": ["flex"]})

    @pytest.mark.asyncio
    async def test_tailwind_invoke_times_out_and_reaps_node(self, monkeypatch):
        process = _HangingProcess()

        async def create_subprocess_exec(*args, **_kwargs):
            assert args[:2] == (
                "node",
                f"--max-old-space-size={app_compiler.COMPILER_MAX_OLD_SPACE_MB}",
            )
            assert args[-2:] == (
                str(app_compiler.TAILWIND_MAX_OUTPUT_BYTES),
                str(app_compiler.TAILWIND_MAX_PAYLOAD_BYTES),
            )
            return process

        monkeypatch.setattr(app_compiler, "COMPILER_TIMEOUT_SECONDS", 0.001)
        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(TailwindGenerationError, match="exceeded") as error:
            await AppTailwindService._invoke({"candidates": ["flex"]})
        assert error.value.retryable is True
        assert process.killed is True
        assert process.waited is True

    @pytest.mark.asyncio
    async def test_tailwind_invoke_cancellation_reaps_node(self, monkeypatch):
        process = _FailingProcess(app_compiler.asyncio.CancelledError())

        async def create_subprocess_exec(*_args, **_kwargs):
            return process

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        with pytest.raises(app_compiler.asyncio.CancelledError):
            await AppTailwindService._invoke({"candidates": ["flex"]})

        assert process.killed is True
        assert process.waited is True

    @pytest.mark.asyncio
    async def test_tailwind_invoke_handles_spawn_failures(self, monkeypatch):
        async def missing_node(*_args, **_kwargs):
            raise FileNotFoundError("node")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            missing_node,
        )
        with pytest.raises(
            TailwindGenerationError, match="Node.js not available"
        ) as error:
            await AppTailwindService._invoke({"candidates": ["flex"]})
        assert error.value.retryable is True

        async def broken_spawn(*_args, **_kwargs):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            broken_spawn,
        )
        with pytest.raises(TailwindGenerationError, match="spawn failed") as error:
            await AppTailwindService._invoke({"candidates": ["flex"]})
        assert error.value.retryable is True


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.input = None

    async def communicate(self, input=None):
        self.input = input
        return self._stdout, self._stderr


class _HangingProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        del input
        await app_compiler.asyncio.Event().wait()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


class _FailingProcess(_HangingProcess):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def communicate(self, input=None):
        del input
        raise self.error
