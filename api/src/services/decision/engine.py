"""
DecisionEngine

Provider-neutral decision surface. Callers invoke the boolean / choice /
score / rank primitives; the engine validates inputs and provider outputs,
measures latency, hashes the inputs, and stamps provenance metadata. The
mode gate (off / shadow / enforce) decides whether the surface exists and
whether results carry authority.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

from src.services.decision.base import (
    DecisionEngineDisabled,
    DecisionMeta,
    DecisionMode,
    DecisionProvider,
    DecisionResult,
    DecisionValidationError,
    ProviderDecision,
)

_VALID_MODES: tuple[DecisionMode, ...] = ("off", "shadow", "enforce")


def compute_input_hash(
    question: str,
    options: Sequence[str] | None,
    context: Mapping[str, Any],
) -> str:
    """Stable SHA-256 over the canonical decision inputs."""
    payload = json.dumps(
        {
            "question": question,
            "options": list(options) if options is not None else None,
            "context": context,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DecisionEngine:
    """Validated, mode-gated facade over a DecisionProvider."""

    def __init__(self, provider: DecisionProvider, *, mode: DecisionMode):
        if mode not in _VALID_MODES:
            raise DecisionValidationError(f"unknown decision mode: {mode!r}")
        self._provider = provider
        self._mode: DecisionMode = mode

    @property
    def mode(self) -> DecisionMode:
        return self._mode

    @property
    def provider(self) -> DecisionProvider:
        return self._provider

    async def boolean(
        self,
        question: str,
        context: Mapping[str, Any] | None = None,
    ) -> DecisionResult[bool]:
        self._require_enabled()
        ctx = context if context is not None else {}
        _validate_question(question)
        raw, elapsed_ms = await self._call(self._provider.boolean(question, ctx))
        if not isinstance(raw.value, bool):
            raise DecisionValidationError(
                f"provider returned non-boolean value: {raw.value!r}"
            )
        return self._finish(raw, question, None, ctx, elapsed_ms)

    async def choice(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any] | None = None,
    ) -> DecisionResult[str]:
        self._require_enabled()
        ctx = context if context is not None else {}
        _validate_question(question)
        allowed = _validate_options(options)
        raw, elapsed_ms = await self._call(
            self._provider.choice(question, allowed, ctx)
        )
        if raw.value not in allowed:
            raise DecisionValidationError(
                f"provider returned option outside the allowed set: {raw.value!r}"
            )
        return self._finish(raw, question, allowed, ctx, elapsed_ms)

    async def score(
        self,
        question: str,
        context: Mapping[str, Any] | None = None,
    ) -> DecisionResult[float]:
        self._require_enabled()
        ctx = context if context is not None else {}
        _validate_question(question)
        raw, elapsed_ms = await self._call(self._provider.score(question, ctx))
        value = raw.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionValidationError(
                f"provider returned non-numeric score: {value!r}"
            )
        if not 0.0 <= float(value) <= 1.0:
            raise DecisionValidationError(
                f"provider returned score outside [0.0, 1.0]: {value!r}"
            )
        return self._finish(raw, question, None, ctx, elapsed_ms)

    async def rank(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any] | None = None,
    ) -> DecisionResult[list[str]]:
        self._require_enabled()
        ctx = context if context is not None else {}
        _validate_question(question)
        allowed = _validate_options(options)
        raw, elapsed_ms = await self._call(
            self._provider.rank(question, allowed, ctx)
        )
        ranked = raw.value
        if (
            not isinstance(ranked, list)
            or len(ranked) != len(allowed)
            or len(set(ranked)) != len(ranked)
            or set(ranked) != set(allowed)
        ):
            raise DecisionValidationError(
                "provider rank output must be a permutation of the options"
            )
        return self._finish(raw, question, allowed, ctx, elapsed_ms)

    def _require_enabled(self) -> None:
        if self._mode == "off":
            raise DecisionEngineDisabled(
                "decision inference is disabled (decision_inference_mode=off)"
            )

    async def _call(
        self, coro: Awaitable[ProviderDecision[Any]]
    ) -> tuple[ProviderDecision[Any], float]:
        start = time.perf_counter()
        raw = await coro
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return raw, elapsed_ms

    def _finish(
        self,
        raw: ProviderDecision[Any],
        question: str,
        options: Sequence[str] | None,
        context: Mapping[str, Any],
        latency_ms: float,
    ) -> DecisionResult[Any]:
        confidence = raw.confidence
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            raise DecisionValidationError(
                f"provider confidence outside [0.0, 1.0]: {confidence!r}"
            )
        info = self._provider.info
        meta = DecisionMeta(
            provider=info.provider,
            inference_method=info.inference_method,
            model=info.model,
            model_revision=info.model_revision,
            latency_ms=latency_ms,
            input_hash=compute_input_hash(question, options, context),
        )
        return DecisionResult(
            value=raw.value,
            scores=dict(raw.scores),
            confidence=confidence,
            authoritative=self._mode == "enforce",
            meta=meta,
        )


def _validate_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip():
        raise DecisionValidationError("question must be a non-empty string")


def _validate_options(options: Sequence[str]) -> list[str]:
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        raise DecisionValidationError("options must be a sequence of strings")
    allowed = list(options)
    if not allowed:
        raise DecisionValidationError("options must be non-empty")
    if any(not isinstance(opt, str) or not opt for opt in allowed):
        raise DecisionValidationError("options must be non-empty strings")
    if len(set(allowed)) != len(allowed):
        raise DecisionValidationError("options must be unique")
    return allowed
