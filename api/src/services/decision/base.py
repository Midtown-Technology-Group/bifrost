"""
Decision Provider Base Interface

Abstract base class and data types for the provider-neutral DecisionEngine
primitives (boolean / choice / score / rank). See issue #806.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

DecisionMode = Literal["off", "shadow", "enforce"]
DecisionPrimitive = Literal["boolean", "choice", "score", "rank"]


class DecisionError(Exception):
    """Base error for the decision engine surface."""


class DecisionEngineDisabled(DecisionError):
    """Raised when decision inference mode is off."""


class DecisionValidationError(DecisionError):
    """Raised when decision inputs or provider outputs fail validation."""


@dataclass(frozen=True)
class DecisionProviderInfo:
    """Identity of the serving provider, stamped onto every result."""

    provider: str
    inference_method: str
    model: str | None = None
    model_revision: str | None = None


@dataclass(frozen=True)
class ProviderDecision(Generic[T]):
    """Raw provider output before engine validation and enrichment.

    ``scores`` are raw option scores expressing model preference; they are
    not calibrated probabilities. ``confidence`` is reserved for an optional
    empirically calibrated probability supplied by a later calibration layer.
    """

    value: T
    scores: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None


@dataclass(frozen=True)
class DecisionMeta:
    """Reproducibility metadata attached to every engine result."""

    provider: str
    inference_method: str
    model: str | None
    model_revision: str | None
    latency_ms: float
    input_hash: str


@dataclass(frozen=True)
class DecisionResult(Generic[T]):
    """Validated decision output with provenance metadata.

    ``authoritative`` is True only when the engine ran in enforce mode.
    Shadow-mode results must not alter production behavior, and even
    authoritative results remain subject to deterministic policy gates.
    """

    value: T
    scores: Mapping[str, float]
    confidence: float | None
    authoritative: bool
    meta: DecisionMeta


class DecisionProvider(ABC):
    """Serves bounded decisions through a fixed inference method."""

    @property
    @abstractmethod
    def info(self) -> DecisionProviderInfo:
        """Return provider identity for result metadata."""

    @abstractmethod
    async def boolean(
        self,
        question: str,
        context: Mapping[str, Any],
    ) -> ProviderDecision[bool]:
        """Answer a yes/no question."""

    @abstractmethod
    async def choice(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[str]:
        """Select exactly one option from a bounded set."""

    @abstractmethod
    async def score(
        self,
        question: str,
        context: Mapping[str, Any],
    ) -> ProviderDecision[float]:
        """Produce a scalar score in [0.0, 1.0]."""

    @abstractmethod
    async def rank(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[list[str]]:
        """Order every option from most to least preferred."""
