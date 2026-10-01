"""Consume the same canonical vectors as Rust; no DB/worker fixtures or effects."""

import copy
import io
import json

import pytest
from src.runtime_protocol.control import (
    MAX_DEPTH,
    MAX_FRAME_BYTES,
    ProtocolError,
    decode_json,
    encode_frame,
    read_frame,
    write_frame,
)
from src.runtime_protocol.session import (
    AgentExecutionAttempt,
    AgentRunJob,
    ControlSession,
    Direction,
    PreparedBinding,
    StartAuthorization,
    WorkflowExecutionAttempt,
    WorkflowJob,
)

from .interchange import load_vectors


def vectors():
    # Missing shared vectors must fail, never skip or fabricate a fallback.
    data = load_vectors()
    assert data["max_frame_bytes"] == MAX_FRAME_BYTES
    assert data["max_depth"] == MAX_DEPTH
    return data


def wire_bytes(vector):
    return vector["json"].encode() if "json" in vector else bytes.fromhex(vector["hex"])


def test_shared_wire_vectors():
    data = vectors()
    assert data["wire"]
    for vector in data["wire"]:
        raw = wire_bytes(vector)
        if vector["expected"] == "ok":
            frame = decode_json(raw)
            assert read_frame(io.BytesIO(encode_frame(frame))) == frame, vector["name"]
        else:
            with pytest.raises(ProtocolError) as captured:
                decode_json(raw)
            assert captured.value.code.value == vector["expected"], vector["name"]


class PartialReader(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.interrupted = True

    def read(self, size=-1):
        if self.interrupted:
            self.interrupted = False
            raise InterruptedError
        return super().read(min(size, 1))


class PartialWriter(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.interrupted = True

    def write(self, data):
        if self.interrupted:
            self.interrupted = False
            raise InterruptedError
        return super().write(data[:1])


def test_shared_binary_vectors_with_partial_io():
    data = vectors()
    assert data["binary"]
    for vector in data["binary"]:
        stream = PartialReader(bytes.fromhex(vector["hex"]))
        if vector["expected"] == "eof":
            assert read_frame(stream) is None
        elif vector["expected"] == "ok":
            frame = read_frame(stream)
            assert frame is not None
            writer = PartialWriter()
            write_frame(writer, frame)
            assert read_frame(io.BytesIO(writer.getvalue())) == frame
        else:
            with pytest.raises(ProtocolError) as captured:
                read_frame(stream)
            assert captured.value.code.value == vector["expected"], vector["name"]


def prepared_binding(vector):
    obj = copy.deepcopy(vector)
    logical_kind = obj.pop("logical_kind")
    logical_id = obj.pop("logical_id")
    attempt_kind = obj.pop("attempt_kind")
    attempt_id = obj.pop("attempt_id")
    attempt_number = obj.pop("attempt_number")
    job = (
        WorkflowJob(logical_id)
        if logical_kind == "workflow"
        else AgentRunJob(logical_id)
    )
    attempt = (
        WorkflowExecutionAttempt(attempt_id, attempt_number)
        if attempt_kind == "workflow_execution_attempt"
        else AgentExecutionAttempt(attempt_id, attempt_number)
    )
    return PreparedBinding(logical_job=job, attempt=attempt, **obj)


def invoke_event(session, event):
    if event["action"] == "authorize":
        session.authorize_start(StartAuthorization(**event["authorization"]))
    else:
        frame = decode_json(json.dumps(event["frame"], ensure_ascii=False).encode())
        session.accept(Direction(event["direction"]), event["process_identity"], frame)


def test_shared_parent_session_vectors():
    data = vectors()
    assert data["sessions"]
    for vector in data["sessions"]:
        if vector["expected_binding"] != "ok":
            with pytest.raises(ProtocolError) as captured:
                ControlSession(prepared_binding(vector["binding"]))
            assert captured.value.code.value == vector["expected_binding"], vector[
                "name"
            ]
            assert not vector["events"]
            continue
        session = ControlSession(prepared_binding(vector["binding"]))
        for event in vector["events"]:
            before = session.state

            if event["expected"] in (
                "ok",
                "Prepared",
                "Executing",
                "Cancelling",
                "StoppedObserved",
            ):
                invoke_event(session, event)
                if event["expected"] != "ok":
                    assert session.state.value == event["expected"], vector["name"]
            else:
                with pytest.raises(ProtocolError) as captured:
                    invoke_event(session, event)
                assert captured.value.code.value == event["expected"], vector["name"]
                assert session.state == before, vector["name"]


def test_over_cap_payload_and_concatenated_frames():
    with pytest.raises(ProtocolError) as captured:
        decode_json(b" " * (MAX_FRAME_BYTES + 1))
    assert captured.value.code.value == "FrameTooLarge"
    frame = decode_json(wire_bytes(vectors()["wire"][0]))
    encoded = encode_frame(frame)
    stream = io.BytesIO(encoded + encoded)
    assert read_frame(stream) == frame
    assert read_frame(stream) == frame
    assert read_frame(stream) is None
