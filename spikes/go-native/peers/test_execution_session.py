"""Independent Python specification transcripts, never supervisor authority.

The published profile supplies rules and fixtures. Owner commits, material
delivery, release and cleanup are injected assumptions. This test-only module
has no platform/oracle imports, process launch, credentials or durable store.
"""
import copy
import io
import json
import struct
import unittest
from datetime import UTC, datetime
from pathlib import Path

from execution_codec import PROTOCOL, Codec, CodecError

CORPUS = Path(__file__).resolve().parent.parent / "executionprofile/testdata/session-vectors.json"
VECTORS = json.loads(CORPUS.read_text())
PARENT_MESSAGES = {"Select", "Prepare", "Start", "Provision", "Cancel", "ResultReceipt"}
CORRELATIONS = {"Select": "Offer", "Prepare": "Select", "Prepared": "Prepare",
                "Start": "Prepare", "Provision": "Prepare", "ResultReceipt": "Result",
                "LogBatch": "Start", "Usage": "Start", "Result": "Start"}
SINGLE_MESSAGES = {"Offer", "Select", "Prepare", "Prepared", "Start", "Provision", "Cancel", "Result"}


def require(condition, code="InvalidTransition"):
    if not condition:
        raise CodecError(code)


def utc(value):
    return datetime.fromisoformat(value)


class Transcript:
    """In-memory specification state; cannot admit an actual execution."""

    def __init__(self, environment):
        self.binding = copy.deepcopy(environment["binding"])
        self.artifact = copy.deepcopy(environment["artifact"])
        self.messages = {}
        self.sequences = {"parent": 0, "adapter": 0}
        self.payloads = {}
        self.start_commit = None
        self.budget = None
        self.elapsed = self.commit_elapsed = self.heartbeat = self.log_sequence = 0
        self.now = datetime(2030, 1, 1, tzinfo=UTC)
        self.grant = self.receipt = self.result_digest = self.winner = None
        self.start_observed = self.cancel_observed = self.material_delivered = False
        self.start_reported = self.cancel_reported = self.released = False
        self.effects = self.closed = self.stopped = self.revoked = self.cleanup = False

    def snapshot(self):
        return {"effects_permitted": self.effects, "winner": self.winner,
                "closed": self.closed, "cleanup_verified": self.cleanup,
                "result_observed": "Result" in self.messages,
                "receipt": self.receipt["disposition"] if self.receipt else None,
                "sequences": dict(self.sequences)}

    def message_id(self, kind):
        return self.messages.get(kind, {}).get("message_id")

    def tenant_context(self):
        fields = ("execution_kind", "execution_id", "attempt_id", "attempt_number",
                  "solution_id", "deployment_id", "artifact_id", "effective_scope")
        return {"kind": "tenant-context/v1", "caller_id": self.binding["original_caller"]["caller_id"],
                **{key: self.binding[key] for key in fields}}

    def apply(self, step, codec):
        # Transactional test state: even a late correlation rejection must not
        # consume sequence, observation frontier or result digest.
        candidate = copy.deepcopy(self)
        if step["event"] == "receive":
            if "hex" in step:
                raw = bytes.fromhex(step["hex"])
            else:
                # Preserve literal fixture insertion order for its specified
                # receipt preimage. A different encoding has a different digest.
                payload = json.dumps(step["frame"], ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()
                raw = struct.pack(">I", len(payload)) + payload
            stream = io.BytesIO(raw)
            decoded = codec.read(stream)
            require(decoded is not None and not stream.read(1), "InvalidFrame")
            candidate.receive(step["direction"], decoded, raw[4:])
        else:
            candidate.assume(step)
        self.__dict__ = candidate.__dict__

    def receive(self, direction, decoded, payload):
        frame = decoded.frame
        require(not self.closed and not (direction == "adapter" and self.stopped), "SessionClosed")
        require(frame["session_id"] == self.binding["session_id"], "InvalidBinding")
        require(direction in self.sequences and frame["sequence"] == self.sequences[direction] + 1)
        identity = frame["message_id"]
        if identity in self.payloads:
            raise CodecError("DuplicateMessage" if self.payloads[identity] == payload else "ConflictingMessage")
        kind, body = frame["type"], frame["body"]
        require((kind in PARENT_MESSAGES) == (direction == "parent"))
        expected = self.message_id(CORRELATIONS[kind]) if kind in CORRELATIONS else None
        if kind == "Offer":
            require(kind not in self.messages)
            require(PROTOCOL in body["supported_protocols"], "UnsupportedProtocol")
            require("execution_profile/v1" in body["capabilities"], "UnsupportedProfile")
            require(body["runtime_incarnation_id"] == self.binding["runtime_incarnation_id"], "InvalidBinding")
        elif kind == "Select":
            require("Offer" in self.messages and kind not in self.messages)
            require(body["artifact_class"] in self.messages["Offer"]["body"]["artifact_classes"], "UnsupportedProfile")
            require(body["artifact_class"] == self.artifact["kind"], "InvalidBinding")
        elif kind in {"Prepare", "Prepared", "Start"}:
            previous = {"Prepare": "Select", "Prepared": "Prepare", "Start": "Prepared"}[kind]
            require(previous in self.messages and kind not in self.messages and self.winner is None)
            if kind == "Prepare":
                require(body["binding"] == self.binding and body["artifact"] == self.artifact
                        and body["context"] == self.tenant_context(), "InvalidBinding")
            else:
                require(body["prepare_message_id"] == expected, "InvalidBinding")
                if kind == "Prepared":
                    require(body["artifact"] == self.artifact, "InvalidBinding")
                else:
                    require(body["committed_start_id"] == self.start_commit
                            and body["remaining_run_ms"] == self.budget, "InvalidBinding")
        elif kind == "Provision":
            require("Prepared" in self.messages and kind not in self.messages
                    and self.winner is None and self.start_commit is not None)
            require(body == self.grant and body["binding"] == self.binding
                    and body["committed_start_id"] == self.start_commit, "InvalidGrant")
        elif kind == "Cancel":
            require("Select" in self.messages and kind not in self.messages
                    and self.winner in {"cancel", "failure", "result"})
        elif kind == "ResultReceipt":
            require("Result" in self.messages and body == self.receipt)
        elif kind in {"LogBatch", "Usage", "Result"}:
            require("Start" in self.messages)
            require(body["start_message_id"] == expected, "InvalidBinding")
            require(self.released and "Result" not in self.messages)
            if kind == "LogBatch":
                require(body["batch_sequence"] == self.log_sequence + 1)
                self.log_sequence += 1
            if kind == "Result":
                self.result_digest = decoded.payload_sha256()
            self.start_reported = True
        elif kind == "Heartbeat":
            require("Prepared" in self.messages and body["monotonic_elapsed_ms"] >= self.heartbeat)
            state, start = body["state"], body["start_message_id"]
            possible = {
                "prepared": not self.start_reported and not self.cancel_reported and start is None,
                "executing": "Start" in self.messages and start == self.message_id("Start")
                and self.released and not self.cancel_reported,
                "cancelling": "Cancel" in self.messages and start == self.message_id("Start"),
            }
            require(possible.get(state, False))
            self.heartbeat = body["monotonic_elapsed_ms"]
            self.start_reported |= start is not None
            self.cancel_reported |= state == "cancelling"
        elif kind == "Stopped":
            require("Select" in self.messages)
            start, cancel, reason = body["start_message_id"], body["cancel_id"], body["reason"]
            sent_cancel = self.messages.get("Cancel", {}).get("body", {}).get("cancel_id")
            start_matches = start == self.message_id("Start") or (
                not self.start_reported and start is None and reason in {"prepare_rejected", "protocol_error"})
            cancel_matches = cancel == sent_cancel or (not self.cancel_reported and cancel is None)
            require(start_matches and cancel_matches and body["result_message_id"] == self.message_id("Result"),
                    "InvalidBinding")
            require(reason != "completed" or ("Result" in self.messages and not self.cancel_reported))
            require(reason != "cancelled" or ("Cancel" in self.messages and cancel is not None))
            require(reason != "prepare_rejected" or start is None)
            self.stopped = True
        require(frame["correlation_id"] == expected, "InvalidBinding")
        self.sequences[direction] += 1
        self.payloads[identity] = payload
        if kind in SINGLE_MESSAGES:
            self.messages[kind] = frame

    def deadline_expired(self):
        deadline = self.messages["Prepare"]["body"]["workload"]["deadline_utc"]
        return ((deadline is not None and self.now >= utc(deadline))
                or (self.budget is not None and self.elapsed - self.commit_elapsed >= self.budget))

    def grant_current(self):
        return not self.revoked and self.now < utc(self.grant["expires_at"])

    def assume(self, event):
        kind = event["event"]
        if kind == "commit_start":
            require("Prepared" in self.messages and self.start_commit is None
                    and self.winner is None and not self.closed)
            self.start_commit, self.budget = event["committed_start_id"], event["remaining_run_ms"]
            self.commit_elapsed = self.elapsed
        elif kind == "admit_grant":
            require(self.start_commit is not None and not self.closed
                    and self.winner is None and self.grant is None, "InvalidGrant")
            grant = event["provision"]
            require(grant["binding"] == self.binding and grant["committed_start_id"] == self.start_commit
                    and grant["prepare_message_id"] == self.message_id("Prepare"), "InvalidGrant")
            self.grant = copy.deepcopy(grant)
        elif kind == "observe_start":
            require("Start" in self.messages and not self.start_observed)
            self.start_observed = True
        elif kind == "deliver":
            require("Provision" in self.messages and event["delivery_id"] == self.grant["delivery_id"], "InvalidGrant")
            self.material_delivered = True
        elif kind == "release":
            require(self.start_observed and self.material_delivered and not self.closed
                    and self.winner is None and not self.cancel_observed, "EffectsForbidden")
            require(not self.released)
            require(self.grant_current(), "InvalidGrant")
            require(not self.deadline_expired(), "DeadlineExceeded")
            self.effects = self.released = True
        elif kind == "tick":
            now, elapsed = utc(event["now"]), event["elapsed_ms"]
            require(elapsed >= self.elapsed and now >= self.now)
            self.now, self.elapsed = now, elapsed
            if self.released and (not self.grant_current() or self.deadline_expired()):
                self.effects = False
        elif kind in {"cancel_commit", "failure_commit"}:
            if self.winner is None:
                self.winner = "cancel" if kind == "cancel_commit" else "failure"
            self.effects = False
        elif kind == "observe_cancel":
            require("Cancel" in self.messages)
            self.cancel_observed, self.effects = True, False
        elif kind == "revoke":
            self.revoked, self.effects = True, False
        elif kind == "accept_result":
            if self.receipt is not None:
                require(event["result_sha256"] == self.receipt["result_sha256"], "ConflictingReceipt")
                return
            require(not self.closed, "SessionClosed")
            require("Result" in self.messages)
            require(event["result_sha256"] == self.result_digest, "ConflictingReceipt")
            if self.winner is None:
                require(self.grant_current(), "InvalidGrant")
                require(not self.deadline_expired(), "DeadlineExceeded")
                self.winner = "result"
            self.receipt = {"result_message_id": self.message_id("Result"), "result_sha256": self.result_digest,
                            "decision_id": event["decision_id"], "winner": self.winner,
                            "disposition": "accepted" if self.winner == "result" else "retained"}
            self.effects = False
        elif kind in {"transport_loss", "child_crash", "adapter_crash", "close"}:
            self.closed, self.effects = True, False
        elif kind == "cleanup_verified":
            self.cleanup = True
        else:
            raise CodecError("UnknownEvent")


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codec = Codec()

    def test_published_inventory(self):
        self.assertEqual(len(VECTORS["cases"]), 68)
        count = sum(len(case["steps"]) + len(VECTORS["fixtures"].get(case.get("setup"), []))
                    for case in VECTORS["cases"])
        self.assertEqual(count, 695)


def transcript_test(case):
    def test(self):
        model = Transcript(VECTORS["environment"])
        steps = VECTORS["fixtures"].get(case.get("setup"), []) + case["steps"]
        for index, step in enumerate(steps):
            with self.subTest(step=index, event=step["input"]["event"]):
                before = copy.deepcopy(model.__dict__)
                try:
                    model.apply(step["input"], self.codec)
                    actual = None
                except CodecError as exc:
                    actual = exc.code
                    self.assertEqual(model.__dict__, before, "rejection changed private test state")
                self.assertEqual(actual, step["error"])
                self.assertEqual(model.snapshot(), step["after"])
    return test


for index, case in enumerate(VECTORS["cases"]):
    setattr(SessionTests, f"test_session_{index:03d}_{case['name'].replace('-', '_')}", transcript_test(case))


if __name__ == "__main__":
    unittest.main(verbosity=2)
