//! Runnable isolated original owner. Parent source/build acceptance and caller
//! authentication are prerequisites. No production registration or acceptance.
use bifrost_isolated_owner_spike::{
    IntegrationGetRequest, ProvisionRequest, ReleaseRequest, SessionFence,
    archive::{ArchivePins, MAX_ARCHIVE, NAMES, sha256, verify_archive},
    guardian::{Guardian, LaunchSpec},
    issuer::IssuerChannel,
    live_start::{BeginRequest, admit_and_start},
    material::MaterialPipe,
    observe_closed_cancel_recovery, observe_closed_result_recovery,
    peer::OriginalPeer,
    reconcile_committed_result, reconcile_committed_settlement, record_provision_candidate,
    sdk_gate::SDKGate,
    settle_retired_session,
};
use serde_json::{Value, json};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, BufRead, Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    str::FromStr,
    time::{Duration, Instant},
};

fn read(path: &Path, limit: usize) -> Result<Vec<u8>, ()> {
    let mut raw = Vec::new();
    File::open(path)
        .map_err(|_| ())?
        .take((limit + 1) as u64)
        .read_to_end(&mut raw)
        .map_err(|_| ())?;
    if raw.is_empty() || raw.len() > limit {
        return Err(());
    }
    Ok(raw)
}
fn parent() -> Result<Value, ()> {
    let mut raw = Vec::new();
    io::stdin()
        .lock()
        .take(65537)
        .read_until(b'\n', &mut raw)
        .map_err(|_| ())?;
    if raw.len() > 65536 || !raw.ends_with(b"\n") {
        return Err(());
    }
    serde_json::from_slice(&raw).map_err(|_| ())
}
fn emit(value: &Value) -> Result<(), ()> {
    println!("{value}");
    io::stdout().flush().map_err(|_| ())
}
fn text(value: &Value, key: &str) -> Result<String, ()> {
    Ok(value[key].as_str().ok_or(())?.into())
}
fn directory(path: &Path) -> Result<(), ()> {
    let mut builder = fs::DirBuilder::new();
    builder.mode(0o700);
    builder.create(path).map_err(|_| ())
}
fn write_new(path: &Path, raw: &[u8], mode: u32) -> Result<(), ()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .mode(mode)
        .open(path)
        .map_err(|_| ())?;
    file.write_all(raw)
        .and_then(|_| file.sync_all())
        .map_err(|_| ())
}

async fn run(config: Value) -> Result<Value, ()> {
    if std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1") {
        return Err(());
    }
    let started = Instant::now();
    let root = PathBuf::from(text(&config, "root")?);
    let metadata = fs::symlink_metadata(&root).map_err(|_| ())?;
    if !metadata.is_dir()
        || metadata.uid() == 0
        || metadata.mode() & 0o777 != 0o700
        || !root.is_absolute()
        || root.canonicalize().map_err(|_| ())? != root
        || fs::read_dir(&root).map_err(|_| ())?.next().is_some()
    {
        return Err(());
    }
    let descriptor: Value =
        serde_json::from_slice(&read(Path::new(&text(&config, "descriptor")?), 65536)?)
            .map_err(|_| ())?;
    let raw = read(Path::new(&text(&config, "archive")?), MAX_ARCHIVE)?;
    let entries = descriptor["entries"]
        .as_array()
        .filter(|v| v.len() == 6)
        .ok_or(())?;
    let mut pins = ArchivePins {
        archive_sha256: descriptor["artifact"]["artifact_id"]
            .as_str()
            .and_then(|v| v.strip_prefix("sha256:"))
            .ok_or(())?
            .into(),
        entries_sha256: std::array::from_fn(|_| String::new()),
    };
    for (i, name) in NAMES.iter().enumerate() {
        if entries[i]["path"] != *name {
            return Err(());
        }
        pins.entries_sha256[i] = text(&entries[i], "sha256")?;
    }
    let contents = verify_archive(&raw, &pins).map_err(|_| ())?;
    let build: Value = serde_json::from_slice(contents[1]).map_err(|_| ())?;
    if build["guardian_carrier"]["image_id"] != config["image_id"]
        || sha256(contents[5]) != "5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd"
    {
        return Err(());
    }
    let prepare = config["prepare"].clone();
    let binding = &prepare["body"]["binding"];
    if prepare["body"]["artifact"] != descriptor["artifact"] {
        return Err(());
    }
    let session = text(binding, "session_id")?;
    let bundle = root.join("bundle");
    let material = root.join("material");
    let journal = root.join("journal");
    let sdk = root.join("sdk");
    let gate_dir = root.join("gate");
    for path in [&bundle, &material, &journal, &sdk, &gate_dir] {
        directory(path)?;
    }
    for (name, bytes) in NAMES.iter().zip(contents) {
        write_new(
            &bundle.join(name),
            bytes,
            if *name == "adapter" || *name == "workflow" {
                0o500
            } else {
                0o400
            },
        )?;
    }
    let index = serde_json::to_vec(&json!({"version":"isolated-native-session-bundle/v1",
        "binding":binding,"artifact":descriptor["artifact"],
        "input_schema_sha256":descriptor["input_schema_sha256"],
        "output_schema_sha256":descriptor["output_schema_sha256"]}))
    .map_err(|_| ())?;
    write_new(&bundle.join("session-bundle.json"), &index, 0o400)?;
    fs::set_permissions(&bundle, fs::Permissions::from_mode(0o500)).map_err(|_| ())?;
    let mut pipe = MaterialPipe::create(&material, &session, metadata.uid()).map_err(|_| ())?;
    let mut gate = SDKGate::create(&gate_dir, metadata.uid()).map_err(|_| ())?;
    let spec = LaunchSpec {
        session_id: session.clone(),
        nonce: text(&config, "nonce")?,
        image_id: text(&config, "image_id")?,
        index_sha256: sha256(&index),
        adapter: bundle.join("adapter"),
        bundle: bundle.clone(),
        material: pipe.directory().to_owned(),
        journal: journal.clone(),
        sdk_socket_directory: Some(sdk.clone()),
        uid: metadata.uid(),
        gid: metadata.gid(),
    };
    // No kernel/process effect until the authenticated parent starts restricted
    // TLS ingress at this exact empty directory. Public values only on stdout.
    emit(
        &json!({"event":"ingress_required","sdk_directory":sdk,"gate_path":gate.path(),
        "owner_pid":std::process::id(),"owner_uid":metadata.uid()}),
    )?;
    let ready = parent()?;
    if ready["event"] != "ingress_ready" {
        return Err(());
    }
    let url = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL").map_err(|_| ())?;
    let options = PgConnectOptions::from_str(&url).map_err(|_| ())?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options.statement_cache_capacity(0).extra_float_digits(None))
        .await
        .map_err(|_| ())?;
    let mut guardian = Guardian::create(spec.clone()).map_err(|_| ())?;
    guardian.retain_retirement_custody().map_err(|_| ())?;
    let mut admitted_fence = None;
    let mut live_counts = None;
    let mut uncertain_result_commit = false;
    let operation = async {
        guardian.attach_dormant().map_err(|_| ())?;
        let offer = guardian.receive(Duration::from_secs(5)).map_err(|_| ())?;
        let fence = SessionFence { execution_id:text(binding,"execution_id")?, owner_incarnation_id:text(&config,"owner_id")?,
            attempt_id:text(binding,"attempt_id")?,claim_token:text(&config,"claim_token")?,worker_incarnation_id:text(&config,"worker_id")?,
            session_id:session.clone(),supervisor_incarnation_id:text(binding,"supervisor_incarnation_id")?,
            runtime_incarnation_id:text(binding,"runtime_incarnation_id")?,binding_sha256:String::new(),channel_custody_sha256:String::new() };
        let mut live = admit_and_start(&pool,&mut guardian,&offer,BeginRequest { fence,
            workflow_id:text(&config,"workflow_id")?,caller_id:text(&config,"caller_id")?,prepare:prepare.clone(),
            select_message_id:text(&config,"select_message_id")?,start_id:text(&config,"start_id")?,start_message_id:text(&config,"start_message_id")? }).await.map_err(|_| ())?;
        admitted_fence = Some(live.fence().clone());
        emit(&json!({"event":"start_committed","session_id":session,"start":live.start(),
            "binding_sha256":live.fence().binding_sha256,"channel_custody_sha256":live.fence().channel_custody_sha256}))?;
        // The parent reads actual committed Start/caller/source facts to derive
        // validated finite preimages, and starts its original one-use issuer.
        let provision = parent()?;
        if provision["event"] != "provision_ready" { return Err(()); }
        let request = ProvisionRequest { snapshot:provision["snapshot"].clone(),grant_digest:text(&provision,"grant_digest")?,
            integration_name:"Fixture".into(),provision_id:text(&provision,"provision_id")?,delivery_id:text(&provision,"delivery_id")?,frontier_sha256:text(&provision,"frontier_sha256")? };
        record_provision_candidate(&pool,live.fence(),&request).await.map_err(|_| ())?;
        let reference = IntegrationGetRequest { grant_id:text(&request.snapshot,"id")?,grant_digest:request.grant_digest.clone(),
            integration_name:request.integration_name.clone(),organization_id:text(&request.snapshot,"effective_organization_id")?,solution_id:text(&request.snapshot,"solution_install_id")? };
        let issuer = &provision["issuer"];
        let uid = u32::try_from(issuer["uid"].as_u64().ok_or(())?).map_err(|_| ())?;
        let peer = OriginalPeer::pin(u32::try_from(issuer["pid"].as_u64().ok_or(())?).map_err(|_| ())?,uid,issuer["ticks"].as_str().ok_or(())?).map_err(|_| ())?;
        let mut channel = IssuerChannel::pin(Path::new(&text(issuer,"path")?),uid,peer,text(issuer,"ca")?).map_err(|_| ())?;
        let issued = live.issue_material(&pool,&mut guardian,&mut channel,&reference).await.map_err(|_| ())?;
        let release = ReleaseRequest { release_id:text(&provision,"release_id")?,provision_id:request.provision_id,grant_id:reference.grant_id,
            delivery_id:request.delivery_id,operations_sha256:text(&request.snapshot,"operations_digest")?,frontier_sha256:request.frontier_sha256 };
        if config["lost_release_commit_ack"] == true {
            emit(&json!({"event":"release_commit_required","session_id":session}))?;
            let ready = parent()?;
            if ready["event"]!="release_ready" || ready["session_id"]!=session { return Err(()); }
        }
        let delivered = live.release_and_deliver(&pool,&mut guardian,&mut pipe,issued,&release,&text(&provision,"provision_message_id")?).await;
        if config["lost_release_commit_ack"] == true {
            if !matches!(delivered, Err(bifrost_isolated_owner_spike::live_start::BeginError::Owner(bifrost_isolated_owner_spike::ObserveError::UncertainCommit))) {
                return Err(());
            }
            if bifrost_isolated_owner_spike::observe_release_candidate(&pool,live.fence(),&release).await.map_err(|_| ())?
                != bifrost_isolated_owner_spike::ReleaseCommitObservation::AlreadyRetained { return Err(()); }
            emit(&json!({"event":"release_commit_unknown","session_id":session,
                "release_reconciliation":"already_retained","delivery_observed":false}))?;
            let abort = parent()?;
            if abort["event"]!="abort_release_ready" || abort["session_id"]!=session { return Err(()); }
            let mut closed = live.abandon_failed_release(&pool,&mut guardian).await.map_err(|_| ())?;
            closed["release_commit_observation"] = json!("lost_ack");
            closed["release_reconciliation"] = json!("already_retained");
            closed["reconciled_without_replay"] = json!(true);
            return Ok::<_,()>(closed);
        }
        delivered.map_err(|_| ())?;
        if config["cancel_waiting_sdk"] == true {
            live.wait_for_first_sdk_admission(&pool,&mut guardian,&mut gate).await.map_err(|_| ())?;
            emit(&json!({"event":"sdk_admitted","session_id":session}))?;
            let requested = parent()?;
            if requested["event"] != "cancel_ready" || requested["session_id"] != session { return Err(()); }
            return live.cancel_admitted_sdk(&pool,&mut guardian,&text(&config,"cancel_id")?,&text(&config,"cancel_message_id")?).await.map_err(|_| ());
        }
        let served = live.serve_until_result(&pool,&mut guardian,&mut gate,&text(&config,"decision_id")?,&text(&config,"receipt_message_id")?).await;
        live_counts = Some(live.observed_counts());
        uncertain_result_commit = matches!(&served, Err(bifrost_isolated_owner_spike::live_start::BeginError::Owner(bifrost_isolated_owner_spike::ObserveError::UncertainCommit)));
        let completed = served.map_err(|_| ())?;
        Ok::<_,()>(json!({"receipt":completed.decision.receipt_body(),"sdk_admissions":completed.sdk_admissions,
            "sdk_denials":completed.sdk_denials,"heartbeats":completed.heartbeats,"log_batches":completed.log_batches}))
    }.await;
    // Every post-launch outcome drains this original container/channel. No
    // recovery object may enter admission, issuance, delivery or Result service.
    let lost_ack = config["lost_result_commit_ack"] == true && uncertain_result_commit;
    if config["crash_after_cancel"] == true {
        if config["cancel_waiting_sdk"] != true || operation.is_err() {
            return Err(());
        }
        write_new(
            &journal.join("cancel-uncertainty.json"),
            &serde_json::to_vec(&json!({"version":"isolated-original-cancel-uncertainty/v1",
                "session_id":session,"cancel_commit_observed":true,
                "counts":operation.as_ref().map_err(|_| ())?}))
            .map_err(|_| ())?,
            0o600,
        )?;
        File::open(&journal)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| ())?;
        emit(
            &json!({"event":"closed_cancel_recovery_required","session_id":session,
            "owner_pid":std::process::id(),"cancel_commit_observed":true}),
        )?;
        loop {
            std::thread::park();
        }
    }
    if config["crash_after_result_commit"] == true {
        if !lost_ack {
            return Err(());
        }
        write_new(
            &journal.join("result-uncertainty.json"),
            &serde_json::to_vec(&json!({"version":"isolated-original-result-uncertainty/v1",
                "session_id":session,"decision_id":config["decision_id"],
                "result_commit_observation":"lost_ack","result_receipt_sent":false,
                "counts":live_counts.as_ref().ok_or(())?}))
            .map_err(|_| ())?,
            0o600,
        )?;
        File::open(&journal)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| ())?;
        emit(
            &json!({"event":"closed_result_recovery_required","session_id":session,
            "owner_pid":std::process::id(),"result_commit_observation":"lost_ack",
            "result_receipt_sent":false}),
        )?;
        // Trusted parent deliberately SIGKILLs and waits for this original
        // actor. No command is accepted and no cleanup/effect is replayed here.
        loop {
            std::thread::park();
        }
    }
    let (drained, original_drain) = if operation.is_ok() || lost_ack {
        let fence = admitted_fence.as_ref().ok_or(())?;
        let observation = guardian.drain_session(fence).map_err(|_| ())?;
        if !observation.matches(fence) {
            return Err(());
        }
        (observation.observation().clone(), Some(observation))
    } else {
        (guardian.drain().map_err(|_| ())?, None)
    };
    let original_session_drained = original_drain.is_some();
    let mut reconciled_receipt = None;
    let settlement = if let Some(original) = original_drain {
        let retired = original
            .retire_resources(pipe, gate, &pins)
            .map_err(|_| ())?;
        // Trusted fixture observation seam: source accounting must still retain
        // this session after Result/physical retirement but before durable fact.
        emit(&json!({"event":"resources_retired","session_id":session}))?;
        let ready = parent()?;
        if ready["event"] != "settlement_ready" || ready["session_id"] != session {
            return Err(());
        }
        if lost_ack {
            reconciled_receipt = Some(
                reconcile_committed_result(
                    &pool,
                    admitted_fence.as_ref().ok_or(())?,
                    &text(&config, "decision_id")?,
                )
                .await
                .map_err(|_| ())?,
            );
        }
        let expected = sha256(&serde_json::to_vec(retired.observation()).map_err(|_| ())?);
        match settle_retired_session(&pool, retired).await {
            Ok(fact) => {
                if config["lost_settlement_commit_ack"] == true {
                    return Err(());
                }
                fact
            }
            Err(bifrost_isolated_owner_spike::ObserveError::UncertainCommit)
                if config["lost_settlement_commit_ack"] == true =>
            {
                let fence = admitted_fence.as_ref().ok_or(())?;
                let wrong = if expected.starts_with('0') {
                    "1".repeat(64)
                } else {
                    "0".repeat(64)
                };
                if reconcile_committed_settlement(&pool, fence, &wrong)
                    .await
                    .is_ok()
                {
                    return Err(());
                }
                let mut wrong_fence = fence.clone();
                wrong_fence.owner_incarnation_id = "10000000-0000-4000-8000-000000000099".into();
                if reconcile_committed_settlement(&pool, &wrong_fence, &expected)
                    .await
                    .is_ok()
                {
                    return Err(());
                }
                let mut fact = reconcile_committed_settlement(&pool, fence, &expected)
                    .await
                    .map_err(|_| ())?;
                fact["commit_observation"] = json!("lost_ack");
                fact["wrong_digest_denied"] = json!(true);
                fact["wrong_owner_denied"] = json!(true);
                fact
            }
            Err(_) => return Err(()),
        }
    } else {
        // Failed protocol/admission operation still physically cleans up, but
        // cannot turn cleanup JSON into the opaque finalization prerequisite.
        pipe.retire().map_err(|_| ())?;
        gate.retire().map_err(|_| ())?;
        fs::remove_dir(&gate_dir).map_err(|_| ())?;
        fs::remove_dir(&material).map_err(|_| ())?;
        fs::set_permissions(&bundle, fs::Permissions::from_mode(0o700)).map_err(|_| ())?;
        for (i, name) in NAMES.iter().enumerate() {
            if sha256(&read(&bundle.join(name), MAX_ARCHIVE)?) != pins.entries_sha256[i] {
                return Err(());
            }
            fs::remove_file(bundle.join(name)).map_err(|_| ())?;
        }
        if sha256(&read(&bundle.join("session-bundle.json"), 65536)?) != sha256(&index) {
            return Err(());
        }
        fs::remove_file(bundle.join("session-bundle.json")).map_err(|_| ())?;
        fs::remove_dir(&bundle).map_err(|_| ())?;
        Value::Null
    };
    pool.close().await;
    let mut result = if lost_ack {
        let mut counts = live_counts.ok_or(())?;
        counts["receipt"] = reconciled_receipt.ok_or(())?;
        counts["result_commit_observation"] = json!("lost_ack");
        counts["result_receipt_sent"] = json!(false);
        counts["reconciled_without_replay"] = json!(true);
        counts
    } else {
        operation?
    };
    if config["cancel_waiting_sdk"] == true || config["lost_release_commit_ack"] == true {
        result["durable_cancelled_finalization"] =
            settlement["durable_cancelled_finalization"].clone();
        result["source_consumer_settlement"] = settlement["durable_source_settlement"].clone();
    }
    Ok(
        json!({"event":"slice_result","component":result,"physical_drain":drained,
        "source_staging_removed":true,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
        "original_session_drained":original_session_drained,
        "resource_settlement":settlement,
        "runtime_acceptance":false,"production_dispatch":false}),
    )
}
fn recovery_spec(config: &Value) -> Result<LaunchSpec, ()> {
    let root = PathBuf::from(text(config, "root")?);
    let metadata = fs::symlink_metadata(&root).map_err(|_| ())?;
    if !metadata.is_dir()
        || metadata.uid() == 0
        || metadata.mode() & 0o777 != 0o700
        || root.canonicalize().map_err(|_| ())? != root
    {
        return Err(());
    }
    let descriptor: Value =
        serde_json::from_slice(&read(Path::new(&text(config, "descriptor")?), 65536)?)
            .map_err(|_| ())?;
    let binding = &config["prepare"]["body"]["binding"];
    let index = serde_json::to_vec(&json!({"version":"isolated-native-session-bundle/v1",
        "binding":binding,"artifact":descriptor["artifact"],
        "input_schema_sha256":descriptor["input_schema_sha256"],
        "output_schema_sha256":descriptor["output_schema_sha256"]}))
    .map_err(|_| ())?;
    let bundle = root.join("bundle");
    let spec = LaunchSpec {
        session_id: text(binding, "session_id")?,
        nonce: text(config, "nonce")?,
        image_id: text(config, "image_id")?,
        index_sha256: sha256(&index),
        adapter: bundle.join("adapter"),
        bundle,
        material: root.join("material").join(text(binding, "session_id")?),
        journal: root.join("journal"),
        sdk_socket_directory: Some(root.join("sdk")),
        uid: metadata.uid(),
        gid: metadata.gid(),
    };
    Ok(spec)
}

fn recover(config: &Value) -> Result<Value, ()> {
    let guardian = Guardian::recover_for_drain(recovery_spec(config)?).map_err(|_| ())?;
    let drained = guardian.drain().map_err(|_| ())?;
    Ok(
        json!({"event":"stop_only_recovery","physical_drain":drained,"runtime_acceptance":false,
        "source_consumer_settlement":false,"lifecycle_finalization":false}),
    )
}

fn read_private(path: &Path, uid: u32) -> Result<Value, ()> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| ())?;
    let metadata = file.metadata().map_err(|_| ())?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.mode() & 0o777 != 0o600
        || metadata.nlink() != 1
    {
        return Err(());
    }
    let mut raw = Vec::new();
    file.take(65537).read_to_end(&mut raw).map_err(|_| ())?;
    if raw.is_empty() || raw.len() > 65536 {
        return Err(());
    }
    serde_json::from_slice(&raw).map_err(|_| ())
}

async fn recover_closed_session(config: &Value, cancelled: bool) -> Result<Value, ()> {
    if std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1") {
        return Err(());
    }
    let started = Instant::now();
    let spec = recovery_spec(config)?;
    let descriptor: Value =
        serde_json::from_slice(&read(Path::new(&text(config, "descriptor")?), 65536)?)
            .map_err(|_| ())?;
    let entries = descriptor["entries"]
        .as_array()
        .filter(|v| v.len() == 6)
        .ok_or(())?;
    let mut pins = ArchivePins {
        archive_sha256: descriptor["artifact"]["artifact_id"]
            .as_str()
            .and_then(|v| v.strip_prefix("sha256:"))
            .ok_or(())?
            .into(),
        entries_sha256: std::array::from_fn(|_| String::new()),
    };
    for (i, name) in NAMES.iter().enumerate() {
        if entries[i]["path"] != *name {
            return Err(());
        }
        pins.entries_sha256[i] = text(&entries[i], "sha256")?;
    }
    let archive = read(Path::new(&text(config, "archive")?), MAX_ARCHIVE)?;
    let contents = verify_archive(&archive, &pins).map_err(|_| ())?;
    let build: Value = serde_json::from_slice(contents[1]).map_err(|_| ())?;
    if build["guardian_carrier"]["image_id"] != config["image_id"]
        || sha256(contents[5]) != "5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd"
    {
        return Err(());
    }
    let custody = read_private(&spec.journal.join("original-custody.json"), spec.uid)?;
    let uncertainty = read_private(
        &spec.journal.join(if cancelled {
            "cancel-uncertainty.json"
        } else {
            "result-uncertainty.json"
        }),
        spec.uid,
    )?;
    let valid_uncertainty = if cancelled {
        uncertainty["version"] == "isolated-original-cancel-uncertainty/v1"
            && uncertainty["cancel_commit_observed"] == true
            && config["crash_after_cancel"] == true
    } else {
        uncertainty["version"] == "isolated-original-result-uncertainty/v1"
            && uncertainty["decision_id"] == config["decision_id"]
            && uncertainty["result_commit_observation"] == "lost_ack"
            && uncertainty["result_receipt_sent"] == false
    };
    if uncertainty["session_id"] != spec.session_id || !valid_uncertainty {
        return Err(());
    }
    let binding = &config["prepare"]["body"]["binding"];
    let fence = SessionFence {
        execution_id: text(binding, "execution_id")?,
        owner_incarnation_id: text(config, "owner_id")?,
        attempt_id: text(binding, "attempt_id")?,
        claim_token: text(config, "claim_token")?,
        worker_incarnation_id: text(config, "worker_id")?,
        session_id: spec.session_id.clone(),
        supervisor_incarnation_id: text(binding, "supervisor_incarnation_id")?,
        runtime_incarnation_id: text(binding, "runtime_incarnation_id")?,
        binding_sha256: sha256(&serde_json::to_vec(binding).map_err(|_| ())?),
        channel_custody_sha256: text(&custody, "observation_sha256")?,
    };
    let url = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL").map_err(|_| ())?;
    let options = PgConnectOptions::from_str(&url).map_err(|_| ())?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options.statement_cache_capacity(0).extra_float_digits(None))
        .await
        .map_err(|_| ())?;
    let closed = if cancelled {
        observe_closed_cancel_recovery(&pool, &fence).await
    } else {
        observe_closed_result_recovery(&pool, &fence, &text(config, "decision_id")?).await
    }
    .map_err(|_| ())?;
    let receipt = closed.receipt().cloned();
    let guardian = Guardian::recover_for_drain(spec).map_err(|_| ())?;
    let retired = guardian
        .retire_closed_session(closed, &pins)
        .map_err(|_| ())?;
    let physical = retired.observation()["physical_drain"].clone();
    emit(&json!({"event":"resources_retired","session_id":fence.session_id}))?;
    let ready = parent()?;
    if ready["event"] != "settlement_ready" || ready["session_id"] != fence.session_id {
        return Err(());
    }
    let settlement = settle_retired_session(&pool, retired)
        .await
        .map_err(|_| ())?;
    pool.close().await;
    let mut component = uncertainty["counts"].clone();
    if cancelled {
        if receipt.is_some() {
            return Err(());
        }
        component["durable_cancelled_finalization"] =
            settlement["durable_cancelled_finalization"].clone();
        component["source_consumer_settlement"] = settlement["durable_source_settlement"].clone();
    } else {
        component["receipt"] = receipt.ok_or(())?;
        component["result_commit_observation"] = json!("lost_ack");
        component["result_receipt_sent"] = json!(false);
    }
    component["reconciled_without_replay"] = json!(true);
    component["original_owner_crash_recovered"] = json!(true);
    Ok(
        json!({"event":"slice_result","component":component,"physical_drain":physical,
        "source_staging_removed":true,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
        "original_session_drained":true,"resource_settlement":settlement,
        "runtime_acceptance":false,"production_dispatch":false}),
    )
}

#[tokio::main]
async fn main() {
    // The trusted SDK fixture shares the PID namespace for original-peer IPC,
    // but must not inspect this owner's core credential environment or memory.
    if rustix::process::set_dumpable_behavior(rustix::process::DumpableBehavior::NotDumpable)
        .is_err()
    {
        std::process::exit(1);
    }
    let args: Vec<String> = std::env::args().collect();
    let drain = args.len() == 3 && args[1] == "--drain";
    let closed_cancel = args.len() == 3 && args[1] == "--recover-closed-cancel";
    let closed_recovery =
        args.len() == 3 && (args[1] == "--recover-closed-result" || closed_cancel);
    if args.len() != 2 && !drain && !closed_recovery {
        std::process::exit(1);
    }
    let result = match read(
        Path::new(if drain || closed_recovery {
            &args[2]
        } else {
            &args[1]
        }),
        65536,
    )
    .and_then(|raw| serde_json::from_slice(&raw).map_err(|_| ()))
    {
        Ok(config) if drain => recover(&config),
        Ok(config) if closed_recovery => recover_closed_session(&config, closed_cancel).await,
        Ok(config) => run(config).await,
        Err(()) => Err(()),
    };
    match result {
        Ok(value) => {
            if emit(&value).is_err() {
                std::process::exit(1);
            }
        }
        Err(()) => {
            eprintln!(
                "isolated live owner failed; original journals require inspection/stop-only cleanup"
            );
            std::process::exit(1);
        }
    }
}
