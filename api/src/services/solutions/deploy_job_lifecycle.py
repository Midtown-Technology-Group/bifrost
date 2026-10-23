"""Lifecycle rules for pollable Solution deploy-job projections."""

from __future__ import annotations


def failure_projection_status(
    *, allow_connected_install: bool, rollback_committed: bool = False
) -> str:
    """Return the public status after deploy failure handling.

    A first deploy for a newly-created repository install remains nonterminal
    until the platform-job worker commits the install rollback. Other deploy
    failures become terminal immediately.
    """
    if allow_connected_install and not rollback_committed:
        return "running"
    return "failed"
