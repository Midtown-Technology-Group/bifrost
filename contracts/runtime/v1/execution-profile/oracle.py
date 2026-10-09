"""Test-only proposal oracle. No IO transport, process, credentials or durable store.

Trusted events below are injected assumptions, NEVER evidence of real authority.
Rust/Python production codecs and owner integration remain separate packages.
"""
import copy
import hashlib
import json
import math
import re
import struct
from datetime import datetime

MAX_FRAME = 16 * 1024 * 1024
MAX_SAFE = 9007199254740991
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class Rejection(ValueError):
    pass


def reject(code):
    raise Rejection(code)


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            reject("InvalidJson")
        result[key] = value
    return result


def integer_token(value):
    if value == "-0":
        return -0.0
    number = int(value)
    if -(1 << 63) <= number <= (1 << 64) - 1:
        return number
    return float(value)


def tree(value, depth=0):
    if isinstance(value, (list, dict)):
        if depth >= 64:
            reject("InvalidJson")
        for child in (list(value) + list(value.values()) if isinstance(value, dict) else value):
            tree(child, depth + 1)
    elif isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        reject("InvalidJson")
    elif isinstance(value, float) and not math.isfinite(value):
        reject("InvalidJson")


def decode(data, validator):
    """Oracle for a complete length-prefixed specimen, not a stream codec."""
    if not data:
        return None
    if len(data) < 4:
        reject("TruncatedFrame")
    length = struct.unpack(">I", data[:4])[0]
    if length == 0:
        reject("InvalidFrame")
    if length > MAX_FRAME:
        reject("FrameTooLarge")
    if len(data) != length + 4:
        reject("TruncatedFrame" if len(data) < length + 4 else "InvalidFrame")
    try:
        value = json.loads(data[4:].decode("utf-8"), object_pairs_hook=pairs,
                           parse_int=integer_token,
                           parse_constant=lambda _: reject("InvalidJson"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, Rejection):
            raise
        reject("InvalidJson")
    tree(value)
    keys = {"protocol", "type", "session_id", "message_id", "sequence", "correlation_id", "body"}
    if not isinstance(value, dict) or set(value) != keys:
        reject("InvalidFrame")
    if not isinstance(value["protocol"], str):
        reject("InvalidFrame")
    if value["protocol"] != "bifrost.runtime/v1":
        reject("UnsupportedProtocol")
    if not isinstance(value["type"], str) or not value["type"]:
        reject("InvalidFrame")
    for key in ("session_id", "message_id", "correlation_id"):
        item = value[key]
        if key == "correlation_id" and item is None:
            continue
        if not isinstance(item, str) or UUID.fullmatch(item) is None:
            reject("InvalidFrame")
    if type(value["sequence"]) is not int or not 1 <= value["sequence"] <= MAX_SAFE:
        reject("InvalidFrame")
    if value["type"] not in validator.schema["$defs"]:
        reject("UnsupportedFrame")
    if not validator.is_valid(value):
        reject("InvalidFrame")
    kind, body, corr = value["type"], value["body"], value["correlation_id"]
    correlation_field = {
        "Prepared": "prepare_message_id", "Start": "prepare_message_id",
        "Provision": "prepare_message_id", "LogBatch": "start_message_id",
        "Usage": "start_message_id", "Result": "start_message_id",
        "ResultReceipt": "result_message_id",
    }.get(kind)
    if correlation_field is not None and corr != body[correlation_field]:
        reject("InvalidFrame")
    if kind in ("Offer", "Heartbeat", "Cancel", "Stopped") and corr is not None:
        reject("InvalidFrame")
    if kind in ("Select", "Prepare") and corr is None:
        reject("InvalidFrame")
    return value


def wire(frame):
    data = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return struct.pack(">I", len(data)) + data


def tenant_context(binding):
    keys = ("execution_kind", "execution_id", "attempt_id", "attempt_number",
            "solution_id", "deployment_id", "artifact_id", "effective_scope")
    return {"kind": "tenant-context/v1", **{k: binding[k] for k in keys},
            "caller_id": binding["original_caller"]["caller_id"]}


class Session:
    """Independent parent-send/child-observation frontiers and synthetic owner facts."""

    def __init__(self, binding, artifact):
        if binding["artifact_id"] != artifact["artifact_id"]:
            reject("InvalidBinding")
        self.binding, self.artifact = binding, artifact
        self.seq = {"parent": 0, "adapter": 0}
        self.ids = {}
        self.offer = self.selection = self.prepare = self.prepared = None
        self.start = self.provision = self.grant = self.result = self.receipt = None
        self.result_bytes = None
        self.committed_start = self.cancel = None
        self.committed_budget = None
        self.child_start = self.child_cancel = self.delivered = False
        self.reported_start = self.reported_cancel = False
        self.effects = self.stopped = self.closed = self.revoked = False
        self.released = False
        self.winner = None
        self.elapsed = self.commit_elapsed = self.last_heartbeat = self.log_seq = 0
        self.now = datetime.fromisoformat("2030-01-01T00:00:00Z")
        self.cleanup = False

    def snapshot(self):
        return {"effects_permitted": self.effects, "winner": self.winner,
                "closed": self.closed, "cleanup_verified": self.cleanup,
                "result_observed": self.result is not None,
                "receipt": self.receipt["disposition"] if self.receipt else None,
                "sequences": self.seq.copy()}

    def receive(self, direction, frame, payload_bytes=None):
        candidate = copy.deepcopy(self)
        candidate._receive(direction, frame, payload_bytes)
        self.__dict__.update(candidate.__dict__)

    def _receive(self, direction, frame, payload_bytes):
        if self.closed or (direction == "adapter" and self.stopped):
            reject("SessionClosed")
        if frame["session_id"] != self.binding["session_id"]:
            reject("InvalidBinding")
        if direction not in self.seq or frame["sequence"] != self.seq[direction] + 1:
            reject("InvalidTransition")
        if frame["message_id"] in self.ids:
            reject("DuplicateMessage" if wire(frame) == self.ids[frame["message_id"]]
                   else "ConflictingMessage")
        kind, body, corr = frame["type"], frame["body"], frame["correlation_id"]
        parent = {"Select", "Prepare", "Start", "Provision", "Cancel", "ResultReceipt"}
        if (kind in parent) != (direction == "parent"):
            reject("InvalidTransition")
        expected = None
        if kind == "Offer":
            if self.offer is not None:
                reject("InvalidTransition")
            if "bifrost.runtime/v1" not in body["supported_protocols"]:
                reject("UnsupportedProtocol")
            if "execution_profile/v1" not in body["capabilities"]:
                reject("UnsupportedProfile")
            if body["runtime_incarnation_id"] != self.binding["runtime_incarnation_id"]:
                reject("InvalidBinding")
            self.offer = frame
        elif kind == "Select":
            if not self.offer or self.selection:
                reject("InvalidTransition")
            expected = self.offer["message_id"]
            if body["artifact_class"] not in self.offer["body"]["artifact_classes"]:
                reject("UnsupportedProfile")
            if body["artifact_class"] != self.artifact["kind"]:
                reject("InvalidBinding")
            self.selection = frame
        elif kind == "Prepare":
            if not self.selection or self.prepare or self.winner:
                reject("InvalidTransition")
            expected = self.selection["message_id"]
            if (body["binding"] != self.binding or body["artifact"] != self.artifact
                    or body["context"] != tenant_context(self.binding)):
                reject("InvalidBinding")
            self.prepare = frame
        elif kind == "Prepared":
            if not self.prepare or self.prepared or self.winner:
                reject("InvalidTransition")
            expected = self.prepare["message_id"]
            if body["prepare_message_id"] != expected or body["artifact"] != self.artifact:
                reject("InvalidBinding")
            self.prepared = frame
        elif kind == "Start":
            if not self.prepared or self.start or self.winner:
                reject("InvalidTransition")
            expected = self.prepare["message_id"]
            if (body["prepare_message_id"] != expected
                    or body["committed_start_id"] != self.committed_start
                    or body["remaining_run_ms"] != self.committed_budget):
                reject("InvalidBinding")
            self.start = frame
        elif kind == "Provision":
            if not self.prepared or self.provision or self.winner or not self.committed_start:
                reject("InvalidTransition")
            expected = self.prepare["message_id"]
            if body != self.grant or body["binding"] != self.binding or body["committed_start_id"] != self.committed_start:
                reject("InvalidGrant")
            self.provision = frame
        elif kind == "Cancel":
            if not self.selection or self.cancel or self.winner not in ("cancel", "failure", "result"):
                reject("InvalidTransition")
            self.cancel = frame
        elif kind == "ResultReceipt":
            if not self.result or body != self.receipt:
                reject("InvalidTransition")
            expected = self.result["message_id"]
        elif kind in ("Result", "LogBatch", "Usage"):
            if not self.start:
                reject("InvalidTransition")
            expected = self.start["message_id"]
            if body["start_message_id"] != expected:
                reject("InvalidBinding")
            # A queued observation may arrive after parent Cancel; send order isn't child order.
            if not self.released or self.result:
                reject("InvalidTransition")
            if kind == "Result":
                self.result = frame
                self.result_bytes = payload_bytes if payload_bytes is not None else wire(frame)[4:]
            elif kind == "LogBatch":
                if body["batch_sequence"] != self.log_seq + 1:
                    reject("InvalidTransition")
                self.log_seq += 1
            self.reported_start = True
        elif kind == "Heartbeat":
            if not self.prepared or body["monotonic_elapsed_ms"] < self.last_heartbeat:
                reject("InvalidTransition")
            state = body["state"]
            if state == "prepared":
                matches = not self.reported_start and not self.reported_cancel and body["start_message_id"] is None
            elif state == "cancelling":
                matches = self.cancel and body["start_message_id"] == (
                    self.start["message_id"] if self.start else None
                )
            else:
                matches = self.start and body["start_message_id"] == self.start["message_id"]
                matches = matches and self.released and not self.reported_cancel
            if not matches:
                reject("InvalidTransition")
            self.last_heartbeat = body["monotonic_elapsed_ms"]
            self.reported_start |= body["start_message_id"] is not None
            self.reported_cancel |= state == "cancelling"
        elif kind == "Stopped":
            if not self.selection:
                reject("InvalidTransition")
            start_id = self.start["message_id"] if self.start else None
            cancel_id = self.cancel["body"]["cancel_id"] if self.cancel else None
            result_id = self.result["message_id"] if self.result else None
            start_matches = body["start_message_id"] == start_id or (
                not self.reported_start and body["start_message_id"] is None
                and body["reason"] in ("prepare_rejected", "protocol_error")
            )
            cancel_matches = body["cancel_id"] == cancel_id or (
                not self.reported_cancel and body["cancel_id"] is None
            )
            if not start_matches or not cancel_matches or body["result_message_id"] != result_id:
                reject("InvalidBinding")
            if body["reason"] == "completed" and (not self.result or self.reported_cancel):
                reject("InvalidTransition")
            if body["reason"] == "cancelled" and (not self.cancel or body["cancel_id"] is None):
                reject("InvalidTransition")
            if body["reason"] == "prepare_rejected" and body["start_message_id"] is not None:
                reject("InvalidTransition")
            self.stopped = True
        if corr != expected:
            reject("InvalidBinding")
        self.seq[direction] += 1
        self.ids[frame["message_id"]] = wire(frame)

    def event(self, event):
        candidate = copy.deepcopy(self)
        candidate._event(event)
        self.__dict__.update(candidate.__dict__)

    def _event(self, event):
        kind = event["event"]
        if kind == "commit_start":
            if not self.prepared or self.committed_start or self.winner or self.closed:
                reject("InvalidTransition")
            self.committed_start = event["committed_start_id"]
            self.committed_budget = event["remaining_run_ms"]
            self.commit_elapsed = self.elapsed
        elif kind == "admit_grant":
            body = event["provision"]
            if not self.committed_start or self.closed or self.winner:
                reject("InvalidGrant")
            if body["binding"] != self.binding or body["committed_start_id"] != self.committed_start or body["prepare_message_id"] != self.prepare["message_id"]:
                reject("InvalidGrant")
            if self.grant:
                reject("InvalidGrant")
            self.grant = body
        elif kind == "observe_start":
            if not self.start or self.child_start:
                reject("InvalidTransition")
            self.child_start = True
        elif kind == "deliver":
            if not self.provision or event["delivery_id"] != self.provision["body"]["delivery_id"]:
                reject("InvalidGrant")
            self.delivered = True
        elif kind == "release":
            if not (self.child_start and self.delivered) or self.closed or self.winner or self.child_cancel:
                reject("EffectsForbidden")
            if self.released:
                reject("InvalidTransition")
            grant = self.provision["body"]
            if self.revoked or self.now >= datetime.fromisoformat(grant["expires_at"]):
                reject("InvalidGrant")
            deadline = self.prepare["body"]["workload"]["deadline_utc"]
            budget = self.start["body"]["remaining_run_ms"]
            if (deadline is not None and self.now >= datetime.fromisoformat(deadline)) or (budget is not None and self.elapsed - self.commit_elapsed >= budget):
                reject("DeadlineExceeded")
            self.effects = True
            self.released = True
        elif kind == "tick":
            if event["elapsed_ms"] < self.elapsed:
                reject("InvalidTransition")
            now = datetime.fromisoformat(event["now"])
            if now < self.now:
                reject("InvalidTransition")
            self.elapsed, self.now = event["elapsed_ms"], now
            if self.released:
                deadline = self.prepare["body"]["workload"]["deadline_utc"]
                budget = self.start["body"]["remaining_run_ms"]
                if (
                    self.now >= datetime.fromisoformat(self.provision["body"]["expires_at"])
                    or (deadline is not None and self.now >= datetime.fromisoformat(deadline))
                    or (budget is not None and self.elapsed - self.commit_elapsed >= budget)
                ):
                    self.effects = False
        elif kind in ("cancel_commit", "failure_commit"):
            if self.winner is None:
                self.winner = "cancel" if kind == "cancel_commit" else "failure"
            self.effects = False
        elif kind == "observe_cancel":
            if not self.cancel:
                reject("InvalidTransition")
            self.child_cancel, self.effects = True, False
        elif kind == "revoke":
            self.revoked, self.effects = True, False
        elif kind == "accept_result":
            if self.receipt:
                if event["result_sha256"] != self.receipt["result_sha256"]:
                    reject("ConflictingReceipt")
                return
            if not self.result or self.closed:
                reject("SessionClosed" if self.closed else "InvalidTransition")
            digest = hashlib.sha256(self.result_bytes).hexdigest()
            if event["result_sha256"] != digest:
                reject("ConflictingReceipt")
            if self.winner is None and (self.revoked or self.now >= datetime.fromisoformat(
                    self.provision["body"]["expires_at"])):
                reject("InvalidGrant")
            deadline = self.prepare["body"]["workload"]["deadline_utc"]
            budget = self.start["body"]["remaining_run_ms"]
            if self.winner is None and (
                (deadline is not None and self.now >= datetime.fromisoformat(deadline))
                or (budget is not None and self.elapsed - self.commit_elapsed >= budget)
            ):
                reject("DeadlineExceeded")
            if self.winner is None:
                self.winner = "result"
            self.receipt = {"result_message_id": self.result["message_id"], "result_sha256": digest,
                            "decision_id": event["decision_id"], "winner": self.winner,
                            "disposition": "accepted" if self.winner == "result" else "retained"}
            self.effects = False
        elif kind in ("transport_loss", "child_crash", "adapter_crash", "close"):
            self.closed, self.effects = True, False
            # Session/process loss alone is NEVER a durable terminal decision.
        elif kind == "cleanup_verified":
            self.cleanup = True
        else:
            reject("UnknownEvent")
