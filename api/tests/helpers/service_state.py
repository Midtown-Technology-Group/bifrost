"""Bounded terminal-stop proof; never retries a mutation."""

import time


def wait_for_stopped(client, headers, service_id, *, timeout=30.0):
    """Allow two 10s worker beats plus the fixture's 10s shutdown grace."""
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/services/{service_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["desired_state"] == "stopped", body
        if body["observed_state"] == "stopped":
            assert body["active_attempt_id"] is None, body
            return body
        assert body["observed_state"] == "stopping", body
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"Service {service_id} failed to stop within {timeout}s: {body}"
        time.sleep(min(0.5, remaining))
