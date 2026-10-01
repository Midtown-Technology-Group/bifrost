"""One scenario vocabulary for Python now and the actual Rust HTTP adapter in W2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.core.principal import UserPrincipal
from src.models.orm.device_jobs import DeviceJob
from src.services.device_jobs import request_cancel, sweep_device_jobs
from tests.parity.harness import DeviceAdapter, Observation, SEED_TIME, SeededEnvironment

ROUTES = ("heartbeat", "claim", "running", "logs", "result")


def route_body(env: SeededEnvironment, route: str, token: str | None = None) -> tuple[str, dict[str, Any]]:
    session = str(env.ids["session"])
    token = token or str(env.ids["wrong_token"])
    if route == "heartbeat":
        return "/api/device/heartbeat", {"agent_session_id": session}
    if route == "claim":
        return "/api/device/jobs/claim", {"agent_session_id": session}
    body: dict[str, Any] = {"claim_token": token}
    if route == "running":
        body["agent_session_id"] = session
    elif route == "logs":
        body["entries"] = [{"seq": 1, "stream": "stdout", "text": "probe"}]
    else:
        body["status"] = "succeeded"
    return f"/api/device/jobs/{env.ids['job']}/{route}", body


def expect(observation: Observation, status: int, code: str | None = None) -> None:
    assert observation.status == status, f"{observation.step}: unexpected HTTP status {observation.status}"
    if code:
        assert observation.body["error"]["code"] == code
        assert observation.body["error"]["retryable"] is False


async def lifecycle(adapter: DeviceAdapter, env: SeededEnvironment) -> list[Observation]:
    result = []

    async def send(step: str, route: str, body: dict[str, Any] | None = None) -> Observation:
        path, default = route_body(env, route, token if route not in {"heartbeat", "claim"} else None)
        observation = await adapter.request(step, path, body or default, env.keys["device"])
        result.append(observation)
        return observation

    token = ""
    hb = await send("pending-heartbeat", "heartbeat", {"agent_session_id": str(env.ids["session"]), "agent_version": " \u0000v1\n "})
    expect(hb, 200)
    assert hb.body["poll_interval_seconds"] == 5
    assert hb.database["devices"][0]["agent_version"] == "v1"
    claim = await send("claim", "claim")
    expect(claim, 200)
    token = claim.body["claim_token"]
    assert claim.body["claim_lease_seconds"] == 60
    assert claim.database["jobs"][0]["claim_token"] == token
    expect(await send("claim-idle", "claim"), 204)
    # The source allows a different session on FIRST running; replay ignores it.
    running_body = {"claim_token": token, "agent_session_id": str(env.ids["other_session"])}
    running = await send("running-changes-session", "running", running_body)
    expect(running, 200)
    assert running.database["jobs"][0]["agent_session_id"] == str(env.ids["other_session"])
    replay = await send("running-replay-keeps-session", "running")
    expect(replay, 200)
    assert replay.database["jobs"][0]["agent_session_id"] == str(env.ids["other_session"])
    assert replay.database["jobs"][0]["started_at"] == running.database["jobs"][0]["started_at"]
    mismatch = await send("heartbeat-wrong-session", "heartbeat")
    expect(mismatch, 200)
    assert mismatch.database["jobs"][0]["last_agent_activity_at"] == replay.database["jobs"][0]["last_agent_activity_at"]
    assert mismatch.body["poll_interval_seconds"] == 10
    entries = [
        {"seq": 3, "stream": "stderr", "text": "third", "ts": "2020-02-01T00:00:00Z"},
        {"seq": 1, "stream": "stdout", "text": "first", "ts": None},
    ]
    batch = {"claim_token": token, "entries": entries}
    logs = await send("logs-sort-and-gap", "logs", batch)
    expect(logs, 204)
    assert [row["seq"] for row in logs.database["logs"]] == [1, 3]
    assert logs.database["jobs"][0]["log_sequence"] == 3
    assert logs.events == [{"channel": f"bifrost:device_job:{env.ids['job']}", "payload": {
        "type": "device_job_logs", "job_id": str(env.ids["job"]), "entries": [
            {"seq": 1, "stream": "stdout", "text": "first", "ts": None},
            {"seq": 3, "stream": "stderr", "text": "third", "ts": "2020-02-01T00:00:00+00:00"},
        ],
    }}]
    # Timestamp changes alone are ignored by current replay semantics.
    entries[0]["ts"] = "2021-02-01T00:00:00Z"
    replay_logs = await send("log-replay-ignores-ts", "logs", batch)
    expect(replay_logs, 204)
    assert replay_logs.events == []
    assert replay_logs.database["logs"] == logs.database["logs"]
    # An earlier new seq must roll back if a later accepted seq conflicts.
    conflict = await send("log-conflict-atomic", "logs", {"claim_token": token, "entries": [
        {"seq": 2, "stream": "stdout", "text": "must-rollback"},
        {"seq": 3, "stream": "stderr", "text": "conflict"},
    ]})
    expect(conflict, 409, "log_seq_conflict")
    assert conflict.database["logs"] == logs.database["logs"]
    assert conflict.events == []
    result_body = {"claim_token": token, "status": "succeeded", "exit_code": 0, "output": "complete", "duration_ms": 7, "truncated": True, "extra": "ignored"}
    finished = await send("result", "result", result_body)
    expect(finished, 200)
    job = finished.database["jobs"][0]
    assert (job["status"], job["result"], job["exit_code"]) == ("succeeded", "complete", 0)
    assert finished.events == []
    expect(await send("result-replay-terminal", "result", result_body), 409, "job_terminal")
    wrong = await send("terminal-stale-fence-precedence", "result", {**result_body, "claim_token": str(env.ids["wrong_token"])})
    expect(wrong, 409, "fence_violation")
    return result


async def python_control(adapter: DeviceAdapter, env: SeededEnvironment, action: str) -> Observation:
    """Python retains cancel/watchdog in mixed ownership; capture those commits too."""
    before = datetime.now(timezone.utc)
    async with env.sessions() as db:
        if action == "cancel":
            user = UserPrincipal(user_id=env.ids["session"], email="synthetic@example.invalid", organization_id=env.ids["org"])
            job = await request_cancel(db, user, env.ids["job"], now=SEED_TIME)
            body = {"status": job.status}
        else:
            body = await sweep_device_jobs(db)
    after = datetime.now(timezone.utc)
    return Observation(f"python-{action}", 0, body, await adapter.capture.snapshot(), await adapter.capture.drain_events(), before, after)


async def reclaim_and_loss(adapter: DeviceAdapter, env: SeededEnvironment, loss_reason: str = "silence") -> list[Observation]:
    path, body = route_body(env, "claim")
    first = await adapter.request("claim-before-possible-spawn", path, body, env.keys["device"])
    expect(first, 200)
    old = first.body["claim_token"]
    # Server cannot observe an external spawn if /running NEVER commits. It
    # reclaims claimed regardless of last_agent_activity_at; this is a known
    # ambiguity, not proof that the previous process had no side effects.
    await env.update(DeviceJob, "job", claimed_at=SEED_TIME)
    second = await adapter.request("reclaim-unreported-spawn", path, {"agent_session_id": str(env.ids["other_session"])}, env.keys["device"])
    expect(second, 200)
    token = second.body["claim_token"]
    assert token != old
    result = [first, second]
    for route in ("running", "logs", "result"):
        path, body = route_body(env, route, old)
        fenced = await adapter.request(f"old-fence-{route}", path, body, env.keys["device"])
        expect(fenced, 409, "fence_violation")
        result.append(fenced)
    path, body = route_body(env, "running", token)
    running = await adapter.request("committed-running", path, body, env.keys["device"])
    expect(running, 200)
    result.append(running)
    # Simulates an ACK lost AFTER commit: resend the exact real route.
    ack_replay = await adapter.request("committed-running-ack-loss-replay", path, body, env.keys["device"])
    expect(ack_replay, 200)
    result.append(ack_replay)
    changes: dict[str, Any] = {"started_at": SEED_TIME}
    if loss_reason == "silence":
        changes["last_agent_activity_at"] = SEED_TIME
    await env.update(DeviceJob, "job", **changes)
    lost = await python_control(adapter, env, "sweep")
    assert lost.database["jobs"][0]["status"] == "lost"
    assert lost.body["lost_silence" if loss_reason == "silence" else "lost_backstop"] >= 1
    result.append(lost)
    for route in ("running", "logs", "result"):
        path, body = route_body(env, route, token)
        late = await adapter.request(f"lost-late-{route}", path, body, env.keys["device"])
        expect(late, 409, "job_terminal")
        assert late.events == []
        result.append(late)
    path, body = route_body(env, "claim")
    idle = await adapter.request("lost-never-requeues", path, body, env.keys["device"])
    expect(idle, 204)
    result.append(idle)
    return result
