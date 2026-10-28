"""Opt-in lifecycle for a static remote launcher (#1105).

A static launcher is always-on unless its own environment says otherwise.
Job launchers ignore these variables and keep Settings → Kubernetes idle
time and ``pool_max_lifetime_seconds``. The values ride the signed hello;
the API applies them. They are not read from the API process environment,
so a shared env file on the API does not retire the API's summary executor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Matches execution_workers.version.
_MAX_VERSION_LENGTH = 100
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class StaticLauncherConfigError(ValueError):
    """A static lifecycle variable is set but not usable."""


@dataclass(frozen=True)
class StaticLauncherPolicy:
    """What this process puts on its hello.

    ``version`` is the build the process is running. ``None`` means the
    hello omits it (only tests and callers that have not read one). A real
    launcher always supplies the string from :func:`shared.version.get_version`.
    """

    version: str | None = None
    drain_when_outdated: bool = False
    exit_when_idle_seconds: int | None = None
    max_runs: int | None = None


def _flag(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise StaticLauncherConfigError(f"{name} must be true or false")


def _idle_seconds(environ: Mapping[str, str], name: str) -> int | None:
    """Unset stays up. ``0`` exits on the next idle observation."""
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise StaticLauncherConfigError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise StaticLauncherConfigError(f"{name} must be a non-negative integer")
    return value


def _positive_int(environ: Mapping[str, str], name: str) -> int | None:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise StaticLauncherConfigError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise StaticLauncherConfigError(f"{name} must be a positive integer")
    return value


def _reported_version(version: str) -> str:
    if (
        not isinstance(version, str)
        or not 1 <= len(version) <= _MAX_VERSION_LENGTH
        or any(ord(char) < 32 or ord(char) > 126 for char in version)
    ):
        raise StaticLauncherConfigError("BIFROST_VERSION must be 1-100 printable characters")
    return version


def static_launcher_policy(
    environ: Mapping[str, str], *, form: str, version: str
) -> StaticLauncherPolicy:
    """Policy for one hello.

    Job launchers report ``version`` only. Static launchers also honor
    ``BIFROST_STATIC_DRAIN_WHEN_OUTDATED``,
    ``BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS`` and ``BIFROST_STATIC_MAX_RUNS``.
    Those three are ignored on a Job even if the environment sets them:
    a Job already drains from Settings and from the upgrade controller.
    """
    reported = _reported_version(version)
    if form != "static":
        return StaticLauncherPolicy(version=reported)
    return StaticLauncherPolicy(
        version=reported,
        drain_when_outdated=_flag(environ, "BIFROST_STATIC_DRAIN_WHEN_OUTDATED"),
        exit_when_idle_seconds=_idle_seconds(environ, "BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS"),
        max_runs=_positive_int(environ, "BIFROST_STATIC_MAX_RUNS"),
    )
