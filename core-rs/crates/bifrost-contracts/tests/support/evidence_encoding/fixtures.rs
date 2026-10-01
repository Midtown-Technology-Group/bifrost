//! Decode typed synthetic inputs; expected bytes are never a projection source.

use super::{
    EncodingLimits, EvidenceEncodeError, ProjectedRoot, ProjectedValue,
    encode_delivery_ascii_fixture, encode_finite_sorted_utf8, encode_sorted_ascii_fixture,
    encode_workspace_source_fixture,
};
use serde::Deserialize;
use serde_json::Value;
use std::{collections::BTreeSet, fs, path::PathBuf};

type Result<T> = std::result::Result<T, EvidenceEncodeError>;
const INVALID: EvidenceEncodeError = EvidenceEncodeError::InvalidFixture;
const PLATFORM: &str = "ba783472b770291e612433ad7ca9564fe671dade";

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum Description {
    #[serde(rename = "null")]
    Null {},
    #[serde(rename = "bool")]
    Bool { value: bool },
    #[serde(rename = "int")]
    Int { decimal: String },
    #[serde(rename = "f64")]
    F64 {
        bits_be_hex: String,
        #[serde(default)]
        repr: Option<String>,
    },
    #[serde(rename = "string")]
    String { value: String },
    #[serde(rename = "array")]
    Array { items: Vec<Self> },
    #[serde(rename = "object")]
    Object { entries: Vec<(String, Self)> },
    #[serde(rename = "model")]
    Model {
        class: String,
        model_dump_json: Box<Self>,
    },
}

pub fn decode_root(description: &Description) -> Result<ProjectedRoot> {
    match decode(description)? {
        ProjectedValue::Object(entries) => Ok(ProjectedRoot(entries)),
        _ => Err(INVALID),
    }
}
fn decode(description: &Description) -> Result<ProjectedValue> {
    Ok(match description {
        Description::Null {} => ProjectedValue::Null,
        Description::Bool { value } => ProjectedValue::Bool(*value),
        Description::Int { decimal } => {
            let digits = decimal.strip_prefix('-').unwrap_or(decimal);
            if decimal != "0"
                && (digits.is_empty()
                    || digits.starts_with('0')
                    || !digits.bytes().all(|c| c.is_ascii_digit()))
            {
                return Err(INVALID);
            }
            if decimal.starts_with('-') {
                ProjectedValue::I64(decimal.parse().map_err(|_| INVALID)?)
            } else {
                let value = decimal.parse::<u64>().map_err(|_| INVALID)?;
                match i64::try_from(value) {
                    Ok(value) => ProjectedValue::I64(value),
                    Err(_) => ProjectedValue::U64(value),
                }
            }
        }
        Description::F64 { bits_be_hex, repr } => {
            // Optional repr is retained provenance, not an input/output numeric oracle.
            let _ = repr;
            if bits_be_hex.len() != 16
                || !bits_be_hex
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
            {
                return Err(INVALID);
            }
            ProjectedValue::F64(f64::from_bits(
                u64::from_str_radix(bits_be_hex, 16).map_err(|_| INVALID)?,
            ))
        }
        Description::String { value } => ProjectedValue::String(value.clone()),
        Description::Array { items } => {
            ProjectedValue::Array(items.iter().map(decode).collect::<Result<_>>()?)
        }
        Description::Object { entries } => ProjectedValue::Object(
            entries
                .iter()
                .map(|(key, value)| Ok((key.clone(), decode(value)?)))
                .collect::<Result<_>>()?,
        ),
        // Only the named top-level fixture adapters may interpret model dumps.
        Description::Model { .. } => return Err(INVALID),
    })
}

fn fields(
    entries: &[(String, ProjectedValue)],
    required: &[&str],
    optional: &[&str],
) -> Result<()> {
    let mut seen = BTreeSet::new();
    for (name, _) in entries {
        if !seen.insert(name.as_str())
            || (!required.contains(&name.as_str()) && !optional.contains(&name.as_str()))
        {
            return Err(INVALID);
        }
    }
    if required.iter().any(|name| !seen.contains(name)) {
        return Err(INVALID);
    }
    Ok(())
}
fn member_mut<'a>(
    entries: &'a mut [(String, ProjectedValue)],
    name: &str,
) -> Result<&'a mut ProjectedValue> {
    entries
        .iter_mut()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
        .ok_or(INVALID)
}
fn object_mut(value: &mut ProjectedValue) -> Result<&mut Vec<(String, ProjectedValue)>> {
    match value {
        ProjectedValue::Object(entries) => Ok(entries),
        _ => Err(INVALID),
    }
}
fn omit_nulls(entries: &mut Vec<(String, ProjectedValue)>, names: &[&str]) {
    entries.retain(|(key, value)| !names.contains(&key.as_str()) || *value != ProjectedValue::Null);
}
fn empty_only(entries: &mut Vec<(String, ProjectedValue)>, name: &str, omit: bool) -> Result<()> {
    if let Some((_, value)) = entries.iter().find(|(key, _)| key == name) {
        if !matches!(value, ProjectedValue::Object(items) if items.is_empty()) {
            return Err(INVALID);
        }
    }
    if omit {
        entries.retain(|(key, _)| key != name);
    }
    Ok(())
}
fn controls(entries: &mut Vec<(String, ProjectedValue)>) -> Result<()> {
    fields(
        entries,
        &[
            "display_name",
            "execution_mode",
            "timeout_seconds",
            "cache_ttl_seconds",
            "time_saved",
            "value",
            "retry_policy",
            "access_level",
            "role_ids",
            "endpoint_enabled",
            "public_endpoint",
            "allowed_methods",
            "disable_global_key",
        ],
        &[],
    )?;
    if !matches!(member_mut(entries, "value")?, ProjectedValue::F64(_)) {
        return Err(INVALID);
    }
    fields(
        object_mut(member_mut(entries, "retry_policy")?)?,
        &["version", "enabled", "max_attempts", "retry_on"],
        &[],
    )?;
    omit_nulls(entries, &["display_name"]);
    Ok(())
}
fn manifest(entries: &mut Vec<(String, ProjectedValue)>) -> Result<()> {
    fields(
        entries,
        &[
            "schema_version",
            "solution_id",
            "deployment_id",
            "bundle_hash",
            "resolution_map_hash",
            "source",
            "workflows",
            "agents",
            "forms",
            "events",
            "applications",
            "tables",
            "file_locations",
            "connections",
            "config_requirements",
            "dependencies",
            "git",
        ],
        &["shared_tables", "resources", "root_file_bindings"],
    )?;
    for name in ["shared_tables", "resources", "root_file_bindings"] {
        empty_only(entries, name, true)?;
    }
    // No unobserved shared-table grant/resource/root-binding/dependency model adapter.
    empty_only(entries, "dependencies", false)?;
    fields(
        object_mut(member_mut(entries, "source")?)?,
        &["artifact_key", "runtime_prefix"],
        &[],
    )?;
    for name in [
        "workflows",
        "agents",
        "forms",
        "events",
        "applications",
        "tables",
    ] {
        for (_, value) in object_mut(member_mut(entries, name)?)? {
            let entity = object_mut(value)?;
            fields(
                entity,
                &[
                    "portable_ref",
                    "resolved_id",
                    "definition",
                    "source_ref",
                    "source_hash",
                    "dependency_solution_id",
                ],
                &[],
            )?;
            omit_nulls(
                entity,
                &["source_ref", "source_hash", "dependency_solution_id"],
            );
        }
    }
    let git = object_mut(member_mut(entries, "git")?)?;
    fields(git, &["repository", "resolved_ref", "commit_sha"], &[])?;
    omit_nulls(git, &["repository", "resolved_ref", "commit_sha"]);
    Ok(())
}
fn recipe(entries: &mut Vec<(String, ProjectedValue)>) -> Result<()> {
    fields(
        entries,
        &[
            "schema_version",
            "solution_id",
            "files",
            "workflows",
            "shared_tables",
        ],
        &["resources", "root_file_bindings"],
    )?;
    empty_only(entries, "shared_tables", false)?;
    for name in ["resources", "root_file_bindings"] {
        empty_only(entries, name, true)?;
    }
    let ProjectedValue::Array(workflows) = member_mut(entries, "workflows")? else {
        return Err(INVALID);
    };
    for workflow in workflows {
        let registration = object_mut(workflow)?;
        fields(
            registration,
            &[
                "id",
                "path",
                "function_name",
                "organization_id",
                "runtime_bounds",
                "controls",
            ],
            &[],
        )?;
        let bounds = object_mut(member_mut(registration, "runtime_bounds")?)?;
        fields(
            bounds,
            &[
                "max_duration_seconds",
                "max_external_calls",
                "max_records_read",
                "max_output_bytes",
                "max_records_written",
                "max_output_rows",
                "max_pages",
            ],
            &[],
        )?;
        omit_nulls(
            bounds,
            &["max_records_written", "max_output_rows", "max_pages"],
        );
        controls(object_mut(member_mut(registration, "controls")?)?)?;
        omit_nulls(registration, &["organization_id"]);
    }
    Ok(())
}
fn model(description: &Description) -> Result<ProjectedRoot> {
    let Description::Model {
        class,
        model_dump_json,
    } = description
    else {
        return Err(INVALID);
    };
    let mut root = decode_root(model_dump_json)?;
    match class.as_str() {
        "CompiledDeploymentManifest" => manifest(&mut root.0)?,
        "WorkflowRegistrationControls" => controls(&mut root.0)?,
        "ReviewedWorkflowRecipe" => recipe(&mut root.0)?,
        _ => return Err(INVALID),
    }
    Ok(root)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Producer {
    path: String,
    function: String,
    line: usize,
    #[serde(rename = "ref")]
    source_ref: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Vector {
    pub profile: String,
    pub name: String,
    producer: Producer,
    pub input: Description,
    fixture: String,
    serialized_input: Option<Description>,
    dump_options: Option<Value>,
    pub helper_digest: Option<String>,
    pub outcome: String,
    pub utf8: Option<String>,
    pub hex: Option<String>,
    pub sha256: Option<String>,
    pub digest: Option<String>,
    pub exception_type: Option<String>,
    pub expected_failure_observed: Option<bool>,
}

impl Vector {
    pub fn render(&self) -> Result<Vec<u8>> {
        let limits = EncodingLimits::default();
        match self.profile.as_str() {
            "PythonDeploymentEvidenceV1" => {
                encode_finite_sorted_utf8(&decode_root(&self.input)?, limits)
            }
            "PythonDeploymentDocumentV1" => encode_finite_sorted_utf8(&model(&self.input)?, limits),
            "PythonWorkspaceIdentityV1" => {
                let root = decode_root(&self.input)?;
                if root != decode_root(self.serialized_input.as_ref().ok_or(INVALID)?)? {
                    return Err(INVALID);
                }
                encode_workspace_source_fixture(&root, limits)
            }
            "PythonWorkspaceManifestV1" => {
                let input = decode_root(&self.input)?;
                let mut leaves = Vec::new();
                for (path, value) in input.0 {
                    let ProjectedValue::String(hash) = value else {
                        return Err(INVALID);
                    };
                    let hash = hash.strip_prefix("sha256:").unwrap_or(&hash);
                    if hash.len() != 64
                        || !hash
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                    {
                        return Err(INVALID);
                    }
                    leaves.push((path, ProjectedValue::String(hash.to_owned())));
                }
                let projected = ProjectedRoot(vec![
                    (
                        "schema".to_owned(),
                        ProjectedValue::String("bifrost.workspace-file-manifest/v1".to_owned()),
                    ),
                    ("files".to_owned(), ProjectedValue::Object(leaves)),
                ]);
                let captured = decode_root(self.serialized_input.as_ref().ok_or(INVALID)?)?;
                if projected != captured {
                    return Err(INVALID);
                }
                encode_finite_sorted_utf8(&captured, limits)
            }
            "PythonAttemptPolicyV1" => {
                let mut input = decode_root(&self.input)?.0;
                fields(&input, &["runtime_mode", "retry_policy"], &[])?;
                let projected = ProjectedRoot(vec![
                    (
                        "attempt_policy".to_owned(),
                        ProjectedValue::String("workflow-attempt/v1".to_owned()),
                    ),
                    (
                        "runtime_mode".to_owned(),
                        member_mut(&mut input, "runtime_mode")?.clone(),
                    ),
                    (
                        "execution_retry".to_owned(),
                        member_mut(&mut input, "retry_policy")?.clone(),
                    ),
                ]);
                let captured = decode_root(self.serialized_input.as_ref().ok_or(INVALID)?)?;
                if projected != captured {
                    return Err(INVALID);
                }
                encode_sorted_ascii_fixture(&captured, limits)
            }
            "PythonDeliveryPlaintextV1" => {
                let root = decode_root(&self.input)?;
                if root != decode_root(self.serialized_input.as_ref().ok_or(INVALID)?)? {
                    return Err(INVALID);
                }
                encode_delivery_ascii_fixture(&root, limits)
            }
            _ => Err(INVALID),
        }
    }
}

pub fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 15)]));
    }
    result
}

pub fn load() -> Result<Vec<Vector>> {
    let path = std::env::var_os("BIFROST_RUNTIME_EVIDENCE_VECTORS")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../../contracts/runtime/v1/evidence/evidence-vectors.json")
        });
    // Read through a cap rather than trusting metadata before an unbounded read.
    use std::io::Read;
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|_| INVALID)?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| INVALID)?;
    if bytes.len() > 1024 * 1024 {
        return Err(INVALID);
    }
    let document: Value = serde_json::from_slice(&bytes).map_err(|_| INVALID)?;
    let expected_fields = [
        "schema",
        "procedure",
        "evidence",
        "platform_ref",
        "mounted_foundation_ref",
        "workspace_ref",
        "image",
        "python",
        "isolation",
        "versions",
        "sources",
        "vectors",
        "observations",
        "barriers",
        "vector_count",
        "barrier_count",
    ];
    let metadata = document.as_object().ok_or(INVALID)?;
    if metadata.len() != expected_fields.len()
        || expected_fields
            .iter()
            .any(|key| !metadata.contains_key(*key))
    {
        return Err(INVALID);
    }
    for (key, expected) in [
        ("schema", "bifrost.synthetic-canonical-source-probe/v1"),
        ("procedure", "mtg-engineering-flow/2026-09-30.1"),
        (
            "evidence",
            "supported-Docker synthetic source-helper execution only",
        ),
        ("platform_ref", PLATFORM),
        (
            "mounted_foundation_ref",
            "a5e129ac3cbcb1f3098e7dd0923721fccc84cb16",
        ),
        ("workspace_ref", "83c1cb034dbbcfa29eb1723506735b13b4788536"),
        (
            "image",
            "sha256:1ed49b5898986723c040dab6cf98f05951e71a57c226e025d353f15b300ca581",
        ),
        ("python", "3.14.7"),
    ] {
        if document.get(key).and_then(Value::as_str) != Some(expected) {
            return Err(INVALID);
        }
    }
    if document["vector_count"] != 64
        || document["barrier_count"] != 0
        || document["barriers"] != serde_json::json!([])
    {
        return Err(INVALID);
    }
    if document["versions"]
        != serde_json::json!({"pydantic": "2.13.3", "pydantic-core": "2.46.3", "SQLAlchemy": "2.0.49", "cryptography": "50.0.0"})
        || document["sources"].as_object().map(|sources| sources.len()) != Some(7)
        || document["isolation"]["authored_workflow_import"] != false
        || document["isolation"]["database_or_workload_execution_called"] != false
    {
        return Err(INVALID);
    }
    for (path, hash) in [
        (
            "src/services/solutions/deployment_manifest.py",
            "815284d4127a646cfbe1fa713ed01bc3ea3ba7e3854f162c9b53d4f216b618bc",
        ),
        (
            "bifrost/workspace_release.py",
            "6e7d20a4ad7c5fd5b3a51b0932f7c70a69477c217c3f6496902051c61957ed92",
        ),
        (
            "bifrost/solution_delivery_review.py",
            "a48d4d7f95ddcfc73fb34c896ad5fc22565faa84bdda1878e56db5155c7c8540",
        ),
        (
            "src/services/solutions/deployment_runtime.py",
            "5282fb57793eab13861193590e3f47bcfca6901c52b38fb9b12dfc14c73cc331",
        ),
        (
            "src/services/execution/attempts.py",
            "82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa",
        ),
        (
            "src/services/work_delivery_store.py",
            "a208dbeb2c16fd50b6062b07362533278be00e1fe0c66aad152ab3c3c6bd4a6c",
        ),
        (
            "features/utilities/workflows/check_integration_readiness.py",
            "f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de",
        ),
    ] {
        let source_ref = if path.starts_with("features/") {
            "83c1cb034dbbcfa29eb1723506735b13b4788536"
        } else {
            PLATFORM
        };
        if document["sources"][path]["sha256"].as_str() != Some(hash)
            || document["sources"][path]["ref"].as_str() != Some(source_ref)
        {
            return Err(INVALID);
        }
    }
    if document["sources"]["bifrost/solution_delivery_review.py"]["compiler"]
        != serde_json::json!({"path": "bifrost/solution_delivery_review.py", "function": "compile_workflow_registrations", "line": 294, "ref": PLATFORM})
    {
        return Err(INVALID);
    }
    let vectors: Vec<Vector> =
        serde_json::from_value(document["vectors"].clone()).map_err(|_| INVALID)?;
    let common = [
        "integer-zero",
        "float-zero",
        "float-negative-zero",
        "integer-one",
        "float-one",
        "float-1e-4",
        "float-1e-5",
        "float-1e-6",
        "float-1e-7",
        "float-1e15",
        "float-1e16",
        "float-1e20",
        "negative-1e-5",
        "smallest-subnormal",
        "largest-finite",
        "roundtrip",
        "null-present",
        "null-absent",
        "nested-order",
        "unicode-and-escapes",
        "unicode-composed",
        "unicode-decomposed",
        "nan",
        "positive-infinity",
        "negative-infinity",
    ];
    let mut expected = BTreeSet::new();
    for profile in ["PythonDeploymentEvidenceV1", "PythonWorkspaceIdentityV1"] {
        for name in common {
            expected.insert((profile.to_owned(), name.to_owned()));
        }
    }
    for name in [
        "selected-A-compiled-definition",
        "selected-A-synthetic-queue-evidence",
    ] {
        expected.insert(("PythonDeploymentEvidenceV1".to_owned(), name.to_owned()));
    }
    for name in [
        "model-none-and-nested-null",
        "model-explicit-empty",
        "controls-default",
        "controls-explicit-int-zero",
        "controls-explicit-float-zero",
        "controls-negative-zero",
        "controls-1e-5",
        "selected-A-shaped-recipe",
    ] {
        expected.insert(("PythonDeploymentDocumentV1".to_owned(), name.to_owned()));
    }
    for (profile, name) in [
        ("PythonWorkspaceManifestV1", "prefixed-leaf-normalization"),
        ("PythonAttemptPolicyV1", "selected-A-shaped-policy"),
        ("PythonAttemptPolicyV1", "synthetic-ASCII-escaping"),
        ("PythonDeliveryPlaintextV1", "synthetic-envelope"),
    ] {
        expected.insert((profile.to_owned(), name.to_owned()));
    }
    for vector in &vectors {
        if !expected.remove(&(vector.profile.clone(), vector.name.clone()))
            || vector.fixture != "synthetic"
            || vector.producer.source_ref != PLATFORM
        {
            return Err(INVALID);
        }
        let (path, function, line) = match vector.profile.as_str() {
            "PythonDeploymentEvidenceV1" | "PythonDeploymentDocumentV1" => (
                "src/services/solutions/deployment_manifest.py",
                "canonical_json",
                52,
            ),
            "PythonWorkspaceIdentityV1" => ("bifrost/workspace_release.py", "canonical_digest", 33),
            "PythonWorkspaceManifestV1" => {
                ("bifrost/workspace_release.py", "workspace_manifest_id", 40)
            }
            "PythonAttemptPolicyV1" => ("src/services/execution/attempts.py", "_policy_digest", 16),
            "PythonDeliveryPlaintextV1" => {
                ("src/services/work_delivery_store.py", "_encrypted", 75)
            }
            _ => return Err(INVALID),
        };
        if vector.producer.path != path
            || vector.producer.function != function
            || vector.producer.line != line
        {
            return Err(INVALID);
        }
        let options = match vector.profile.as_str() {
            "PythonWorkspaceIdentityV1" | "PythonWorkspaceManifestV1" => Some(
                serde_json::json!({"sort_keys": true, "separators": [",", ":"], "ensure_ascii": false}),
            ),
            "PythonAttemptPolicyV1" => {
                Some(serde_json::json!({"sort_keys": true, "separators": [",", ":"]}))
            }
            "PythonDeliveryPlaintextV1" => Some(serde_json::json!({})),
            _ => None,
        };
        if vector.dump_options != options {
            return Err(INVALID);
        }
        let expected_exception = vector.profile == "PythonDeploymentEvidenceV1"
            && matches!(
                vector.name.as_str(),
                "nan" | "positive-infinity" | "negative-infinity"
            );
        if expected_exception {
            if vector.outcome != "exception"
                || vector.exception_type.as_deref() != Some("ValueError")
                || vector.expected_failure_observed != Some(true)
                || vector.utf8.is_some()
                || vector.hex.is_some()
                || vector.sha256.is_some()
                || vector.digest.is_some()
                || vector.helper_digest.is_some()
            {
                return Err(INVALID);
            }
        } else {
            let hash = vector.sha256.as_deref().ok_or(INVALID)?;
            if vector.outcome != "encoded"
                || vector.utf8.is_none()
                || vector.hex.is_none()
                || hash.len() != 64
                || !hash
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                || vector.digest.as_deref() != Some(format!("sha256:{hash}").as_str())
                || vector.exception_type.is_some()
                || vector.expected_failure_observed.is_some()
            {
                return Err(INVALID);
            }
            if matches!(
                vector.profile.as_str(),
                "PythonWorkspaceIdentityV1" | "PythonWorkspaceManifestV1" | "PythonAttemptPolicyV1"
            ) {
                if vector.helper_digest != vector.digest {
                    return Err(INVALID);
                }
            } else if vector.helper_digest.is_some() {
                return Err(INVALID);
            }
        }
    }
    if vectors.len() != 64 || !expected.is_empty() {
        return Err(INVALID);
    }
    Ok(vectors)
}
