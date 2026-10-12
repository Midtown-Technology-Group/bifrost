"""Shared worker heartbeat compatibility parsing.

Worker heartbeat payloads evolved across releases: newer workers report
``active_process_count`` / ``configured_capacity``, while legacy payloads
only carry ``pool_size`` / ``max_workers`` (or omit capacity entirely).
All platform worker handlers (list, stats, detail) must apply the same
fallback rules, so they are centralized here instead of being duplicated
per endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _to_int(value: Any) -> int | None:
    """Best-effort int coercion; returns None when the value is unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                as_float = float(text)
            except ValueError:
                return None
            return int(as_float) if as_float.is_integer() else None
    return None


def _to_float(value: Any) -> float | None:
    """Best-effort float coercion; returns None when the value is unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class NormalizedHeartbeat:
    """Capacity-relevant fields of one worker heartbeat with fallbacks applied."""

    active_process_count: int = 0
    pool_size: int = 0
    configured_capacity: int | None = None
    max_workers: int | None = None
    idle_count: int = 0
    busy_count: int = 0
    available_slots: int | None = None
    saturation_ratio: float | None = None
    requirements_installed: int | None = None
    requirements_total: int | None = None
    admission_rejections: dict[str, int] = field(default_factory=dict)


def normalize_heartbeat(hb: Mapping[str, Any] | None) -> NormalizedHeartbeat:
    """Apply the shared capacity fallback rules to one heartbeat payload.

    - ``active_process_count`` falls back to ``pool_size`` (legacy), else 0.
    - ``pool_size`` falls back to the resolved ``active_process_count``.
    - ``configured_capacity`` falls back to ``max_workers``, and vice versa;
      both stay None when the heartbeat reports neither.
    - Counts default to 0; capacity/slot/ratio fields stay None when missing
      or unparseable so callers can distinguish "unknown" from zero.
    """
    if not isinstance(hb, Mapping):
        return NormalizedHeartbeat()

    active_raw = _to_int(hb.get("active_process_count"))
    size_raw = _to_int(hb.get("pool_size"))
    active = active_raw if active_raw is not None else (size_raw if size_raw is not None else 0)
    size = size_raw if size_raw is not None else active

    capacity_raw = _to_int(hb.get("configured_capacity"))
    max_workers_raw = _to_int(hb.get("max_workers"))
    configured = capacity_raw if capacity_raw is not None else max_workers_raw
    max_workers = max_workers_raw if max_workers_raw is not None else configured

    admission = hb.get("admission")
    rejections: dict[str, int] = {}
    if isinstance(admission, Mapping):
        raw_rejections = admission.get("rejections")
        if isinstance(raw_rejections, Mapping):
            for reason, count in raw_rejections.items():
                coerced = _to_int(count)
                if coerced is not None:
                    rejections[str(reason)] = coerced

    return NormalizedHeartbeat(
        active_process_count=active,
        pool_size=size,
        configured_capacity=configured,
        max_workers=max_workers,
        idle_count=_to_int(hb.get("idle_count")) or 0,
        busy_count=_to_int(hb.get("busy_count")) or 0,
        available_slots=_to_int(hb.get("available_slots")),
        saturation_ratio=_to_float(hb.get("saturation_ratio")),
        requirements_installed=_to_int(hb.get("requirements_installed")),
        requirements_total=_to_int(hb.get("requirements_total")),
        admission_rejections=rejections,
    )
