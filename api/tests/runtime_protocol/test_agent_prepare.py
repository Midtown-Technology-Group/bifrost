"""Private mechanical codec controls, never installed or runtime readiness."""

import io
import hashlib
import json
import struct
from pathlib import Path
import pytest
from src.runtime_protocol import control
from src.runtime_protocol.agent_prepare import (
    AgentPrepareCodec,
    validate_prepared_binding,
)

CORPUS = (
    Path(__file__).resolve().parents[3]
    / "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/agent-prepare-vectors.json"
)
VECTORS = json.loads(CORPUS.read_bytes())["wire"]


def named(name):
    return next(
        (value["json"].encode("utf-8") for value in VECTORS if value["name"] == name)
    )


@pytest.mark.parametrize("vector", VECTORS, ids=lambda value: value["name"])
def test_shared_corpus(vector):
    codec = AgentPrepareCodec()
    if vector["expected"] == "ok":
        codec.decode_json(vector["json"].encode("utf-8"))
    else:
        with pytest.raises(control.ProtocolError) as error:
            codec.decode_json(vector["json"].encode("utf-8"))
        assert error.value.code.value == vector["expected"]


@pytest.mark.parametrize(
    "name", ["prepare-parent", "raw-whitespace", "raw-float-spellings"]
)
def test_actual_original_reemit_and_consumption(name):
    codec = AgentPrepareCodec()
    payload = named(name)
    decoded = codec.decode_json(payload)
    digest = decoded.payload_sha256
    encoded = codec.encode_retained(decoded)
    output = io.BytesIO()
    codec.write_frame(output, encoded)
    assert output.getvalue()[4:] == payload
    assert encoded.payload_sha256 == digest
    assert digest == hashlib.sha256(payload).hexdigest()
    with pytest.raises(control.ProtocolError):
        codec.encode_retained(decoded)
    with pytest.raises(control.ProtocolError):
        _ = decoded.frame


def test_byte_different_hashes():
    codec = AgentPrepareCodec()
    assert (
        codec.decode_json(named("prepare-parent")).payload_sha256
        != codec.decode_json(named("raw-whitespace")).payload_sha256
    )


def test_fresh_typed_revalidates():
    codec = AgentPrepareCodec()
    frame = codec.decode_json(named("raw-float-spellings")).frame
    encoded = codec.encode_typed(frame)
    output = io.BytesIO()
    codec.write_frame(output, encoded)
    assert b"1.2300e+02" in output.getvalue()
    assert (
        codec.read_frame(io.BytesIO(output.getvalue())).payload_sha256
        == encoded.payload_sha256
    )


def test_readonly_view_has_no_mutable_alias():
    codec = AgentPrepareCodec()
    frame = codec.decode_json(named("prepare-parent")).frame
    with pytest.raises(AttributeError):
        frame.body.limits.configured_max_iterations = 100
    assert isinstance(frame.body.tools, tuple)
    assert not hasattr(frame.body.prompt.input_data, "raw")
    assert not hasattr(frame.body.prompt.input_data, "serialize")


def test_actual_binding_not_supplied_hash():
    codec = AgentPrepareCodec()
    hello = codec.decode_json(named("hello"))
    prepare = codec.decode_json(named("prepare-parent"))
    prepared = codec.decode_json(named("prepared-unexpected-observations"))
    validate_prepared_binding(prepared, prepare, hello)
    changed = codec.decode_json(named("raw-whitespace"))
    with pytest.raises(control.ProtocolError):
        validate_prepared_binding(prepared, changed, hello)


def test_source_optional_absence_preserved():
    codec = AgentPrepareCodec()
    encoded = codec.encode_typed(
        codec.decode_json(named("prepare-solution_deployment")).frame
    )
    output = io.BytesIO()
    codec.write_frame(output, encoded)
    data = json.loads(output.getvalue()[4:])["body"]["staged_closure"][
        "execution_evidence"
    ]["data"]
    assert "workflow_runtime_bounds" not in data
    assert "workflow_parameters_schema" not in data


@pytest.mark.parametrize("name", ["prepare-parent", "prepared-unexpected-observations"])
def test_existing_control_rejects(name):
    with pytest.raises(control.ProtocolError) as error:
        control.decode_json(named(name))
    assert error.value.code is control.ErrorCode.UNSUPPORTED_FRAME


@pytest.mark.parametrize(
    "raw,code",
    [(b"\xff", control.ErrorCode.INVALID_JSON), (b"", control.ErrorCode.INVALID_JSON)],
)
def test_non_json(raw, code):
    with pytest.raises(control.ProtocolError) as error:
        AgentPrepareCodec().decode_json(raw)
    assert error.value.code is code


def test_framing_boundaries():
    codec = AgentPrepareCodec()
    assert codec.read_frame(io.BytesIO()) is None
    for raw, code in [
        (b"\x00\x00", control.ErrorCode.TRUNCATED_FRAME),
        (b"\x00" * 4, control.ErrorCode.INVALID_FRAME),
        (
            struct.pack("!I", control.MAX_FRAME_BYTES + 1),
            control.ErrorCode.FRAME_TOO_LARGE,
        ),
    ]:
        with pytest.raises(control.ProtocolError) as error:
            codec.read_frame(io.BytesIO(raw))
        assert error.value.code is code


def test_complete_wire_cap():
    payload = named("prepare-parent")
    padding = control.MAX_FRAME_BYTES - len(payload)
    full = payload.replace(b"synthetic only", b"synthetic only" + b"x" * padding)
    assert len(full) == control.MAX_FRAME_BYTES
    AgentPrepareCodec().decode_json(full)
    with pytest.raises(control.ProtocolError) as error:
        AgentPrepareCodec().decode_json(full + b" ")
    assert error.value.code is control.ErrorCode.FRAME_TOO_LARGE


@pytest.mark.parametrize("depth", [63, 64, 65])
def test_depth(depth):
    raw = ("[" * depth + "0" + "]" * depth).encode()
    with pytest.raises(control.ProtocolError) as error:
        AgentPrepareCodec().decode_json(raw)
    assert error.value.code is (
        control.ErrorCode.INVALID_FRAME
        if depth <= 64
        else control.ErrorCode.INVALID_JSON
    )


def test_escaped_numeric_looking_string():
    AgentPrepareCodec().parse_business_json(
        b'{"string":"9999999999999999999999999999999"}'
    )


def test_required_fields_at_every_seeded_structural_depth():
    codec = AgentPrepareCodec()
    business_maps = {
        "input_data",
        "output_schema",
        "parameters",
        "workflow_parameters_schema",
        "deployment_source_hashes",
        "workspace_release_source_hashes",
        "workflow_runtime_bounds",
    }
    for seed in [
        "prepare-parent",
        "prepare-solution_deployment",
        "prepare-workspace_release",
        "prepared-unexpected-observations",
    ]:
        value = json.loads(named(seed))
        work = [(value, ())]
        while work:
            node, path = work.pop()
            if isinstance(node, dict):
                for key in node:
                    optional = key in {
                        "verified_role_ids",
                        "workflow_parameters_schema",
                    } or (
                        key == "workflow_runtime_bounds"
                        and seed == "prepare-solution_deployment"
                    )
                    if not optional:
                        changed = json.loads(named(seed))
                        target = changed
                        for part in path:
                            target = target[part]
                        del target[key]
                        with pytest.raises(control.ProtocolError) as error:
                            codec.decode_json(json.dumps(changed).encode())
                        assert error.value.code is control.ErrorCode.INVALID_FRAME
                    if key not in business_maps:
                        work.append((node[key], path + (key,)))
            elif isinstance(node, list):
                work.extend((item, path + (index,)) for index, item in enumerate(node))


def test_mutable_input_is_copied_before_retained_ownership():
    original = named("prepare-parent")
    mutable = bytearray(original)
    codec = AgentPrepareCodec()
    decoded = codec.decode_json(mutable)
    mutable[:] = b"x" * len(mutable)
    output = io.BytesIO()
    codec.write_frame(output, codec.encode_retained(decoded))
    assert output.getvalue()[4:] == original


def test_fragmented_reader_and_partial_writer():
    class Reader(io.BytesIO):
        def read(self, count=-1):
            return super().read(min(count, 1))

    class Writer(io.BytesIO):
        def write(self, block):
            return super().write(bytes(block[:1]))

    codec = AgentPrepareCodec()
    output = Writer()
    codec.write_frame(
        output, codec.encode_retained(codec.decode_json(named("prepare-parent")))
    )
    decoded = codec.read_frame(Reader(output.getvalue()))
    assert decoded.payload_sha256 == hashlib.sha256(named("prepare-parent")).hexdigest()


@pytest.mark.parametrize(
    "outcome", [OSError("private"), ValueError("private"), None, "x", b"xx"]
)
def test_initial_read_failure_stops_without_extra_reads(outcome):
    class Stream:
        calls = 0

        def read(self, size):
            self.calls += 1
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    stream = Stream()
    with pytest.raises(control.ProtocolError) as caught:
        AgentPrepareCodec().read_frame(stream)
    assert caught.value.code == control.ErrorCode.IO
    assert caught.value.__context__ is None
    assert stream.calls == 1


@pytest.mark.parametrize("interrupt_at", [0, 1, 4])
def test_interrupted_read_continues_same_frame(interrupt_at):
    payload = named("hello")
    wire = struct.pack("!I", len(payload)) + payload

    class Stream:
        offset = 0
        interrupted = False

        def read(self, size):
            if self.offset == interrupt_at and not self.interrupted:
                self.interrupted = True
                raise InterruptedError
            block = wire[self.offset : self.offset + 1]
            self.offset += len(block)
            return block

    stream = Stream()
    decoded = AgentPrepareCodec().read_frame(stream)
    assert decoded.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert stream.interrupted
    assert stream.offset == len(wire)


def test_interrupted_write_continues_same_output():
    codec = AgentPrepareCodec()
    payload = named("hello")
    encoded = codec.encode_retained(codec.decode_json(payload))

    class Stream:
        output = bytearray()
        interrupted = False

        def write(self, data):
            if len(self.output) == 1 and not self.interrupted:
                self.interrupted = True
                raise InterruptedError
            self.output.extend(data[:1])
            return 1

    stream = Stream()
    codec.write_frame(stream, encoded)
    assert stream.interrupted
    assert bytes(stream.output) == struct.pack("!I", len(payload)) + payload


def test_every_nullable_and_optional_field_distinction():
    corpus = json.loads(CORPUS.read_bytes())
    codec = AgentPrepareCodec()
    assert len(corpus["coverage"]) == 31
    assert len({item["schema"] for item in corpus["coverage"]}) == 31
    assert (
        sum(
            rule.startswith("?")
            for item in corpus["coverage"]
            for rule in item["rules"].values()
        )
        == 30
    )
    assert (
        sum(
            rule.startswith("~")
            for item in corpus["coverage"]
            for rule in item["rules"].values()
        )
        == 3
    )
    for item in corpus["coverage"]:
        for field, rule in item["rules"].items():
            if not rule.startswith(("?", "~")):
                continue
            base = json.loads(named(item["seed"]))
            target = base
            for component in item["path"]:
                target = target[component]
            target.pop(field, None)
            raw = json.dumps(base).encode()
            if rule.startswith("~"):
                codec.decode_json(raw)
            else:
                with pytest.raises(control.ProtocolError) as caught:
                    codec.decode_json(raw)
                assert caught.value.code is control.ErrorCode.INVALID_FRAME
            target[field] = None
            if item["schema"] == "AgentBinding" and field == "expected_image_digest":
                base["body"]["staged_closure"]["runtime_expected"]["image_digest"] = (
                    None
                )
            if item["schema"] == "PrepareArtifact" and field == "image_digest":
                base["body"]["binding"]["expected_image_digest"] = None
            raw = json.dumps(base).encode()
            if rule.startswith("?"):
                codec.decode_json(raw)
            else:
                with pytest.raises(control.ProtocolError) as caught:
                    codec.decode_json(raw)
                assert caught.value.code is control.ErrorCode.INVALID_FRAME


@pytest.mark.parametrize(
    "case",
    [
        "hello-message",
        "hello-session",
        "prepare-message",
        "slot",
        "prepare-hash",
        "hello-hash",
    ],
)
def test_binding_rejects_distinct_actual_owners(case):
    codec = AgentPrepareCodec()
    hello_raw = json.loads(named("hello"))
    prepare_raw = json.loads(named("prepare-parent"))
    prepared_raw = json.loads(named("prepared-unexpected-observations"))
    other = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    if case == "hello-message":
        hello_raw["message_id"] = other
    elif case == "hello-session":
        hello_raw["session_id"] = other
    elif case == "prepare-message":
        prepare_raw["message_id"] = other
        prepare_raw["body"]["binding"]["prepare_message_id"] = other
    elif case == "slot":
        prepared_raw["body"]["provision_slot_id"] = other
    elif case == "prepare-hash":
        # Same valid facts, different complete bytes owned by the decoder.
        prepare_raw = dict(reversed(list(prepare_raw.items())))
    else:
        hello_raw = dict(reversed(list(hello_raw.items())))
    hello_bytes = (
        json.dumps(hello_raw).encode() if case.startswith("hello-") else named("hello")
    )
    prepare_bytes = (
        json.dumps(prepare_raw).encode()
        if case.startswith("prepare-")
        else named("prepare-parent")
    )
    if case not in {"prepare-hash", "hello-hash"}:
        prepared_raw["body"]["hello_payload_sha256"] = hashlib.sha256(
            hello_bytes
        ).hexdigest()
        prepared_raw["body"]["prepare_payload_sha256"] = hashlib.sha256(
            prepare_bytes
        ).hexdigest()
    hello = codec.decode_json(hello_bytes)
    prepare = codec.decode_json(prepare_bytes)
    prepared = codec.decode_json(json.dumps(prepared_raw).encode())
    with pytest.raises(control.ProtocolError) as caught:
        validate_prepared_binding(prepared, prepare, hello)
    assert caught.value.code is control.ErrorCode.INVALID_FRAME


@pytest.mark.parametrize(
    "name", ["retained-reordered-prepare", "fresh-business-map-extra-key"]
)
def test_new_positive_retained_and_fresh_custody(name):
    codec = AgentPrepareCodec()
    raw = named(name)
    retained = codec.encode_retained(codec.decode_json(raw))
    output = io.BytesIO()
    codec.write_frame(output, retained)
    assert output.getvalue()[4:] == raw
    assert retained.payload_sha256 == hashlib.sha256(raw).hexdigest()
    fresh = codec.encode_typed(codec.decode_json(raw).frame)
    output = io.BytesIO()
    codec.write_frame(output, fresh)
    codec.decode_json(output.getvalue()[4:])
    assert fresh.payload_sha256 == hashlib.sha256(output.getvalue()[4:]).hexdigest()
