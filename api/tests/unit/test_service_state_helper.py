"""Negative and deadline contracts without real waits or live mutations."""

from unittest.mock import Mock

import pytest

from tests.helpers import service_state


def _client(states):
    responses = []
    for desired, observed, active in states:
        response = Mock(status_code=200)
        response.json.return_value = {
            "desired_state": desired, "observed_state": observed, "active_attempt_id": active,
        }
        responses.append(response)
    client = Mock()
    client.get.side_effect = responses
    return client


def test_stopped_wait_proves_terminal_and_only_reads(monkeypatch):
    monkeypatch.setattr(service_state.time, "sleep", lambda _: None)
    client = _client([("stopped", "stopping", "a"), ("stopped", "stopped", None)])
    assert service_state.wait_for_stopped(client, {}, "s")["active_attempt_id"] is None
    assert client.get.call_count == 2
    client.post.assert_not_called()


@pytest.mark.parametrize("state", [
    ("running", "stopped", None),
    ("stopped", "starting", "a"),
    ("stopped", "failed", None),
    ("stopped", "stopped", "a"),
])
def test_stopped_wait_rejects_invalid_progress(state):
    with pytest.raises(AssertionError):
        service_state.wait_for_stopped(_client([state]), {}, "s")


def test_stopped_wait_rejects_http_failure():
    client = Mock()
    client.get.return_value.status_code = 500
    with pytest.raises(AssertionError):
        service_state.wait_for_stopped(client, {}, "s")


def test_stopped_wait_times_out_without_retrying_mutation(monkeypatch):
    monkeypatch.setattr(service_state.time, "monotonic", Mock(side_effect=[0, 30]))
    sleep = Mock()
    monkeypatch.setattr(service_state.time, "sleep", sleep)
    client = _client([("stopped", "stopping", "a")])
    with pytest.raises(AssertionError, match="failed to stop within"):
        service_state.wait_for_stopped(client, {}, "s")
    sleep.assert_not_called()
    client.post.assert_not_called()
