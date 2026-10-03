"""Explicit private agent facts codec; no runtime/source/provider authority."""

from __future__ import annotations
import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import ClassVar, TypeVar
from . import control

PROFILE = "agent_prepare_profile/v1"


def _fail(code=control.ErrorCode.INVALID_FRAME):
    raise control.ProtocolError(code)


class _Float:
    __slots__ = ("__lexeme",)

    def __init__(self, lexeme):
        if not math.isfinite(float(lexeme)):
            _fail(control.ErrorCode.INVALID_JSON)
        object.__setattr__(self, "_Float__lexeme", lexeme)

    def __setattr__(self, name, value):
        _fail()

    def _emit(self):
        return self.__lexeme


class RawBusinessJson:
    __slots__ = ("__tree",)

    def __init__(self, tree, *, _key=None):
        if _key is not _PRIVATE:
            _fail()
        self.__tree = _freeze(tree)

    def kind(self):
        return "object"

    def field_names(self):
        return tuple(self.__tree)

    def field(self, name):
        return (
            BusinessView(self.__tree[name], _key=_PRIVATE)
            if name in self.__tree
            else None
        )

    def _emit(self):
        return _emit(self.__tree)


class BusinessView:
    __slots__ = ("__value",)

    def __init__(self, value, *, _key=None):
        if _key is not _PRIVATE:
            _fail()
        self.__value = value

    def kind(self):
        value = self.__value
        return (
            "object"
            if isinstance(value, Mapping)
            else "array"
            if isinstance(value, tuple)
            else "number"
            if isinstance(value, _Float) or type(value) is int
            else "bool"
            if type(value) is bool
            else "null"
            if value is None
            else "string"
        )

    def field(self, name):
        if not isinstance(self.__value, Mapping):
            _fail()
        return (
            BusinessView(self.__value[name], _key=_PRIVATE)
            if name in self.__value
            else None
        )

    def element(self, index):
        if not isinstance(self.__value, tuple) or type(index) is not int or index < 0:
            _fail()
        return (
            BusinessView(self.__value[index], _key=_PRIVATE)
            if index < len(self.__value)
            else None
        )

    def string(self):
        if type(self.__value) is not str:
            _fail()
        return self.__value

    def boolean(self):
        if type(self.__value) is not bool:
            _fail()
        return self.__value

    def integer(self):
        if type(self.__value) is not int:
            _fail()
        return self.__value


_PRIVATE = object()


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple((_freeze(item) for item in value))
    return value


def _integer(lexeme):
    if lexeme == "-0":
        return _Float(lexeme)
    value = int(lexeme)
    if abs(value) > control.MAX_SAFE_INTEGER:
        _fail(control.ErrorCode.INVALID_JSON)
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail(control.ErrorCode.INVALID_JSON)
        result[key] = value
    return result


def _parse(payload):
    if not payload:
        _fail(control.ErrorCode.INVALID_JSON)
    if len(payload) > control.MAX_FRAME_BYTES:
        _fail(control.ErrorCode.FRAME_TOO_LARGE)
    failure = None
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_Float,
            parse_constant=lambda _: _fail(control.ErrorCode.INVALID_JSON),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        failure = (
            error.code
            if isinstance(error, control.ProtocolError)
            else control.ErrorCode.INVALID_JSON
        )
    if failure is not None:
        _fail(failure)
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth >= control.MAX_DEPTH:
                _fail(control.ErrorCode.INVALID_JSON)
            if isinstance(node, dict):
                stack.extend(((key, depth + 1) for key in node))
                stack.extend(((item, depth + 1) for item in node.values()))
            else:
                stack.extend(((item, depth + 1) for item in node))
        elif isinstance(node, str):
            control._text(node)
    return value


def _emit(value):
    if isinstance(value, _Float):
        return value._emit()
    if isinstance(value, RawBusinessJson):
        return value._emit()
    if isinstance(value, _Evidence):
        return (
            '{"kind":' + json.dumps(value.kind) + ',"data":' + _emit(value.data) + "}"
        )
    if isinstance(value, _Facts):
        return (
            "{"
            + ",".join(
                (
                    json.dumps(field.name) + ":" + _emit(getattr(value, field.name))
                    for field in fields(value)
                    if not (
                        value._rules[field.name].startswith("~")
                        and getattr(value, field.name) is None
                    )
                )
            )
            + "}"
        )
    if isinstance(value, Mapping):
        return (
            "{"
            + ",".join(
                (
                    json.dumps(key, ensure_ascii=False) + ":" + _emit(item)
                    for key, item in value.items()
                )
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join((_emit(item) for item in value)) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class _Facts:
    _rules: ClassVar[dict[str, str]]


@dataclass(frozen=True, repr=False)
class AgentLogical(_Facts):
    kind: str
    run_id: str
    _rules: ClassVar[dict[str, str]] = {"kind": "=agent_run", "run_id": "uuid"}


@dataclass(frozen=True, repr=False)
class AgentAttempt(_Facts):
    kind: str
    attempt_id: str
    attempt_number: int
    _rules: ClassVar[dict[str, str]] = {
        "kind": "=agent_execution_attempt",
        "attempt_id": "uuid",
        "attempt_number": "positive",
    }


@dataclass(frozen=True, repr=False)
class AgentBinding(_Facts):
    supervisor_incarnation_id: str
    session_id: str
    prepare_message_id: str
    process_identity: str
    logical_job: AgentLogical
    attempt: AgentAttempt
    expected_artifact_id: str
    expected_image_digest: str | None
    _rules: ClassVar[dict[str, str]] = {
        "supervisor_incarnation_id": "uuid",
        "session_id": "uuid",
        "prepare_message_id": "uuid",
        "process_identity": "name",
        "logical_job": "AgentLogical",
        "attempt": "AgentAttempt",
        "expected_artifact_id": "name",
        "expected_image_digest": "?name",
    }


@dataclass(frozen=True, repr=False)
class SourceBaseline(_Facts):
    reference_source_main: str
    authored_workspace_revision: str
    selected_manifest_sha256: str
    adapter_source_sha256: str
    mechanical_contract_sha256: str
    _rules: ClassVar[dict[str, str]] = {
        "reference_source_main": "git",
        "authored_workspace_revision": "git",
        "selected_manifest_sha256": "hash",
        "adapter_source_sha256": "hash",
        "mechanical_contract_sha256": "hash",
    }


@dataclass(frozen=True, repr=False)
class CallerFacts(_Facts):
    user_id: str
    email: str
    name: str
    organization_id: str | None
    is_superuser: bool
    is_platform_admin: bool
    is_external: bool
    is_provider_org: bool
    roles: tuple[str, ...]
    verified_role_ids: tuple[str, ...] | None
    _rules: ClassVar[dict[str, str]] = {
        "user_id": "uuid",
        "email": "text",
        "name": "text",
        "organization_id": "?uuid",
        "is_superuser": "bool",
        "is_platform_admin": "bool",
        "is_external": "bool",
        "is_provider_org": "bool",
        "roles": "[text]",
        "verified_role_ids": "~[uuid]",
    }


@dataclass(frozen=True, repr=False)
class EffectiveFacts(_Facts):
    organization_id: str | None
    solution_id: str | None
    solution_install_id: str | None
    agent_id: str
    agent_run_id: str
    caller_user_id: str | None
    caller_email: str | None
    caller_name: str | None
    accounting_org_id: str | None
    trigger_type: str
    trigger_source: str | None
    event_delivery_id: str | None
    artifact_workspace_id: str | None
    _rules: ClassVar[dict[str, str]] = {
        "organization_id": "?uuid",
        "solution_id": "?uuid",
        "solution_install_id": "?uuid",
        "agent_id": "uuid",
        "agent_run_id": "uuid",
        "caller_user_id": "?uuid",
        "caller_email": "?text",
        "caller_name": "?text",
        "accounting_org_id": "?uuid",
        "trigger_type": "text",
        "trigger_source": "?text",
        "event_delivery_id": "?uuid",
        "artifact_workspace_id": "?text",
    }


@dataclass(frozen=True, repr=False)
class AdmissionFacts(_Facts):
    admission_snapshot_id: str
    original_caller: CallerFacts | None
    effective_context: EffectiveFacts
    original_caller_provenance_sha256: str
    _rules: ClassVar[dict[str, str]] = {
        "admission_snapshot_id": "uuid",
        "original_caller": "?CallerFacts",
        "effective_context": "EffectiveFacts",
        "original_caller_provenance_sha256": "hash",
    }


@dataclass(frozen=True, repr=False)
class LimitFacts(_Facts):
    configured_max_iterations: int
    configured_max_token_budget: int
    llm_max_tokens: int | None
    parent_duration_seconds: int
    parent_deadline_monotonic_ms: int
    _rules: ClassVar[dict[str, str]] = {
        "configured_max_iterations": "positive",
        "configured_max_token_budget": "positive",
        "llm_max_tokens": "?positive",
        "parent_duration_seconds": "positive",
        "parent_deadline_monotonic_ms": "uint",
    }


@dataclass(frozen=True, repr=False)
class AgentFacts(_Facts):
    id: str
    name: str
    is_active: bool
    organization_id: str | None
    _rules: ClassVar[dict[str, str]] = {
        "id": "uuid",
        "name": "text",
        "is_active": "bool",
        "organization_id": "?uuid",
    }


@dataclass(frozen=True, repr=False)
class PromptFacts(_Facts):
    system_prompt: str
    input_data: RawBusinessJson | None
    output_schema: RawBusinessJson | None
    _rules: ClassVar[dict[str, str]] = {
        "system_prompt": "text",
        "input_data": "?business",
        "output_schema": "?business",
    }


@dataclass(frozen=True, repr=False)
class ToolFacts(_Facts):
    name: str
    description: str
    parameters: RawBusinessJson
    workflow_id: str
    _rules: ClassVar[dict[str, str]] = {
        "name": "name",
        "description": "text",
        "parameters": "business",
        "workflow_id": "uuid",
    }


@dataclass(frozen=True, repr=False)
class ModelFacts(_Facts):
    slot: int
    profile_id: str
    provider: str
    model: str
    provider_connection_id: str | None
    default_max_tokens: int | None
    anthropic_prompt_cache_supported: bool | None
    openai_transport: str | None
    _rules: ClassVar[dict[str, str]] = {
        "slot": "positive",
        "profile_id": "uuid",
        "provider": "=openai|anthropic|google",
        "model": "name",
        "provider_connection_id": "?uuid",
        "default_max_tokens": "?positive",
        "anthropic_prompt_cache_supported": "?bool",
        "openai_transport": "?=responses|chat_completions",
    }


@dataclass(frozen=True, repr=False)
class EntrypointFacts(_Facts):
    kind: str
    adapter_module: str
    adapter_function: str
    registered_agent_id: str
    _rules: ClassVar[dict[str, str]] = {
        "kind": "=autonomous_agent",
        "adapter_module": "text",
        "adapter_function": "text",
        "registered_agent_id": "uuid",
    }


@dataclass(frozen=True, repr=False)
class StagedEntry(_Facts):
    entry_id: str
    path: str
    kind: str
    expected_byte_length: int
    expected_digest: str
    _rules: ClassVar[dict[str, str]] = {
        "entry_id": "name",
        "path": "name",
        "kind": "=adapter_source|dependency_file|resource_file|manifest_file",
        "expected_byte_length": "uint",
        "expected_digest": "name",
    }


@dataclass(frozen=True, repr=False)
class ExpectedNamespace(_Facts):
    module_name: str
    kind: str
    staged_paths: tuple[str, ...]
    entry_ids: tuple[str, ...]
    expected_origins: tuple[str, ...]
    _rules: ClassVar[dict[str, str]] = {
        "module_name": "name",
        "kind": "=module|package|implicit_namespace",
        "staged_paths": "[text]",
        "entry_ids": "[text]",
        "expected_origins": "[text]",
    }


@dataclass(frozen=True, repr=False)
class PrepareInterpreter(_Facts):
    implementation: str
    version: str
    _rules: ClassVar[dict[str, str]] = {"implementation": "name", "version": "name"}


@dataclass(frozen=True, repr=False)
class PrepareSdk(_Facts):
    distribution: str | None
    version: str | None
    _rules: ClassVar[dict[str, str]] = {"distribution": "?name", "version": "?name"}


@dataclass(frozen=True, repr=False)
class PrepareArtifact(_Facts):
    artifact_id: str
    image_digest: str | None
    interpreter: PrepareInterpreter
    sdk: PrepareSdk
    requirements_lock_sha256: str | None
    runtime_protocol: str
    _rules: ClassVar[dict[str, str]] = {
        "artifact_id": "name",
        "image_digest": "?name",
        "interpreter": "PrepareInterpreter",
        "sdk": "PrepareSdk",
        "requirements_lock_sha256": "?hash",
        "runtime_protocol": "=bifrost.runtime/v1",
    }


@dataclass(frozen=True, repr=False)
class SolutionEvidence(_Facts):
    solution_id: str
    solution_deployment_id: str
    bundle_hash: str
    compiled_manifest_hash: str
    workflow_source_hash: str
    git_commit_sha: str | None
    workflow_organization_id: str | None
    runtime_storage_prefix: str
    workflow_portable_ref: str
    workflow_name: str
    workflow_function_name: str
    workflow_path: str
    workflow_execution_mode: str
    workflow_type: str
    workflow_timeout_seconds: int
    workflow_time_saved: int
    workflow_cache_ttl_seconds: int
    workflow_value: _Float | int
    solution_global_repo_access: bool
    deployment_source_hashes: Mapping[str, str]
    workflow_runtime_bounds: Mapping[str, int] | None
    workflow_parameters_schema: RawBusinessJson | None
    _rules: ClassVar[dict[str, str]] = {
        "solution_id": "uuid",
        "solution_deployment_id": "uuid",
        "bundle_hash": "text",
        "compiled_manifest_hash": "text",
        "workflow_source_hash": "text",
        "git_commit_sha": "?text",
        "workflow_organization_id": "?text",
        "runtime_storage_prefix": "text",
        "workflow_portable_ref": "text",
        "workflow_name": "text",
        "workflow_function_name": "text",
        "workflow_path": "text",
        "workflow_execution_mode": "text",
        "workflow_type": "text",
        "workflow_timeout_seconds": "signed",
        "workflow_time_saved": "signed",
        "workflow_cache_ttl_seconds": "signed",
        "workflow_value": "number",
        "solution_global_repo_access": "bool",
        "deployment_source_hashes": "strings",
        "workflow_runtime_bounds": "~bounds",
        "workflow_parameters_schema": "~business",
    }


@dataclass(frozen=True, repr=False)
class WorkspaceEvidence(_Facts):
    schema_version: str
    workspace_release_row_id: str
    workspace_release_artifact_id: str
    workflow_id: str
    workspace_release_id: str
    workspace_release_effective_manifest_id: str
    workspace_release_governed_manifest_id: str
    workspace_release_registration_manifest_id: str
    workspace_release_runtime_storage_prefix: str
    workspace_release_source_commit_sha: str
    workspace_release_source_tree_sha: str
    workspace_release_registration_state_fingerprint: str
    workflow_name: str
    workflow_function_name: str
    workflow_path: str
    workflow_source_hash: str
    workflow_execution_mode: str
    workflow_type: str
    workspace_release_source_hashes: Mapping[str, str]
    workflow_runtime_bounds: Mapping[str, int]
    workflow_timeout_seconds: int
    workflow_time_saved: int
    workflow_cache_ttl_seconds: int
    workflow_value: _Float | int
    workflow_organization_id: str | None
    _rules: ClassVar[dict[str, str]] = {
        "schema_version": "=bifrost.workspace-release-runtime/v1",
        "workspace_release_row_id": "uuid",
        "workspace_release_artifact_id": "uuid",
        "workflow_id": "uuid",
        "workspace_release_id": "prefixed",
        "workspace_release_effective_manifest_id": "prefixed",
        "workspace_release_governed_manifest_id": "prefixed",
        "workspace_release_registration_manifest_id": "prefixed",
        "workspace_release_runtime_storage_prefix": "text",
        "workspace_release_source_commit_sha": "text",
        "workspace_release_source_tree_sha": "text",
        "workspace_release_registration_state_fingerprint": "text",
        "workflow_name": "text",
        "workflow_function_name": "text",
        "workflow_path": "text",
        "workflow_source_hash": "text",
        "workflow_execution_mode": "text",
        "workflow_type": "text",
        "workspace_release_source_hashes": "strings",
        "workflow_runtime_bounds": "bounds",
        "workflow_timeout_seconds": "signed",
        "workflow_time_saved": "signed",
        "workflow_cache_ttl_seconds": "signed",
        "workflow_value": "number",
        "workflow_organization_id": "?text",
    }


@dataclass(frozen=True, repr=False)
class ParentAgentEvidence(_Facts):
    admission_snapshot_id: str
    agent_id: str
    authored_agent_manifest_sha256: str
    source_observation_id: str
    _rules: ClassVar[dict[str, str]] = {
        "admission_snapshot_id": "uuid",
        "agent_id": "uuid",
        "authored_agent_manifest_sha256": "hash",
        "source_observation_id": "uuid",
    }


@dataclass(frozen=True, repr=False)
class StagedClosure(_Facts):
    entrypoint: EntrypointFacts
    execution_evidence: _Evidence
    entries: tuple[StagedEntry, ...]
    namespace: tuple[ExpectedNamespace, ...]
    runtime_expected: PrepareArtifact
    _rules: ClassVar[dict[str, str]] = {
        "entrypoint": "EntrypointFacts",
        "execution_evidence": "evidence",
        "entries": "[StagedEntry]",
        "namespace": "[ExpectedNamespace]",
        "runtime_expected": "PrepareArtifact",
    }


@dataclass(frozen=True, repr=False)
class PrepareFacts(_Facts):
    type: str
    binding: AgentBinding
    source_baseline: SourceBaseline
    staged_closure: StagedClosure
    admission: AdmissionFacts
    limits: LimitFacts
    agent: AgentFacts
    prompt: PromptFacts
    tools: tuple[ToolFacts, ...]
    model_chain: tuple[ModelFacts, ...]
    provision_slot_id: str
    loop_profile: str
    _rules: ClassVar[dict[str, str]] = {
        "type": "=Prepare",
        "binding": "AgentBinding",
        "source_baseline": "SourceBaseline",
        "staged_closure": "StagedClosure",
        "admission": "AdmissionFacts",
        "limits": "LimitFacts",
        "agent": "AgentFacts",
        "prompt": "PromptFacts",
        "tools": "[ToolFacts]",
        "model_chain": "[ModelFacts]",
        "provision_slot_id": "uuid",
        "loop_profile": "name",
    }


@dataclass(frozen=True, repr=False)
class ObservedEntry(_Facts):
    entry_id: str
    observed_path: str
    observed_byte_length: int
    observed_sha256: str
    _rules: ClassVar[dict[str, str]] = {
        "entry_id": "text",
        "observed_path": "text",
        "observed_byte_length": "uint",
        "observed_sha256": "hash",
    }


@dataclass(frozen=True, repr=False)
class ObservedNamespace(_Facts):
    module_name: str
    kind: str
    observed_paths: tuple[str, ...]
    origin_entry_ids: tuple[str, ...]
    shadowing_origins: tuple[str, ...]
    _rules: ClassVar[dict[str, str]] = {
        "module_name": "text",
        "kind": "=module|package|implicit_namespace",
        "observed_paths": "[text]",
        "origin_entry_ids": "[text]",
        "shadowing_origins": "[text]",
    }


@dataclass(frozen=True, repr=False)
class ObservedFile(_Facts):
    path: str
    byte_length: int
    sha256: str
    _rules: ClassVar[dict[str, str]] = {
        "path": "text",
        "byte_length": "uint",
        "sha256": "hash",
    }


@dataclass(frozen=True, repr=False)
class ObservedPackage(_Facts):
    distribution: str
    version: str
    installed_path: str
    files: tuple[ObservedFile, ...]
    _rules: ClassVar[dict[str, str]] = {
        "distribution": "text",
        "version": "text",
        "installed_path": "text",
        "files": "[ObservedFile]",
    }


@dataclass(frozen=True, repr=False)
class ObservedArtifact(_Facts):
    artifact_id: str
    image_digest: str | None
    interpreter: PrepareInterpreter
    sdk: PrepareSdk
    requirements_lock_sha256: str | None
    runtime_protocol: str
    launch_artifact_files: tuple[ObservedFile, ...]
    interpreter_executable: ObservedFile
    sdk_files: tuple[ObservedFile, ...]
    packages: tuple[ObservedPackage, ...]
    _rules: ClassVar[dict[str, str]] = {
        "artifact_id": "name",
        "image_digest": "?name",
        "interpreter": "PrepareInterpreter",
        "sdk": "PrepareSdk",
        "requirements_lock_sha256": "?hash",
        "runtime_protocol": "=bifrost.runtime/v1",
        "launch_artifact_files": "[ObservedFile]",
        "interpreter_executable": "ObservedFile",
        "sdk_files": "[ObservedFile]",
        "packages": "[ObservedPackage]",
    }


@dataclass(frozen=True, repr=False)
class ObservedStartup(_Facts):
    observed_sys_path: tuple[str, ...]
    site_hook_files: tuple[ObservedFile, ...]
    package_hook_files: tuple[ObservedFile, ...]
    _rules: ClassVar[dict[str, str]] = {
        "observed_sys_path": "[text]",
        "site_hook_files": "[ObservedFile]",
        "package_hook_files": "[ObservedFile]",
    }


@dataclass(frozen=True, repr=False)
class ObservationFacts(_Facts):
    entries: tuple[ObservedEntry, ...]
    namespace: tuple[ObservedNamespace, ...]
    artifact: ObservedArtifact
    startup: ObservedStartup
    _rules: ClassVar[dict[str, str]] = {
        "entries": "[ObservedEntry]",
        "namespace": "[ObservedNamespace]",
        "artifact": "ObservedArtifact",
        "startup": "ObservedStartup",
    }


@dataclass(frozen=True, repr=False)
class PreparedFacts(_Facts):
    type: str
    session_id: str
    prepare_message_id: str
    prepare_payload_sha256: str
    provision_slot_id: str
    hello_message_id: str
    hello_payload_sha256: str
    observations: ObservationFacts
    _rules: ClassVar[dict[str, str]] = {
        "type": "=Prepared",
        "session_id": "uuid",
        "prepare_message_id": "uuid",
        "prepare_payload_sha256": "hash",
        "provision_slot_id": "uuid",
        "hello_message_id": "uuid",
        "hello_payload_sha256": "hash",
        "observations": "ObservationFacts",
    }


_TYPES = {cls.__name__: cls for cls in _Facts.__subclasses__()}


def _decode_rule(value, rule):
    if rule.startswith("?"):
        return None if value is None else _decode_rule(value, rule[1:])
    if rule.startswith("["):
        if not isinstance(value, list):
            _fail()
        return tuple((_decode_rule(item, rule[1:-1]) for item in value))
    if rule.startswith("="):
        if not isinstance(value, str) or value not in rule[1:].split("|"):
            _fail()
        return value
    if rule in _TYPES:
        return _decode_facts(value, _TYPES[rule])
    if rule in {"text", "name", "hash", "git", "prefixed", "uuid"}:
        value = control._text(value, name=rule == "name")
        if rule == "uuid":
            return control.canonical_uuid(value)
        if (
            rule in {"hash", "git"}
            and re.fullmatch(
                "[0-9a-f]{" + str(64 if rule == "hash" else 40) + "}", value
            )
            is None
        ):
            _fail()
        if rule == "prefixed" and re.fullmatch("sha256:[0-9a-f]{64}", value) is None:
            _fail()
        return value
    if rule == "bool":
        if type(value) is not bool:
            _fail()
        return value
    if rule in {"uint", "positive", "signed"}:
        if rule == "signed":
            if type(value) is not int or abs(value) > control.MAX_SAFE_INTEGER:
                _fail()
            return value
        return control.integer(value, positive=rule == "positive")
    if rule == "number":
        if type(value) is not int and (not isinstance(value, _Float)):
            _fail()
        return value
    if rule == "business":
        if not isinstance(value, dict):
            _fail()
        return RawBusinessJson(value, _key=_PRIVATE)
    if rule in {"strings", "bounds"}:
        if not isinstance(value, dict):
            _fail()
        result = {
            control._text(key): _decode_rule(
                item, "text" if rule == "strings" else "positive"
            )
            for key, item in value.items()
        }
        if rule == "bounds" and (
            not {
                "max_duration_seconds",
                "max_external_calls",
                "max_records_read",
                "max_output_bytes",
            }
            <= set(result)
        ):
            _fail()
        return MappingProxyType(result)
    if rule == "evidence":
        obj = control._fields(value, ("kind", "data"))
        types = {
            "solution_deployment": SolutionEvidence,
            "workspace_release": WorkspaceEvidence,
            "parent_admitted_agent": ParentAgentEvidence,
        }
        if not isinstance(obj["kind"], str) or obj["kind"] not in types:
            _fail()
        return _Evidence(obj["kind"], _decode_facts(obj["data"], types[obj["kind"]]))
    _fail()


@dataclass(frozen=True, repr=False)
class _Evidence:
    kind: str
    data: _Facts


_F = TypeVar("_F", bound=_Facts)


def _decode_facts(value: object, cls: type[_F]) -> _F:
    if not isinstance(value, dict):
        _fail()
    required = {key for key, rule in cls._rules.items() if not rule.startswith("~")}
    if not required <= set(value) or not set(value) <= set(cls._rules):
        _fail()
    return cls(
        **{
            key: None
            if rule.startswith("~") and key not in value
            else _decode_rule(value[key], rule.removeprefix("~"))
            for key, rule in cls._rules.items()
        }
    )


@dataclass(frozen=True, repr=False)
class AgentFrame:
    session_id: str
    message_id: str
    sequence: int
    correlation_id: str | None
    body: PrepareFacts | PreparedFacts | control.Body


class RawPayload:
    __slots__ = ("__bytes",)

    def __init__(self, payload, *, _key=None):
        if _key is not _PRIVATE:
            _fail()
        self.__bytes = bytes(payload)

    def _take(self):
        if self.__bytes is None:
            _fail()
        result = self.__bytes
        self.__bytes = None
        return result


class DecodedAgentFrame:
    __slots__ = ("__frame", "__payload", "__hash")

    def __init__(
        self, frame: AgentFrame, payload: bytes, payload_sha256: str, *, _key=None
    ):
        if _key is not _PRIVATE:
            _fail()
        self.__frame = frame
        self.__payload = RawPayload(payload, _key=_PRIVATE)
        self.__hash = payload_sha256

    @property
    def frame(self):
        if self.__payload is None:
            _fail()
        return self.__frame

    @property
    def payload_sha256(self):
        if self.__payload is None:
            _fail()
        return self.__hash

    def _consume(self):
        if self.__payload is None:
            _fail()
        result = (self.__frame, self.__payload._take(), self.__hash)
        self.__payload = None
        self.__frame = None
        return result


class EncodedAgentFrame:
    __slots__ = ("__wire", "__hash")

    def __init__(self, payload, *, _key=None):
        if _key is not _PRIVATE:
            _fail()
        self.__wire = struct.pack("!I", len(payload)) + payload
        self.__hash = hashlib.sha256(payload).hexdigest()

    @property
    def payload_sha256(self):
        return self.__hash

    def _write(self, stream):
        view = memoryview(self.__wire)
        while view:
            try:
                count = stream.write(view)
            except InterruptedError:
                continue
            except (OSError, ValueError):
                count = None
            if type(count) is not int or count <= 0 or count > len(view):
                _fail(control.ErrorCode.IO)
            view = view[count:]


class AgentPrepareCodec:
    def parse_business_json(self, payload):
        if not isinstance(payload, (bytes, bytearray)):
            _fail(control.ErrorCode.INVALID_JSON)
        if len(payload) > control.MAX_FRAME_BYTES:
            _fail(control.ErrorCode.FRAME_TOO_LARGE)
        return _decode_rule(_parse(bytes(payload)), "business")

    def decode_json(self, payload):
        if not isinstance(payload, (bytes, bytearray)):
            _fail(control.ErrorCode.INVALID_JSON)
        if len(payload) > control.MAX_FRAME_BYTES:
            _fail(control.ErrorCode.FRAME_TOO_LARGE)
        payload = bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        obj = control._fields(
            _parse(payload),
            (
                "protocol",
                "type",
                "session_id",
                "message_id",
                "sequence",
                "correlation_id",
                "body",
            ),
        )
        if control._text(obj["protocol"]) != control.PROTOCOL:
            _fail(control.ErrorCode.UNSUPPORTED_PROTOCOL)
        kind = control._text(obj["type"], name=True)
        header = (
            control.canonical_uuid(obj["session_id"]),
            control.canonical_uuid(obj["message_id"]),
            control.integer(obj["sequence"], positive=True),
            control._nullable(obj["correlation_id"], control.canonical_uuid),
        )
        if kind not in {"Prepare", "Prepared"}:
            frame = control.decode_json(payload)
            return DecodedAgentFrame(
                AgentFrame(*header, frame.body), payload, digest, _key=_PRIVATE
            )
        body = _decode_facts(
            obj["body"], PrepareFacts if kind == "Prepare" else PreparedFacts
        )
        frame = AgentFrame(*header, body)
        _associations(frame)
        return DecodedAgentFrame(frame, payload, digest, _key=_PRIVATE)

    def encode_retained(self, decoded):
        if not isinstance(decoded, DecodedAgentFrame):
            _fail()
        _, payload, _ = decoded._consume()
        return EncodedAgentFrame(payload, _key=_PRIVATE)

    def encode_typed(self, frame):
        failure = None
        try:
            return self._encode_typed(frame)
        except (AttributeError, TypeError, ValueError, RecursionError) as error:
            failure = (
                error.code
                if isinstance(error, control.ProtocolError)
                else control.ErrorCode.INVALID_FRAME
            )
        _fail(failure)

    def _encode_typed(self, frame):
        if not isinstance(frame, AgentFrame):
            _fail()
        if isinstance(frame.body, (PrepareFacts, PreparedFacts)):
            _associations(frame)
            payload = (
                '{"protocol":'
                + json.dumps(control.PROTOCOL)
                + ',"type":'
                + json.dumps(frame.body.type)
                + ',"session_id":'
                + json.dumps(frame.session_id)
                + ',"message_id":'
                + json.dumps(frame.message_id)
                + ',"sequence":'
                + _emit(frame.sequence)
                + ',"correlation_id":'
                + _emit(frame.correlation_id)
                + ',"body":'
                + _emit(frame.body)
                + "}"
            ).encode("utf-8")
        else:
            payload = control.encode_frame(
                control.Frame(
                    frame.session_id,
                    frame.message_id,
                    frame.sequence,
                    frame.correlation_id,
                    frame.body,
                )
            )[4:]
        self.decode_json(payload)
        return EncodedAgentFrame(payload, _key=_PRIVATE)

    def write_frame(self, stream, encoded):
        if not isinstance(encoded, EncodedAgentFrame):
            _fail()
        encoded._write(stream)

    def read_frame(self, stream):
        while True:
            try:
                first = stream.read(1)
            except InterruptedError:
                continue
            except (OSError, ValueError):
                first = None
            break
        if not isinstance(first, bytes) or len(first) > 1:
            _fail(control.ErrorCode.IO)
        if first == b"":
            return None
        prefix = first + _read_exact(stream, 3)
        length = struct.unpack("!I", prefix)[0]
        if not length:
            _fail()
        if length > control.MAX_FRAME_BYTES:
            _fail(control.ErrorCode.FRAME_TOO_LARGE)
        return self.decode_json(_read_exact(stream, length))


def _read_exact(stream, length):
    result = bytearray()
    while len(result) < length:
        try:
            block = stream.read(length - len(result))
        except InterruptedError:
            continue
        except (OSError, ValueError):
            block = None
        if not isinstance(block, bytes):
            _fail(control.ErrorCode.IO)
        if not block:
            _fail(control.ErrorCode.TRUNCATED_FRAME)
        if len(block) > length - len(result):
            _fail(control.ErrorCode.IO)
        result.extend(block)
    return bytes(result)


def _associations(frame):
    body = frame.body
    if isinstance(body, PrepareFacts):
        binding = body.binding
        if (
            frame.correlation_id is not None
            or binding.session_id != frame.session_id
            or binding.prepare_message_id != frame.message_id
        ):
            _fail()
        if (
            body.agent.id != body.admission.effective_context.agent_id
            or body.agent.id != body.staged_closure.entrypoint.registered_agent_id
            or binding.logical_job.run_id
            != body.admission.effective_context.agent_run_id
        ):
            _fail()
        if (
            binding.expected_artifact_id
            != body.staged_closure.runtime_expected.artifact_id
            or binding.expected_image_digest
            != body.staged_closure.runtime_expected.image_digest
        ):
            _fail()
        evidence = body.staged_closure.execution_evidence
        if evidence.kind == "parent_admitted_agent" and (
            evidence.data.agent_id != body.agent.id
            or evidence.data.admission_snapshot_id
            != body.admission.admission_snapshot_id
        ):
            _fail()
        if (
            not body.model_chain
            or [item.slot for item in body.model_chain]
            != list(range(1, len(body.model_chain) + 1))
            or len({item.profile_id for item in body.model_chain})
            != len(body.model_chain)
        ):
            _fail()
        ids = [entry.entry_id for entry in body.staged_closure.entries]
        if len(ids) != len(set(ids)) or any(
            (
                identity not in ids
                for node in body.staged_closure.namespace
                for identity in node.entry_ids
            )
        ):
            _fail()
    elif isinstance(body, PreparedFacts):
        if (
            frame.session_id != body.session_id
            or frame.correlation_id != body.prepare_message_id
        ):
            _fail()


def validate_prepared_binding(prepared, prepare, hello):
    if not all(
        (isinstance(item, DecodedAgentFrame) for item in (prepared, prepare, hello))
    ):
        _fail()
    ready, request, greeting = (prepared.frame, prepare.frame, hello.frame)
    if (
        not isinstance(ready.body, PreparedFacts)
        or not isinstance(request.body, PrepareFacts)
        or (not isinstance(greeting.body, control.Hello))
    ):
        _fail()
    value = ready.body
    if (
        ready.session_id != request.session_id
        or ready.session_id != greeting.session_id
        or value.prepare_message_id != request.message_id
        or (value.hello_message_id != greeting.message_id)
        or (value.provision_slot_id != request.body.provision_slot_id)
        or (value.prepare_payload_sha256 != prepare.payload_sha256)
        or (value.hello_payload_sha256 != hello.payload_sha256)
    ):
        _fail()
