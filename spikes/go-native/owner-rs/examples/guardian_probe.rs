//! Live hosted custody probe. No admission, Start, SDK or lifecycle finalization.
use bifrost_isolated_owner_spike::{
    archive::{ArchivePins, MAX_ARCHIVE, NAMES, sha256, verify_archive},
    guardian::{Guardian, GuardianError, LaunchSpec},
    material::MaterialPipe,
};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    os::unix::net::UnixListener,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn read(path: &Path, limit: usize) -> Result<Vec<u8>, ()> {
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(|_| ())?
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ())?;
    if bytes.len() > limit || bytes.is_empty() {
        return Err(());
    }
    Ok(bytes)
}
fn write_new(path: &Path, raw: &[u8], mode: u32) -> Result<(), ()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)
        .map_err(|_| ())?;
    file.write_all(raw)
        .and_then(|_| file.sync_all())
        .map_err(|_| ())
}
fn directory(path: &Path) -> Result<(), ()> {
    let mut builder = fs::DirBuilder::new();
    builder.mode(0o700);
    builder.create(path).map_err(|_| ())
}
fn socket_directory(root: &Path, session: &str) -> Result<PathBuf, ()> {
    let path = root.join(format!("s{}", session.replace('-', "")));
    // Linux sockaddr_un.sun_path includes its terminating NUL. Keep this
    // first-party fixture path bounded before bind; no silent path fallback.
    if path.join("ingress.sock").to_str().ok_or(())?.len() >= 108 {
        return Err(());
    }
    Ok(path)
}
fn uuid() -> Result<String, ()> {
    let mut bytes = [0; 16];
    File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .map_err(|_| ())?;
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..]
    ))
}
fn save_spec(spec: &LaunchSpec, path: &Path) -> Result<(), ()> {
    let record = json!({"session_id":spec.session_id,"nonce":spec.nonce,"image_id":spec.image_id,
        "index_sha256":spec.index_sha256,"adapter":spec.adapter,"bundle":spec.bundle,
        "material":spec.material,"journal":spec.journal,"sdk_socket_directory":spec.sdk_socket_directory,"uid":spec.uid,"gid":spec.gid});
    write_new(path, &serde_json::to_vec(&record).map_err(|_| ())?, 0o600)
}
fn load_spec(path: &Path) -> Result<LaunchSpec, ()> {
    let value: Value = serde_json::from_slice(&read(path, 65536)?).map_err(|_| ())?;
    let text = |name: &str| value[name].as_str().map(str::to_owned).ok_or(());
    Ok(LaunchSpec {
        session_id: text("session_id")?,
        nonce: text("nonce")?,
        image_id: text("image_id")?,
        index_sha256: text("index_sha256")?,
        adapter: text("adapter")?.into(),
        bundle: text("bundle")?.into(),
        material: text("material")?.into(),
        journal: text("journal")?.into(),
        sdk_socket_directory: if value["sdk_socket_directory"].is_null() {
            None
        } else {
            Some(text("sdk_socket_directory")?.into())
        },
        uid: u32::try_from(value["uid"].as_u64().ok_or(())?).map_err(|_| ())?,
        gid: u32::try_from(value["gid"].as_u64().ok_or(())?).map_err(|_| ())?,
    })
}

fn worker(path: &Path) -> Result<(), ()> {
    let spec = load_spec(path)?;
    let mut guardian = Guardian::create(spec.clone()).map_err(|_| ())?;
    guardian.attach_dormant().map_err(|_| ())?;
    let offer = guardian.receive(Duration::from_secs(5)).map_err(|_| ())?;
    let custody = guardian.observe_live(&offer).map_err(|_| ())?;
    write_new(
        &spec.journal.join("live-custody.json"),
        &serde_json::to_vec(&custody).map_err(|_| ())?,
        0o600,
    )?;
    write_new(&spec.journal.join("guardian-ready"), b"ready\n", 0o600)?;
    loop {
        thread::park();
    } // parent deliberately SIGKILLs this real guardian
}

fn cli_reaped(custody: &Value) -> Result<(), ()> {
    let pid = custody["observation"]["attached_cli_pid"]
        .as_u64()
        .ok_or(())?;
    let original = custody["observation"]["attached_cli_start_ticks"]
        .as_str()
        .ok_or(())?;
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match fs::read_to_string(format!("/proc/{pid}/stat")) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Ok(text) => {
                let current = text
                    .rsplit_once(") ")
                    .and_then(|(_, tail)| tail.split_whitespace().nth(19))
                    .ok_or(())?;
                if current != original {
                    return Ok(());
                }
            }
            Err(_) => return Err(()),
        }
        if Instant::now() >= deadline {
            return Err(());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn selection(session: &str, offer: &Value) -> Result<Value, ()> {
    Ok(json!({"protocol":"bifrost.runtime/v1","type":"Select",
        "session_id":session,"message_id":uuid()?,"sequence":1,
        "correlation_id":offer["message_id"],"body":{"protocol":"bifrost.runtime/v1",
        "capability":"execution_profile/v1","artifact_class":"native-executable/v1"}}))
}

fn run_case(spec: LaunchSpec, mode: &str) -> Result<Value, ()> {
    let operation = (|| {
        match mode {
            "normal" | "poisoned-channel" | "sdk-relay-dormant" | "sdk-relay-lost-ingress" => {
                let mut guardian = Guardian::create(spec.clone()).map_err(|_| ())?;
                guardian.attach_dormant().map_err(|_| ())?;
                let offer = guardian.receive(Duration::from_secs(5)).map_err(|_| ())?;
                let custody = guardian.observe_live(&offer).map_err(|_| ())?;
                write_new(
                    &spec.journal.join("live-custody.json"),
                    &serde_json::to_vec(&custody).map_err(|_| ())?,
                    0o600,
                )?;
                guardian.verify_live().map_err(|_| ())?;
                // Exercise real outbound common framing; no Prepare/Start or
                // material, so the adapter remains inert waiting for Prepare.
                guardian
                    .send(&selection(&spec.session_id, offer.frame())?)
                    .map_err(|_| ())?;
                guardian.verify_live().map_err(|_| ())?;
                if mode == "poisoned-channel" {
                    // A rejected frame poisons physical outbound custody. No
                    // retained digest may authorize SDK admission afterward.
                    if guardian.send(&json!({})) != Err(GuardianError::Uncertain)
                        || guardian.verify_live() != Err(GuardianError::Rejected)
                    {
                        return Err(());
                    }
                }
                if guardian.attach_dormant() != Err(GuardianError::Rejected) {
                    return Err(());
                }
                let drain = if mode == "sdk-relay-lost-ingress" {
                    fs::remove_file(
                        spec.sdk_socket_directory
                            .as_ref()
                            .ok_or(())?
                            .join("ingress.sock"),
                    )
                    .map_err(|_| ())?;
                    drop(guardian);
                    let mut recovered =
                        Guardian::recover_for_drain(spec.clone()).map_err(|_| ())?;
                    if recovered.attach_dormant() != Err(GuardianError::Rejected)
                        || recovered.verify_live() != Err(GuardianError::Rejected)
                    {
                        return Err(());
                    }
                    recovered.drain().map_err(|_| ())?
                } else {
                    guardian.drain().map_err(|_| ())?
                };
                Ok(json!({"case":mode,"custody":custody,"drain":drain,
                    "fresh_custody_checked":true,"poisoned_channel_denied":mode=="poisoned-channel",
                    "sdk_relay_present":mode.starts_with("sdk-relay-"),
                    "lost_sdk_ingress_stop_only":mode=="sdk-relay-lost-ingress"}))
            }
            "lost-create-reply" => {
                let guardian = Guardian::create(spec.clone()).map_err(|_| ())?;
                drop(guardian); // discard the returned handle; retain original intent
                let mut recovered = Guardian::recover_for_drain(spec.clone()).map_err(|_| ())?;
                if recovered.attach_dormant() != Err(GuardianError::Rejected) {
                    return Err(());
                }
                if recovered.verify_live() != Err(GuardianError::Rejected) {
                    return Err(());
                }
                let drain = recovered.drain().map_err(|_| ())?;
                Ok(json!({"case":mode,"fresh_start_denied":true,"drain":drain}))
            }
            "guardian-crash-after-offer" => {
                let spec_path = spec.journal.join("worker-spec.json");
                save_spec(&spec, &spec_path)?;
                let mut child = Command::new(std::env::current_exe().map_err(|_| ())?)
                    .args(["--worker"])
                    .arg(&spec_path)
                    .env_clear()
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .map_err(|_| ())?;
                let deadline = Instant::now() + Duration::from_secs(5);
                while !spec.journal.join("guardian-ready").exists() {
                    if child.try_wait().map_err(|_| ())?.is_some() || Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(());
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                child.kill().map_err(|_| ())?;
                let status = child.wait().map_err(|_| ())?;
                if status.success() {
                    return Err(());
                }
                let custody: Value =
                    serde_json::from_slice(&read(&spec.journal.join("live-custody.json"), 65536)?)
                        .map_err(|_| ())?;
                let mut recovered = Guardian::recover_for_drain(spec.clone()).map_err(|_| ())?;
                if recovered.attach_dormant() != Err(GuardianError::Rejected) {
                    return Err(());
                }
                if recovered.verify_live() != Err(GuardianError::Rejected) {
                    return Err(());
                }
                let drain = recovered.drain().map_err(|_| ())?;
                cli_reaped(&custody)?;
                Ok(
                    json!({"case":mode,"guardian_sigkill":true,"guardian_waited":true,
                    "fresh_start_denied":true,"original_cli_reaped_observed":true,"custody":custody,"drain":drain}),
                )
            }
            _ => Err(()),
        }
    })();
    if operation.is_err() {
        // Stop-only salvage of the original authenticated intent; never retry
        // creation/start. Preserve the failure and every journal for inspection.
        if let Ok(guardian) = Guardian::recover_for_drain(spec) {
            let _ = guardian.drain();
        }
    }
    operation
}

fn run(arguments: &[String]) -> Result<Value, ()> {
    if arguments.len() != 6 {
        return Err(());
    }
    let raw = read(Path::new(&arguments[1]), MAX_ARCHIVE)?;
    let descriptor: Value =
        serde_json::from_slice(&read(Path::new(&arguments[2]), 65536)?).map_err(|_| ())?;
    let entries = descriptor["entries"]
        .as_array()
        .filter(|e| e.len() == 6)
        .ok_or(())?;
    let mut pins = ArchivePins {
        archive_sha256: descriptor["artifact"]["artifact_id"]
            .as_str()
            .and_then(|s| s.strip_prefix("sha256:"))
            .ok_or(())?
            .into(),
        entries_sha256: std::array::from_fn(|_| String::new()),
    };
    for (i, name) in NAMES.iter().enumerate() {
        if entries[i]["path"] != *name {
            return Err(());
        }
        pins.entries_sha256[i] = entries[i]["sha256"].as_str().ok_or(())?.into();
    }
    let contents = verify_archive(&raw, &pins).map_err(|_| ())?;
    let build: Value = serde_json::from_slice(contents[1]).map_err(|_| ())?;
    if build["guardian_carrier"]["image_id"] != arguments[4]
        || sha256(contents[5]) != "160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34"
    {
        return Err(());
    }
    let vectors: Vec<Value> =
        serde_json::from_slice(&read(Path::new(&arguments[3]), 16 << 20)?).map_err(|_| ())?;
    let original = vectors
        .into_iter()
        .find(|v| v["name"] == "valid-Prepare")
        .ok_or(())?;
    let root = PathBuf::from(&arguments[5]);
    let metadata = fs::symlink_metadata(&root).map_err(|_| ())?;
    if !metadata.is_dir()
        || metadata.uid() == 0
        || metadata.mode() & 0o777 != 0o700
        || root.canonicalize().map_err(|_| ())? != root
    {
        return Err(());
    }
    let mut cases = Vec::new();
    for mode in [
        "normal",
        "poisoned-channel",
        "sdk-relay-dormant",
        "sdk-relay-lost-ingress",
        "lost-create-reply",
        "guardian-crash-after-offer",
    ] {
        let session = uuid()?;
        let base = root.join(&session);
        directory(&base)?;
        let bundle = base.join("bundle");
        let journal = base.join("journal");
        let material = base.join("material");
        directory(&bundle)?;
        directory(&journal)?;
        directory(&material)?;
        let sdk_directory = socket_directory(&root, &session)?;
        let sdk_listener = if mode.starts_with("sdk-relay-") {
            directory(&sdk_directory)?;
            let listener =
                UnixListener::bind(sdk_directory.join("ingress.sock")).map_err(|_| ())?;
            fs::set_permissions(
                sdk_directory.join("ingress.sock"),
                fs::Permissions::from_mode(0o600),
            )
            .map_err(|_| ())?;
            Some(listener)
        } else {
            None
        };
        for (name, raw) in NAMES.iter().zip(contents) {
            write_new(
                &bundle.join(name),
                raw,
                if *name == "adapter" || *name == "workflow" {
                    0o500
                } else {
                    0o400
                },
            )?;
        }
        let mut binding = original["frame"]["body"]["binding"].clone();
        binding["session_id"] = json!(session);
        binding["runtime_incarnation_id"] = json!(uuid()?);
        binding["artifact_id"] = descriptor["artifact"]["artifact_id"].clone();
        let index = serde_json::to_vec(&json!({"version":"isolated-native-session-bundle/v1","binding":binding,
            "artifact":descriptor["artifact"],"input_schema_sha256":descriptor["input_schema_sha256"],
            "output_schema_sha256":descriptor["output_schema_sha256"]})).map_err(|_| ())?;
        write_new(&bundle.join("session-bundle.json"), &index, 0o400)?;
        fs::set_permissions(&bundle, fs::Permissions::from_mode(0o500)).map_err(|_| ())?;
        let mut pipe = MaterialPipe::create(&material, &session, metadata.uid()).map_err(|_| ())?;
        let spec = LaunchSpec {
            session_id: session.clone(),
            nonce: uuid()?,
            image_id: arguments[4].clone(),
            index_sha256: sha256(&index),
            adapter: bundle.join("adapter"),
            bundle: bundle.clone(),
            material: pipe.directory().to_owned(),
            journal,
            sdk_socket_directory: sdk_listener.as_ref().map(|_| sdk_directory.clone()),
            uid: metadata.uid(),
            gid: metadata.gid(),
        };
        let started = Instant::now();
        let mut result = run_case(spec, mode)?;
        if result["drain"]["namespace_drained"] != true
            || result["drain"]["container_removed"] != true
        {
            return Err(());
        }
        pipe.close();
        pipe.retire().map_err(|_| ())?;
        fs::remove_dir(&material).map_err(|_| ())?;
        fs::set_permissions(&bundle, fs::Permissions::from_mode(0o700)).map_err(|_| ())?;
        for name in NAMES.into_iter().chain(["session-bundle.json"]) {
            fs::remove_file(bundle.join(name)).map_err(|_| ())?;
        }
        fs::remove_dir(&bundle).map_err(|_| ())?;
        if sdk_listener.is_some() {
            drop(sdk_listener);
            if mode == "sdk-relay-lost-ingress" {
                let absent = fs::symlink_metadata(sdk_directory.join("ingress.sock"));
                if !matches!(absent, Err(e) if e.kind()==std::io::ErrorKind::NotFound) {
                    return Err(());
                }
            } else {
                fs::remove_file(sdk_directory.join("ingress.sock")).map_err(|_| ())?;
            }
            fs::remove_dir(&sdk_directory).map_err(|_| ())?;
        }
        result["private_paths_removed"] = json!(true);
        result["elapsed_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
        cases.push(result);
    }
    Ok(
        json!({"runtime_acceptance":false,"tenant_start":false,"sdk_issuance":false,
        "durable_lifecycle":false,"network":"none","artifact_id":descriptor["artifact"]["artifact_id"],
        "unchanged_workflow_sha256":sha256(contents[5]),"cases":cases}),
    )
}
fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 3 && arguments[1] == "--worker" {
        if worker(Path::new(&arguments[2])).is_err() {
            std::process::exit(1);
        }
        return;
    }
    match run(&arguments) {
        Ok(evidence) => println!("{evidence}"),
        Err(_) => {
            eprintln!(
                "live guardian proof failed; inspect retained journals and cleanup inventory"
            );
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bifrost_execution_wire_spike::Codec;
    #[test]
    fn sdk_socket_fixture_obeys_linux_path_bound() {
        let root = Path::new("/home/runner/work/_temp/go-spike-evidence/live-guardian");
        let session = "00000000-0000-0000-0000-000000000005";
        let directory =
            socket_directory(root, session).unwrap_or_else(|_| panic!("trusted short path"));
        assert!(
            directory
                .join("ingress.sock")
                .to_str()
                .is_some_and(|p| p.len() < 108)
        );
        assert!(socket_directory(Path::new(&format!("/{}", "a".repeat(108))), session).is_err());
    }
    #[test]
    fn selection_uses_schema_capability_name_not_profile_uri() {
        let offer = json!({"message_id":"00000000-0000-0000-0000-000000000064"});
        let mut frame = selection("00000000-0000-0000-0000-000000000005", &offer)
            .unwrap_or_else(|_| panic!("trusted selection"));
        let codec = Codec::new().unwrap_or_else(|_| panic!("trusted schema"));
        assert!(
            codec
                .decode(&serde_json::to_vec(&frame).unwrap_or_default())
                .is_ok()
        );
        frame["body"]["capability"] = json!("bifrost.runtime/v1/execution_profile/v1");
        assert!(
            codec
                .decode(&serde_json::to_vec(&frame).unwrap_or_default())
                .is_err()
        );
    }
}
