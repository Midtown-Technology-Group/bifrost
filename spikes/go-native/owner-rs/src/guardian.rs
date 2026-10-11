//! Private Linux guardian custody. No lifecycle/SDK authority or production dispatch.
//! The initial live lane permits only a dormant, network-none carrier. A retained
//! intent permits stop-only recovery, never another start or tenant initialization.
use crate::{
    SessionFence,
    archive::{ArchivePins, MAX_ARCHIVE, NAMES, sha256},
    canonical_uuid, digest,
    material::MaterialPipe,
    sdk_gate::SDKGate,
};
use bifrost_execution_wire_spike::{Codec, MAX_FRAME};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::AsRawFd,
        unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc::{self, Receiver},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Debug, PartialEq, Eq)]
pub enum GuardianError {
    Rejected,
    Uncertain,
    CleanupRequired,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn spec() -> LaunchSpec {
        LaunchSpec {
            session_id: "00000000-0000-0000-0000-000000000005".into(),
            nonce: "00000000-0000-0000-0000-000000000006".into(),
            image_id: format!("sha256:{}", "a".repeat(64)),
            index_sha256: "b".repeat(64),
            adapter: "/trusted/adapter".into(),
            bundle: "/trusted/bundle".into(),
            material: "/trusted/material".into(),
            journal: "/trusted/journal".into(),
            sdk_socket_directory: None,
            uid: 1001,
            gid: 1001,
        }
    }

    fn fence() -> SessionFence {
        SessionFence {
            execution_id: "00000000-0000-0000-0000-000000000001".into(),
            owner_incarnation_id: "00000000-0000-0000-0000-000000000002".into(),
            attempt_id: "00000000-0000-0000-0000-000000000003".into(),
            claim_token: "00000000-0000-0000-0000-000000000004".into(),
            worker_incarnation_id: "00000000-0000-0000-0000-000000000007".into(),
            session_id: spec().session_id,
            supervisor_incarnation_id: "00000000-0000-0000-0000-000000000008".into(),
            runtime_incarnation_id: "00000000-0000-0000-0000-000000000009".into(),
            binding_sha256: "c".repeat(64),
            channel_custody_sha256: "d".repeat(64),
        }
    }

    #[test]
    fn drain_observation_rejects_every_identity_substitution() {
        let original = fence();
        let proof = DrainedSession {
            fence: original.clone(),
            observation: json!({}),
            bundle: spec().bundle,
            material: spec().material,
            uid: spec().uid,
            index_sha256: spec().index_sha256,
        };
        assert!(proof.matches(&original));
        for field in 0..10 {
            let mut changed = original.clone();
            let target = match field {
                0 => &mut changed.execution_id,
                1 => &mut changed.owner_incarnation_id,
                2 => &mut changed.attempt_id,
                3 => &mut changed.claim_token,
                4 => &mut changed.worker_incarnation_id,
                5 => &mut changed.session_id,
                6 => &mut changed.supervisor_incarnation_id,
                7 => &mut changed.runtime_incarnation_id,
                8 => &mut changed.binding_sha256,
                _ => &mut changed.channel_custody_sha256,
            };
            *target = if field < 8 {
                "00000000-0000-0000-0000-000000000099".into()
            } else {
                "e".repeat(64)
            };
            assert!(!proof.matches(&changed), "field {field}");
        }
    }

    #[test]
    fn stop_only_custody_cannot_construct_original_session_drain() {
        let recovered = Guardian {
            spec: spec(),
            id: "a".repeat(64),
            may_start: false,
            attachment: None,
            kernel: None,
            custody_sha256: None,
            offered_runtime_id: None,
        };
        assert!(matches!(
            recovered.drain_session(&fence()),
            Err(GuardianError::Rejected)
        ));
    }

    #[test]
    fn original_custody_is_exclusive_durable_and_link_fenced() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_else(|_| panic!("clock"))
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("bifrost-custody-{}-{nanos}", std::process::id()));
        fs::create_dir(&root).unwrap_or_else(|_| panic!("temporary directory"));
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
            .unwrap_or_else(|_| panic!("permissions"));
        let mut spec = spec();
        spec.uid = fs::metadata(&root)
            .unwrap_or_else(|_| panic!("metadata"))
            .uid();
        spec.journal = root.clone();
        let observation = br#"{"version":"isolated-guardian-custody/v1","synthetic":true}"#;
        assert!(retain_custody(&spec, observation).is_ok());
        let raw = private_record(&spec, "original-custody.json")
            .unwrap_or_else(|_| panic!("retained custody"));
        let record: Value = serde_json::from_slice(&raw).unwrap_or_else(|_| panic!("journal"));
        assert_eq!(record["session_id"], spec.session_id);
        assert_eq!(record["nonce"], spec.nonce);
        assert_eq!(record["owner_pid"], std::process::id());
        assert_eq!(
            record["owner_start_ticks"],
            start_ticks(u64::from(std::process::id())).unwrap_or_else(|_| panic!("owner ticks"))
        );
        assert_eq!(record["observation_sha256"], sha256(observation));
        assert!(retain_custody(&spec, observation).is_err());
        assert_eq!(
            private_record(&spec, "original-custody.json").ok(),
            Some(raw)
        );
        let path = root.join("original-custody.json");
        let alias = root.join("alias");
        fs::hard_link(&path, &alias).unwrap_or_else(|_| panic!("hard link"));
        assert!(private_record(&spec, "original-custody.json").is_err());
        fs::remove_file(&alias).unwrap_or_else(|_| panic!("remove alias"));
        fs::remove_file(&path).unwrap_or_else(|_| panic!("remove custody"));
        std::os::unix::fs::symlink("missing", &path).unwrap_or_else(|_| panic!("symlink"));
        assert!(retain_custody(&spec, observation).is_err());
        assert!(private_record(&spec, "original-custody.json").is_err());
        fs::remove_file(&path).unwrap_or_else(|_| panic!("remove symlink"));
        fs::remove_dir(&root).unwrap_or_else(|_| panic!("remove directory"));
    }

    #[test]
    fn process_loss_requires_original_incarnation_gone() {
        let _physical_birth = crate::material::TEST_PROCESS_CREATION
            .lock()
            .unwrap_or_else(|_| panic!("poisoned fixture custody"));
        let pid = u64::from(std::process::id());
        let ticks = start_ticks(pid).unwrap_or_else(|_| panic!("owner ticks"));
        assert_eq!(process_gone(pid, &ticks), Ok(false));
        assert_eq!(process_gone(pid, "0"), Ok(true));
        for (pid, ticks) in [(0, "1"), (1, "1"), (pid, ""), (pid, "invalid")] {
            assert!(process_gone(pid, ticks).is_err());
        }
        let mut child = Command::new("/usr/bin/sleep")
            .arg("60")
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap_or_else(|_| panic!("owned child"));
        let child_pid = u64::from(child.id());
        let observed = start_ticks(child_pid);
        child.kill().unwrap_or_else(|_| panic!("kill owned child"));
        child.wait().unwrap_or_else(|_| panic!("wait owned child"));
        let child_ticks = observed.unwrap_or_else(|_| panic!("child ticks"));
        assert_eq!(process_gone(child_pid, &child_ticks), Ok(true));
    }

    #[test]
    fn retirement_custody_detects_directory_reassignment_and_mode_drift() {
        let _physical_birth = crate::material::TEST_PROCESS_CREATION
            .lock()
            .unwrap_or_else(|_| panic!("poisoned fixture custody"));
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_else(|_| panic!("clock"))
            .as_nanos();
        let root = std::env::temp_dir().join(format!("bf-retire-{}-{nanos}", std::process::id()));
        fs::create_dir(&root).unwrap_or_else(|_| panic!("temporary root"));
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
            .unwrap_or_else(|_| panic!("root permissions"));
        let mut spec = spec();
        spec.uid = fs::metadata(&root)
            .unwrap_or_else(|_| panic!("metadata"))
            .uid();
        spec.bundle = root.join("bundle");
        spec.journal = root.join("journal");
        let material = root.join("material");
        let gate_dir = root.join("gate");
        for path in [&spec.bundle, &spec.journal, &material, &gate_dir] {
            fs::create_dir(path).unwrap_or_else(|_| panic!("directory"));
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .unwrap_or_else(|_| panic!("permissions"));
        }
        fs::set_permissions(&spec.bundle, fs::Permissions::from_mode(0o500))
            .unwrap_or_else(|_| panic!("bundle permissions"));
        let pipe = MaterialPipe::create(&material, &spec.session_id, spec.uid)
            .unwrap_or_else(|_| panic!("original material"));
        spec.material = pipe.directory().to_owned();
        let gate = SDKGate::create(&gate_dir, spec.uid).unwrap_or_else(|_| panic!("original gate"));
        let original = retirement_custody(&spec).unwrap_or_else(|_| panic!("original custody"));
        exclusive_record(&spec, "retirement-custody.json", &original)
            .unwrap_or_else(|_| panic!("journal"));
        assert!(exclusive_record(&spec, "retirement-custody.json", &original).is_err());
        fs::set_permissions(gate.path(), fs::Permissions::from_mode(0o640))
            .unwrap_or_else(|_| panic!("gate drift"));
        assert!(retirement_custody(&spec).is_err());
        fs::set_permissions(gate.path(), fs::Permissions::from_mode(0o600))
            .unwrap_or_else(|_| panic!("restore gate"));
        let old = root.join("old-bundle");
        fs::rename(&spec.bundle, &old).unwrap_or_else(|_| panic!("reassign directory"));
        fs::create_dir(&spec.bundle).unwrap_or_else(|_| panic!("replacement directory"));
        fs::set_permissions(&spec.bundle, fs::Permissions::from_mode(0o500))
            .unwrap_or_else(|_| panic!("replacement permissions"));
        assert_ne!(retirement_custody(&spec).ok(), Some(original));
        pipe.retire()
            .unwrap_or_else(|_| panic!("retire original material"));
        gate.retire()
            .unwrap_or_else(|_| panic!("retire original gate"));
        fs::remove_file(spec.journal.join("retirement-custody.json"))
            .unwrap_or_else(|_| panic!("remove journal"));
        for path in [&spec.bundle, &old, &spec.journal, &material, &gate_dir] {
            fs::remove_dir(path).unwrap_or_else(|_| panic!("remove directory"));
        }
        fs::remove_dir(root).unwrap_or_else(|_| panic!("remove root"));
    }

    #[test]
    fn staged_retirement_rejects_symlinks_hardlinks_and_wrong_custody() {
        use std::os::unix::fs::symlink;
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_else(|_| panic!("clock"))
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("bifrost-staged-{}-{nanos}", std::process::id()));
        fs::create_dir(&root).unwrap_or_else(|_| panic!("temporary directory"));
        let path = root.join("source");
        fs::write(&path, b"immutable fixture").unwrap_or_else(|_| panic!("fixture"));
        fs::set_permissions(&path, fs::Permissions::from_mode(0o400))
            .unwrap_or_else(|_| panic!("permissions"));
        let uid = fs::metadata(&path)
            .unwrap_or_else(|_| panic!("metadata"))
            .uid();
        assert_eq!(
            staged_bytes(&path, uid, 0o400, 64).ok(),
            Some(b"immutable fixture".to_vec())
        );
        assert!(staged_bytes(&path, uid ^ 1, 0o400, 64).is_err());
        assert!(staged_bytes(&path, uid, 0o500, 64).is_err());
        assert!(staged_bytes(&path, uid, 0o400, 1).is_err());
        let alias = root.join("alias");
        symlink(&path, &alias).unwrap_or_else(|_| panic!("symlink"));
        assert!(staged_bytes(&alias, uid, 0o400, 64).is_err());
        fs::remove_file(&alias).unwrap_or_else(|_| panic!("remove alias"));
        fs::hard_link(&path, &alias).unwrap_or_else(|_| panic!("hard link"));
        assert!(staged_bytes(&path, uid, 0o400, 64).is_err());
        fs::remove_file(&alias).unwrap_or_else(|_| panic!("remove alias"));
        fs::remove_file(&path).unwrap_or_else(|_| panic!("remove source"));
        fs::remove_dir(&root).unwrap_or_else(|_| panic!("remove directory"));
    }

    #[test]
    fn observed_footprint_rejects_privilege_network_or_source_substitution() {
        let spec = spec();
        let snapshot = json!({"Name":format!("/{}",spec.name()),"Image":spec.image_id,
            "Config":{"User":spec.user(),"Tty":false,"Entrypoint":["/launcher"],"Cmd":["--index-sha256",spec.index_sha256],
                "Labels":{"bifrost.isolated.guardian.nonce":spec.nonce,"bifrost.isolated.guardian.session":spec.session_id},"Env":["PATH=/usr/bin:/bin"]},
            "HostConfig":{"NetworkMode":"none","ReadonlyRootfs":true,"Privileged":false,"CapDrop":["ALL"],"SecurityOpt":["no-new-privileges"],
                "PidMode":"","IpcMode":"private","PidsLimit":64,"Memory":134217728,"NanoCpus":2000000000_u64,"AutoRemove":false,"RestartPolicy":{"Name":"no","MaximumRetryCount":0}},
            "NetworkSettings":{"Networks":{"none":{}}},
            "Mounts":[{"Type":"bind","Source":"/trusted/adapter","Destination":"/adapter","RW":false},
                {"Type":"bind","Source":"/trusted/bundle","Destination":"/bundle","RW":false},
                {"Type":"bind","Source":"/trusted/material","Destination":"/material","RW":false}]});
        assert!(spec.matches(&snapshot));
        for (pointer, value) in [
            ("/HostConfig/NetworkMode", json!("host")),
            ("/HostConfig/Privileged", json!(true)),
            ("/HostConfig/ReadonlyRootfs", json!(false)),
            ("/HostConfig/PidMode", json!("host")),
            ("/NetworkSettings/Networks", json!({"none":{},"db":{}})),
            ("/HostConfig/RestartPolicy/Name", json!("always")),
            ("/Config/User", json!("0:0")),
            (
                "/Config/Env",
                json!(["PATH=/usr/bin:/bin", "DATABASE_URL=forbidden"]),
            ),
            (
                "/Config/Labels/bifrost.isolated.guardian.nonce",
                json!("changed"),
            ),
            ("/Mounts/0/Source", json!("/var/run/docker.sock")),
            ("/Mounts/1/RW", json!(true)),
        ] {
            let mut changed = snapshot.clone();
            *changed
                .pointer_mut(pointer)
                .unwrap_or_else(|| panic!("invalid trusted pointer")) = value;
            assert!(!spec.matches(&changed), "{pointer}");
        }
        let mut relay_spec = spec.clone();
        relay_spec.sdk_socket_directory = Some("/trusted/sdk".into());
        let mut relay_snapshot = snapshot.clone();
        relay_snapshot["Config"]["Cmd"] =
            json!(["--index-sha256", spec.index_sha256, "--sdk-relay"]);
        relay_snapshot["Mounts"]
            .as_array_mut()
            .unwrap_or_else(|| panic!("trusted mounts"))
            .push(json!({"Type":"bind","Source":"/trusted/sdk","Destination":"/sdk","RW":false}));
        assert!(relay_spec.matches(&relay_snapshot));
        assert!(!spec.matches(&relay_snapshot));
        relay_snapshot["Mounts"][3]["RW"] = json!(true);
        assert!(!relay_spec.matches(&relay_snapshot));
    }

    #[test]
    fn raw_received_preimage_is_not_json_reserialization() {
        let payload = br#" { "protocol":"bifrost.runtime/v1", "type":"Offer", "session_id":"00000000-0000-0000-0000-000000000005", "message_id":"00000000-0000-0000-0000-000000000064", "sequence":1, "correlation_id":null, "body":{"runtime_incarnation_id":"00000000-0000-0000-0000-000000000007","supported_protocols":["bifrost.runtime/v1"],"capabilities":["execution_profile/v1"],"artifact_classes":["native-executable/v1"]} } "#;
        let mut wire = (payload.len() as u32).to_be_bytes().to_vec();
        wire.extend(payload);
        let codec = Codec::new().unwrap_or_else(|_| panic!("trusted codec"));
        let received = receive(&mut Cursor::new(wire), &codec)
            .unwrap_or_else(|e| panic!("valid frame: {e:?}"))
            .unwrap_or_else(|| panic!("missing frame"));
        assert_eq!(received.payload(), payload);
        assert_ne!(
            serde_json::to_vec(received.frame()).ok().as_deref(),
            Some(payload.as_slice())
        );
        assert!(
            receive(
                &mut Cursor::new((MAX_FRAME as u32 + 1).to_be_bytes()),
                &codec
            )
            .is_err()
        );
        assert!(receive(&mut Cursor::new([0, 0, 0, 1]), &codec).is_err());
    }

    #[test]
    fn recovered_custody_cannot_enter_start_even_before_daemon_inspection() {
        let mut guardian = Guardian {
            spec: spec(),
            id: "c".repeat(64),
            may_start: false,
            attachment: None,
            kernel: None,
            custody_sha256: None,
            offered_runtime_id: None,
        };
        assert_eq!(guardian.attach_dormant(), Err(GuardianError::Rejected));
        assert_eq!(guardian.verify_live(), Err(GuardianError::Rejected));
        assert!(matches!(
            guardian.receive_if_ready(),
            Err(GuardianError::Rejected)
        ));
        assert!(guardian.attachment.is_none());
    }

    #[test]
    fn fresh_kernel_observation_cannot_replace_retained_incarnation() {
        let original = KernelObservation {
            pid: 123,
            start_ticks: "456".into(),
            cgroup: "/sys/fs/cgroup/original".into(),
        };
        for current in [
            KernelObservation {
                pid: 124,
                ..original.clone()
            },
            KernelObservation {
                start_ticks: "457".into(),
                ..original.clone()
            },
            KernelObservation {
                cgroup: "/sys/fs/cgroup/replacement".into(),
                ..original.clone()
            },
        ] {
            assert!(!original.same_incarnation(&current));
        }
        assert!(original.same_incarnation(&original.clone()));
    }
}

/// Inputs belong to the trusted coordinator, never the tenant or wire protocol.
/// Accepted source/producer/artifact association remains an external prerequisite.
#[derive(Clone)]
pub struct LaunchSpec {
    pub session_id: String,
    pub nonce: String,
    pub image_id: String,
    pub index_sha256: String,
    pub adapter: PathBuf,
    pub bundle: PathBuf,
    pub material: PathBuf,
    pub journal: PathBuf,
    /// Optional, private per-session directory containing only ingress.sock.
    /// The socket terminates at the trusted restricted SDK ingress; no DB/key
    /// directory is mounted and the runtime's network remains disabled.
    pub sdk_socket_directory: Option<PathBuf>,
    pub uid: u32,
    pub gid: u32,
}
impl LaunchSpec {
    fn name(&self) -> String {
        format!("bifrost-guardian-{}", self.session_id)
    }
    fn user(&self) -> String {
        format!("{}:{}", self.uid, self.gid)
    }
    fn valid(&self, require_ingress: bool) -> bool {
        self.uid != 0
            && self.gid != 0
            && canonical_uuid(&self.session_id)
            && canonical_uuid(&self.nonce)
            && digest(&self.index_sha256)
            && self.image_id.strip_prefix("sha256:").is_some_and(digest)
            && [&self.adapter, &self.bundle, &self.material, &self.journal]
                .into_iter()
                .all(|p| {
                    p.is_absolute()
                        && p.to_str().is_some_and(|s| !s.contains([',', '\n', '\r']))
                        && p.canonicalize().is_ok_and(|real| real == *p)
                        && fs::symlink_metadata(p)
                            .is_ok_and(|m| m.uid() == self.uid && m.mode() & 0o077 == 0)
                })
            && self.adapter.is_file()
            && self.bundle.is_dir()
            && self.material.is_dir()
            && self.journal.is_dir()
            && !self.journal.starts_with(&self.bundle)
            && !self.journal.starts_with(&self.material)
            && !self.material.starts_with(&self.bundle)
            && !self.bundle.starts_with(&self.material)
            && self.sdk_socket_directory.as_ref().is_none_or(|directory| {
                directory.is_absolute()
                    && directory
                        .to_str()
                        .is_some_and(|s| !s.contains([',', '\n', '\r']))
                    && directory
                        .canonicalize()
                        .is_ok_and(|real| real == *directory)
                    && fs::symlink_metadata(directory).is_ok_and(|m| {
                        m.is_dir() && m.uid() == self.uid && m.mode() & 0o777 == 0o700
                    })
                    && [&self.bundle, &self.material, &self.journal]
                        .into_iter()
                        .all(|other| !directory.starts_with(other) && !other.starts_with(directory))
                    && fs::read_dir(directory).is_ok_and(|entries| {
                        let entries: Result<Vec<_>, _> = entries.collect();
                        entries.is_ok_and(|items| {
                            (!require_ingress && items.is_empty())
                                || (items.len() == 1 && items[0].file_name() == "ingress.sock")
                        })
                    })
                    && match fs::symlink_metadata(directory.join("ingress.sock")) {
                        Ok(m) => {
                            m.file_type().is_socket()
                                && m.uid() == self.uid
                                && m.mode() & 0o777 == 0o600
                        }
                        Err(e) => !require_ingress && e.kind() == std::io::ErrorKind::NotFound,
                    }
            })
    }
    fn launch_args(&self) -> Value {
        if self.sdk_socket_directory.is_some() {
            json!(["--index-sha256", self.index_sha256, "--sdk-relay"])
        } else {
            json!(["--index-sha256", self.index_sha256])
        }
    }
    fn mounts(&self) -> Vec<(&PathBuf, &'static str)> {
        let mut mounts = vec![
            (&self.adapter, "/adapter"),
            (&self.bundle, "/bundle"),
            (&self.material, "/material"),
        ];
        if let Some(directory) = &self.sdk_socket_directory {
            mounts.push((directory, "/sdk"));
        }
        mounts
    }
    fn intent(&self) -> Value {
        json!({"version":"isolated-guardian-intent/v1", "session_id":self.session_id,
            "nonce":self.nonce, "name":self.name(), "image_id":self.image_id,
            "index_sha256":self.index_sha256, "adapter":self.adapter,
            "bundle":self.bundle, "material":self.material, "user":self.user(),
            "network":"none", "tenant_release":false,"sdk_socket_directory":self.sdk_socket_directory})
    }
    fn matches(&self, snapshot: &Value) -> bool {
        let host = &snapshot["HostConfig"];
        let config = &snapshot["Config"];
        let mounts = snapshot["Mounts"].as_array();
        snapshot["Name"] == format!("/{}", self.name())
            && snapshot["Image"] == self.image_id
            && config["User"] == self.user()
            && config["Tty"] == false
            && config["Entrypoint"] == json!(["/launcher"])
            && config["Cmd"] == self.launch_args()
            && config["Labels"]["bifrost.isolated.guardian.nonce"] == self.nonce
            && config["Labels"]["bifrost.isolated.guardian.session"] == self.session_id
            && config["Env"].as_array().is_some_and(|env| {
                env.len() == 1 && env[0].as_str().is_some_and(|s| s.starts_with("PATH="))
            })
            && host["NetworkMode"] == "none"
            && host["ReadonlyRootfs"] == true
            && host["Privileged"] == false
            && host["CapDrop"] == json!(["ALL"])
            && host["SecurityOpt"] == json!(["no-new-privileges"])
            && host["PidMode"] == ""
            && host["IpcMode"] == "private"
            && host["PidsLimit"] == 64
            && host["Memory"] == 134217728
            && host["NanoCpus"] == 2000000000_u64
            && host["AutoRemove"] == false
            && host["RestartPolicy"]["Name"] == "no"
            && host["RestartPolicy"]["MaximumRetryCount"] == 0
            && snapshot["NetworkSettings"]["Networks"]
                .as_object()
                .is_some_and(|net| net.len() == 1 && net.contains_key("none"))
            && mounts.is_some_and(|m| {
                m.len() == self.mounts().len()
                    && self.mounts().into_iter().all(|(source, destination)| {
                        m.iter()
                            .filter(|item| {
                                item["Type"] == "bind"
                                    && item["Source"].as_str() == source.to_str()
                                    && item["Destination"] == destination
                                    && item["RW"] == false
                            })
                            .count()
                            == 1
                    })
            })
    }
}

fn private_record(spec: &LaunchSpec, name: &str) -> Result<Vec<u8>, GuardianError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(spec.journal.join(name))
        .map_err(|_| GuardianError::Rejected)?;
    let meta = file.metadata().map_err(|_| GuardianError::Rejected)?;
    if !meta.is_file()
        || meta.uid() != spec.uid
        || meta.mode() & 0o777 != 0o600
        || meta.nlink() != 1
    {
        return Err(GuardianError::Rejected);
    }
    let mut raw = Vec::new();
    file.take(65537)
        .read_to_end(&mut raw)
        .map_err(|_| GuardianError::Rejected)?;
    if raw.is_empty() || raw.len() > 65536 {
        return Err(GuardianError::Rejected);
    }
    Ok(raw)
}

fn exclusive_record(spec: &LaunchSpec, name: &str, value: &Value) -> Result<(), GuardianError> {
    let raw = serde_json::to_vec(value).map_err(|_| GuardianError::Rejected)?;
    if raw.is_empty() || raw.len() > 65536 {
        return Err(GuardianError::Rejected);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(spec.journal.join(name))
        .map_err(|_| GuardianError::Uncertain)?;
    let metadata = file.metadata().map_err(|_| GuardianError::Uncertain)?;
    if metadata.uid() != spec.uid || metadata.mode() & 0o777 != 0o600 || metadata.nlink() != 1 {
        return Err(GuardianError::Rejected);
    }
    file.write_all(&raw)
        .and_then(|_| file.sync_all())
        .map_err(|_| GuardianError::Uncertain)?;
    File::open(&spec.journal)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| GuardianError::Uncertain)
}

fn retirement_custody(spec: &LaunchSpec) -> Result<Value, GuardianError> {
    let root = spec.bundle.parent().ok_or(GuardianError::Rejected)?;
    if spec.material != root.join("material").join(&spec.session_id) {
        return Err(GuardianError::Rejected);
    }
    let mut entries = Vec::new();
    for (path, kind, mode) in [
        (spec.bundle.clone(), "directory", 0o500),
        (root.join("material"), "directory", 0o700),
        (spec.material.clone(), "directory", 0o700),
        (spec.material.join("sdk.pipe"), "fifo", 0o600),
        (root.join("gate"), "directory", 0o700),
        (root.join("gate/gate.sock"), "socket", 0o600),
    ] {
        let metadata = fs::symlink_metadata(&path).map_err(|_| GuardianError::Rejected)?;
        let valid_type = match kind {
            "directory" => metadata.is_dir(),
            "fifo" => metadata.file_type().is_fifo(),
            "socket" => metadata.file_type().is_socket(),
            _ => false,
        };
        if !valid_type
            || metadata.uid() != spec.uid
            || metadata.mode() & 0o777 != mode
            || (kind != "directory" && metadata.nlink() != 1)
        {
            return Err(GuardianError::Rejected);
        }
        entries.push(json!({"path":path,"kind":kind,"mode":mode,"device":metadata.dev(),"inode":metadata.ino()}));
    }
    Ok(
        json!({"version":"isolated-original-retirement-custody/v1", "session_id":spec.session_id,
        "nonce":spec.nonce,"index_sha256":spec.index_sha256,"entries":entries}),
    )
}

fn process_gone(pid: u64, ticks: &str) -> Result<bool, GuardianError> {
    if pid < 2 || ticks.is_empty() || !ticks.bytes().all(|b| b.is_ascii_digit()) {
        return Err(GuardianError::Rejected);
    }
    match fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(text) => Ok(text
            .rsplit_once(") ")
            .and_then(|(_, tail)| tail.split_whitespace().nth(19))
            .ok_or(GuardianError::Rejected)?
            != ticks),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(_) => Err(GuardianError::CleanupRequired),
    }
}

fn verify_staged_source(spec: &LaunchSpec, pins: &ArchivePins) -> Result<Value, GuardianError> {
    for (i, name) in NAMES.iter().enumerate() {
        let mode = if *name == "workflow" || *name == "adapter" {
            0o500
        } else {
            0o400
        };
        if sha256(&staged_bytes(
            &spec.bundle.join(name),
            spec.uid,
            mode,
            MAX_ARCHIVE,
        )?) != pins.entries_sha256[i]
        {
            return Err(GuardianError::Rejected);
        }
    }
    let raw = staged_bytes(
        &spec.bundle.join("session-bundle.json"),
        spec.uid,
        0o400,
        65536,
    )?;
    if sha256(&raw) != spec.index_sha256 {
        return Err(GuardianError::Rejected);
    }
    serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)
}

fn docker_command() -> Command {
    let mut command = Command::new("/usr/bin/docker");
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", "/nonexistent");
    command
}

/// Bound both the first-party CLI and its output. An ambiguous CLI outcome is
/// never converted into another create/start attempt. No secrets in arguments.
fn docker(args: &[String]) -> Result<Vec<u8>, GuardianError> {
    let mut child = docker_command()
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| GuardianError::Uncertain)?;
    let stdout = child.stdout.take().ok_or(GuardianError::Uncertain)?;
    let output = thread::spawn(move || {
        let mut raw = Vec::new();
        stdout.take(1048577).read_to_end(&mut raw).map(|_| raw)
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    let raw = output
        .join()
        .map_err(|_| GuardianError::Uncertain)?
        .map_err(|_| GuardianError::Uncertain)?;
    if status.is_some_and(|s| s.success()) && raw.len() <= 1048576 {
        Ok(raw)
    } else {
        Err(GuardianError::Uncertain)
    }
}
fn args(values: &[&str]) -> Vec<String> {
    values.iter().map(|s| (*s).into()).collect()
}

fn inspect(id: &str) -> Result<Value, GuardianError> {
    let raw = docker(&args(&["container", "inspect", id]))?;
    let values: Vec<Value> = serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)?;
    if values.len() != 1 {
        return Err(GuardianError::Rejected);
    }
    values.into_iter().next().ok_or(GuardianError::Rejected)
}

pub struct ReceivedFrame {
    payload: Vec<u8>,
    frame: Value,
}
impl ReceivedFrame {
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
    pub fn frame(&self) -> &Value {
        &self.frame
    }
}
fn receive(reader: &mut impl Read, codec: &Codec) -> Result<Option<ReceivedFrame>, GuardianError> {
    let mut first = [0; 1];
    if reader
        .read(&mut first)
        .map_err(|_| GuardianError::Uncertain)?
        == 0
    {
        return Ok(None);
    }
    let mut prefix = [0; 4];
    prefix[0] = first[0];
    reader
        .read_exact(&mut prefix[1..])
        .map_err(|_| GuardianError::Uncertain)?;
    let length = u32::from_be_bytes(prefix) as usize;
    if length == 0 || length > MAX_FRAME {
        return Err(GuardianError::Rejected);
    }
    let mut payload = vec![0; length];
    reader
        .read_exact(&mut payload)
        .map_err(|_| GuardianError::Uncertain)?;
    let decoded = codec
        .decode(&payload)
        .map_err(|_| GuardianError::Rejected)?;
    Ok(Some(ReceivedFrame {
        payload,
        frame: decoded.frame,
    }))
}

struct Attachment {
    child: Child,
    input: Option<File>,
    output: Option<Receiver<Result<Option<ReceivedFrame>, GuardianError>>>,
    reader: Option<JoinHandle<()>>,
}
impl Drop for Attachment {
    fn drop(&mut self) {
        self.input.take();
        self.output.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
        // This reaps the owned first-party CLI only; never claims kernel drain.
    }
}

/// Actual stream/container ownership. Neither Debug nor Clone can disclose or
/// duplicate private channel handles. Recovery constructs stop-only custody.
pub struct Guardian {
    spec: LaunchSpec,
    id: String,
    may_start: bool,
    attachment: Option<Attachment>,
    kernel: Option<KernelObservation>,
    custody_sha256: Option<String>,
    offered_runtime_id: Option<String>,
}

/// Consumed original-session drain evidence. Public JSON, advisory Stopped and
/// stop-only recovery cannot construct this value. It proves physical custody
/// settlement only; staged source and ingress retirement remain prerequisites
/// for any future durable resource-settlement transaction.
pub struct DrainedSession {
    fence: SessionFence,
    observation: Value,
    bundle: PathBuf,
    material: PathBuf,
    uid: u32,
    index_sha256: String,
}

/// Actual original process, gate, material and immutable staged-source closure.
/// No JSON/deserialization/Clone constructor; the common writer consumes this
/// value once. A failed/uncertain write cannot recover it for another action.
pub struct RetiredSession {
    pub(crate) fence: SessionFence,
    pub(crate) observation: Value,
}

impl RetiredSession {
    pub fn observation(&self) -> &Value {
        &self.observation
    }
}

impl DrainedSession {
    pub fn observation(&self) -> &Value {
        &self.observation
    }

    /// Identity comparison discloses no claim token or private configuration.
    pub fn matches(&self, fence: &SessionFence) -> bool {
        self.fence.execution_id == fence.execution_id
            && self.fence.owner_incarnation_id == fence.owner_incarnation_id
            && self.fence.attempt_id == fence.attempt_id
            && self.fence.claim_token == fence.claim_token
            && self.fence.worker_incarnation_id == fence.worker_incarnation_id
            && self.fence.session_id == fence.session_id
            && self.fence.supervisor_incarnation_id == fence.supervisor_incarnation_id
            && self.fence.runtime_incarnation_id == fence.runtime_incarnation_id
            && self.fence.binding_sha256 == fence.binding_sha256
            && self.fence.channel_custody_sha256 == fence.channel_custody_sha256
    }

    pub fn retire_resources(
        self,
        pipe: MaterialPipe,
        gate: SDKGate,
        pins: &ArchivePins,
    ) -> Result<RetiredSession, GuardianError> {
        let root = self.bundle.parent().ok_or(GuardianError::Rejected)?;
        let gate_dir = root.join("gate");
        let material_dir = root.join("material");
        if pipe.directory() != self.material.as_path()
            || gate.path().parent() != Some(gate_dir.as_path())
        {
            return Err(GuardianError::Rejected);
        }
        let directory = fs::symlink_metadata(&self.bundle).map_err(|_| GuardianError::Rejected)?;
        if !directory.is_dir() || directory.uid() != self.uid || directory.mode() & 0o777 != 0o500 {
            return Err(GuardianError::Rejected);
        }
        // Verify the complete accepted source before removing any entry. Reads
        // never follow links; the task-owned 0700 root is outside tenant mounts.
        for (i, name) in NAMES.iter().enumerate() {
            let mode = if *name == "workflow" || *name == "adapter" {
                0o500
            } else {
                0o400
            };
            if sha256(&staged_bytes(
                &self.bundle.join(name),
                self.uid,
                mode,
                MAX_ARCHIVE,
            )?) != pins.entries_sha256[i]
            {
                return Err(GuardianError::Rejected);
            }
        }
        if sha256(&staged_bytes(
            &self.bundle.join("session-bundle.json"),
            self.uid,
            0o400,
            65536,
        )?) != self.index_sha256
        {
            return Err(GuardianError::Rejected);
        }
        pipe.retire().map_err(|_| GuardianError::CleanupRequired)?;
        gate.retire().map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_dir(&gate_dir).map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_dir(&material_dir).map_err(|_| GuardianError::CleanupRequired)?;
        let current =
            fs::symlink_metadata(&self.bundle).map_err(|_| GuardianError::CleanupRequired)?;
        if (current.dev(), current.ino()) != (directory.dev(), directory.ino()) {
            return Err(GuardianError::CleanupRequired);
        }
        fs::set_permissions(&self.bundle, fs::Permissions::from_mode(0o700))
            .map_err(|_| GuardianError::CleanupRequired)?;
        for name in NAMES.iter().chain(std::iter::once(&"session-bundle.json")) {
            fs::remove_file(self.bundle.join(name)).map_err(|_| GuardianError::CleanupRequired)?;
        }
        // Unknown source entries or incomplete retirement cannot produce proof.
        fs::remove_dir(&self.bundle).map_err(|_| GuardianError::CleanupRequired)?;
        Ok(RetiredSession {
            fence: self.fence,
            observation: json!({
                "version":"bifrost.runtime-resource-settlement/v1",
                "physical_drain":self.observation,"staged_source_removed":true,
                "material_retired":true,"sdk_gate_retired":true,
                "index_sha256":self.index_sha256,"archive_sha256":pins.archive_sha256
            }),
        })
    }
}

fn staged_bytes(path: &Path, uid: u32, mode: u32, limit: usize) -> Result<Vec<u8>, GuardianError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| GuardianError::Rejected)?;
    let metadata = file.metadata().map_err(|_| GuardianError::Rejected)?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.mode() & 0o777 != mode
        || metadata.nlink() != 1
    {
        return Err(GuardianError::Rejected);
    }
    let mut raw = Vec::new();
    file.take((limit + 1) as u64)
        .read_to_end(&mut raw)
        .map_err(|_| GuardianError::Rejected)?;
    if raw.is_empty() || raw.len() > limit {
        return Err(GuardianError::Rejected);
    }
    Ok(raw)
}

#[derive(Clone)]
struct KernelObservation {
    pid: u64,
    start_ticks: String,
    cgroup: PathBuf,
}
impl KernelObservation {
    fn same_incarnation(&self, current: &Self) -> bool {
        self.pid == current.pid
            && self.start_ticks == current.start_ticks
            && self.cgroup == current.cgroup
    }
}
fn retain_kernel(
    spec: &LaunchSpec,
    id: &str,
    kernel: &KernelObservation,
) -> Result<(), GuardianError> {
    let raw = serde_json::to_vec(&json!({"version":"isolated-guardian-kernel/v1",
        "session_id":spec.session_id,"nonce":spec.nonce,"container_id":id,
        "pid":kernel.pid,"start_ticks":kernel.start_ticks,"cgroup":kernel.cgroup}))
    .map_err(|_| GuardianError::Rejected)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(spec.journal.join("kernel-observation.json"))
        .map_err(|_| GuardianError::Uncertain)?;
    file.write_all(&raw)
        .and_then(|_| file.sync_all())
        .map_err(|_| GuardianError::Uncertain)?;
    File::open(&spec.journal)
        .and_then(|dir| dir.sync_all())
        .map_err(|_| GuardianError::Uncertain)
}

/// Retain the original observation before SQL admission. This private journal
/// grants no recovered channel, Start, material or lifecycle authority. A later
/// closed-only recovery must independently match SQL custody and prove the old
/// owner/CLI/kernel gone; it cannot turn this record into a fresh observation.
fn retain_custody(spec: &LaunchSpec, observation: &[u8]) -> Result<(), GuardianError> {
    let raw = serde_json::to_vec(&json!({
        "version":"isolated-original-custody-journal/v1",
        "session_id":spec.session_id,"nonce":spec.nonce,
        "owner_pid":std::process::id(),
        "owner_start_ticks":start_ticks(u64::from(std::process::id()))?,
        "observation_sha256":sha256(observation),
        "observation":serde_json::from_slice::<Value>(observation).map_err(|_| GuardianError::Rejected)?
    })).map_err(|_| GuardianError::Rejected)?;
    if raw.len() > 65536 {
        return Err(GuardianError::Rejected);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(spec.journal.join("original-custody.json"))
        .map_err(|_| GuardianError::Uncertain)?;
    let metadata = file.metadata().map_err(|_| GuardianError::Uncertain)?;
    if metadata.uid() != spec.uid || metadata.mode() & 0o777 != 0o600 || metadata.nlink() != 1 {
        return Err(GuardianError::Rejected);
    }
    file.write_all(&raw)
        .and_then(|_| file.sync_all())
        .map_err(|_| GuardianError::Uncertain)?;
    File::open(&spec.journal)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| GuardianError::Uncertain)
}
fn retained_kernel(
    spec: &LaunchSpec,
    id: &str,
) -> Result<Option<KernelObservation>, GuardianError> {
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(spec.journal.join("kernel-observation.json"))
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(GuardianError::Rejected),
    };
    let meta = file.metadata().map_err(|_| GuardianError::Rejected)?;
    if !meta.is_file() || meta.uid() != spec.uid || meta.mode() & 0o777 != 0o600 {
        return Err(GuardianError::Rejected);
    }
    let mut raw = Vec::new();
    file.take(65537)
        .read_to_end(&mut raw)
        .map_err(|_| GuardianError::Rejected)?;
    if raw.len() > 65536 {
        return Err(GuardianError::Rejected);
    }
    let record: Value = serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)?;
    if record.as_object().map(|o| o.len()) != Some(7)
        || record["version"] != "isolated-guardian-kernel/v1"
        || record["session_id"] != spec.session_id
        || record["nonce"] != spec.nonce
        || record["container_id"] != id
    {
        return Err(GuardianError::Rejected);
    }
    let pid = record["pid"]
        .as_u64()
        .filter(|p| *p > 0)
        .ok_or(GuardianError::Rejected)?;
    let ticks = record["start_ticks"]
        .as_str()
        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        .ok_or(GuardianError::Rejected)?
        .to_owned();
    let relative = record["cgroup"]
        .as_str()
        .and_then(|p| p.strip_prefix("/sys/fs/cgroup/"))
        .filter(|p| !p.is_empty() && p.split('/').all(|s| !s.is_empty() && s != "." && s != ".."))
        .ok_or(GuardianError::Rejected)?;
    Ok(Some(KernelObservation {
        pid,
        start_ticks: ticks,
        cgroup: Path::new("/sys/fs/cgroup").join(relative),
    }))
}
fn start_ticks(pid: u64) -> Result<String, GuardianError> {
    let text = fs::read_to_string(format!("/proc/{pid}/stat"))
        .map_err(|_| GuardianError::CleanupRequired)?;
    text.rsplit_once(") ")
        .and_then(|(_, tail)| tail.split_whitespace().nth(19))
        .map(str::to_owned)
        .ok_or(GuardianError::Rejected)
}
fn kernel_observation(pid: u64) -> Result<KernelObservation, GuardianError> {
    if pid == 0 {
        return Err(GuardianError::Rejected);
    }
    let ticks = start_ticks(pid)?;
    let network =
        fs::read_to_string(format!("/proc/{pid}/net/dev")).map_err(|_| GuardianError::Rejected)?;
    let interfaces: Vec<_> = network
        .lines()
        .filter_map(|line| line.split_once(':').map(|(name, _)| name.trim()))
        .collect();
    if interfaces != ["lo"] {
        return Err(GuardianError::Rejected);
    }
    let text =
        fs::read_to_string(format!("/proc/{pid}/cgroup")).map_err(|_| GuardianError::Rejected)?;
    let relative = text
        .lines()
        .find_map(|line| line.strip_prefix("0::/"))
        .filter(|p| !p.is_empty() && p.split('/').all(|s| !s.is_empty() && s != "." && s != ".."))
        .ok_or(GuardianError::Rejected)?;
    let cgroup = Path::new("/sys/fs/cgroup").join(relative);
    let pids =
        fs::read_to_string(cgroup.join("cgroup.procs")).map_err(|_| GuardianError::Rejected)?;
    if !pids.lines().any(|p| p == pid.to_string()) || start_ticks(pid)? != ticks {
        return Err(GuardianError::Rejected);
    }
    Ok(KernelObservation {
        pid,
        start_ticks: ticks,
        cgroup,
    })
}

impl Guardian {
    /// The original owner retains exact cleanup identities before admission.
    /// This does not expose any recovered constructor for channel/material use.
    pub fn retain_retirement_custody(&self) -> Result<(), GuardianError> {
        if !self.may_start || self.attachment.is_some() || self.custody_sha256.is_some() {
            return Err(GuardianError::Rejected);
        }
        exclusive_record(
            &self.spec,
            "retirement-custody.json",
            &retirement_custody(&self.spec)?,
        )
    }

    /// Consume stop-only custody and an independently observed immutable closed
    /// Result. No channel or effect is replayed and owner identity is unchanged.
    pub fn retire_closed_result(
        self,
        closed: crate::ClosedResultRecovery,
        pins: &ArchivePins,
    ) -> Result<RetiredSession, GuardianError> {
        if self.may_start || self.attachment.is_some() || self.kernel.is_none() {
            return Err(GuardianError::Rejected);
        }
        if closed.artifact_id != format!("sha256:{}", pins.archive_sha256) {
            return Err(GuardianError::Rejected);
        }
        let spec = self.spec.clone();
        let fence = closed.fence;
        let raw = private_record(&spec, "original-custody.json")?;
        let record: Value = serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)?;
        let observation = &record["observation"];
        let kernel = self.kernel.as_ref().ok_or(GuardianError::Rejected)?;
        if record.as_object().map(|o| o.len()) != Some(7)
            || record["version"] != "isolated-original-custody-journal/v1"
            || record["session_id"] != spec.session_id
            || record["nonce"] != spec.nonce
            || fence.session_id != spec.session_id
            || record["observation_sha256"] != fence.channel_custody_sha256
            || sha256(&serde_json::to_vec(observation).map_err(|_| GuardianError::Rejected)?)
                != fence.channel_custody_sha256
            || observation["session_id"] != spec.session_id
            || observation["nonce"] != spec.nonce
            || observation["container_id"] != self.id
            || observation["image_id"] != spec.image_id
            || observation["index_sha256"] != spec.index_sha256
            || record["owner_pid"] != observation["owner_pid"]
            || record["owner_start_ticks"] != observation["owner_start_ticks"]
            || observation["init_pid"] != kernel.pid
            || observation["init_start_ticks"] != kernel.start_ticks
            || observation["cgroup"] != kernel.cgroup.to_str().ok_or(GuardianError::Rejected)?
        {
            return Err(GuardianError::Rejected);
        }
        let owner_pid = record["owner_pid"]
            .as_u64()
            .ok_or(GuardianError::Rejected)?;
        let owner_ticks = record["owner_start_ticks"]
            .as_str()
            .ok_or(GuardianError::Rejected)?;
        if !process_gone(owner_pid, owner_ticks)? {
            return Err(GuardianError::Rejected);
        }
        let snapshot = inspect(&self.id)?;
        if !spec.matches(&snapshot) || snapshot["State"]["StartedAt"] != observation["started_at"] {
            return Err(GuardianError::Rejected);
        }
        if snapshot["State"]["Running"] == true {
            let current = kernel_observation(
                snapshot["State"]["Pid"]
                    .as_u64()
                    .ok_or(GuardianError::Rejected)?,
            )?;
            if !kernel.same_incarnation(&current) {
                return Err(GuardianError::Rejected);
            }
        }
        let cli_pid = observation["attached_cli_pid"]
            .as_u64()
            .ok_or(GuardianError::Rejected)?;
        let cli_ticks = observation["attached_cli_start_ticks"]
            .as_str()
            .ok_or(GuardianError::Rejected)?;
        let retirement_raw = private_record(&spec, "retirement-custody.json")?;
        if observation["retirement_custody_sha256"] != sha256(&retirement_raw) {
            return Err(GuardianError::Rejected);
        }
        let retained: Value =
            serde_json::from_slice(&retirement_raw).map_err(|_| GuardianError::Rejected)?;
        if retained != retirement_custody(&spec)? {
            return Err(GuardianError::Rejected);
        }
        let index = verify_staged_source(&spec, pins)?;
        if sha256(&serde_json::to_vec(&index["binding"]).map_err(|_| GuardianError::Rejected)?)
            != fence.binding_sha256
            || index["binding"]["execution_id"] != fence.execution_id
            || index["binding"]["attempt_id"] != fence.attempt_id
            || index["binding"]["session_id"] != fence.session_id
            || index["binding"]["runtime_incarnation_id"] != fence.runtime_incarnation_id
            || index["binding"]["supervisor_incarnation_id"] != fence.supervisor_incarnation_id
            || index["artifact"]["artifact_id"] != format!("sha256:{}", pins.archive_sha256)
        {
            return Err(GuardianError::Rejected);
        }
        // Exclusive durable cleanup intent prevents concurrent recovery/replay.
        // A failed or uncertain recovery is retained for inspection, not retried.
        exclusive_record(
            &spec,
            "closed-recovery-intent.json",
            &json!({
            "version":"isolated-closed-result-recovery/v1","session_id":fence.session_id,
            "owner_incarnation_id":fence.owner_incarnation_id,"original_owner_pid":owner_pid,
            "original_owner_start_ticks":owner_ticks,"recovery_pid":std::process::id()}),
        )?;
        let mut drained = self.drain()?;
        if drained["namespace_drained"] != true || drained["container_removed"] != true {
            return Err(GuardianError::CleanupRequired);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while !process_gone(cli_pid, cli_ticks)? {
            if Instant::now() >= deadline {
                return Err(GuardianError::CleanupRequired);
            }
            thread::sleep(Duration::from_millis(10));
        }
        drained["original_cli_reaped_observed"] = json!(true);
        if retained != retirement_custody(&spec)? {
            return Err(GuardianError::CleanupRequired);
        }
        verify_staged_source(&spec, pins)?;
        let root = spec.bundle.parent().ok_or(GuardianError::Rejected)?;
        fs::remove_file(spec.material.join("sdk.pipe"))
            .map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_dir(&spec.material).map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_dir(root.join("material")).map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_file(root.join("gate/gate.sock")).map_err(|_| GuardianError::CleanupRequired)?;
        fs::remove_dir(root.join("gate")).map_err(|_| GuardianError::CleanupRequired)?;
        fs::set_permissions(&spec.bundle, fs::Permissions::from_mode(0o700))
            .map_err(|_| GuardianError::CleanupRequired)?;
        for name in NAMES.iter().chain(std::iter::once(&"session-bundle.json")) {
            fs::remove_file(spec.bundle.join(name)).map_err(|_| GuardianError::CleanupRequired)?;
        }
        fs::remove_dir(&spec.bundle).map_err(|_| GuardianError::CleanupRequired)?;
        Ok(RetiredSession {
            fence,
            observation: json!({
            "version":"bifrost.runtime-resource-settlement/v1","physical_drain":drained,
            "recovery_kind":"closed-result-original-owner-loss/v1","original_owner_gone":true,
            "staged_source_removed":true,"material_retired":true,"sdk_gate_retired":true,
            "index_sha256":spec.index_sha256,"archive_sha256":pins.archive_sha256}),
        })
    }

    pub fn create(spec: LaunchSpec) -> Result<Self, GuardianError> {
        if !spec.valid(true) {
            return Err(GuardianError::Rejected);
        }
        // Journal is exclusive and durable before contacting the daemon. The
        // same intent cannot be re-created after an uncertain outcome/crash.
        let raw = serde_json::to_vec(&spec.intent()).map_err(|_| GuardianError::Rejected)?;
        let mut journal = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(spec.journal.join("launch-intent.json"))
            .map_err(|_| GuardianError::Rejected)?;
        if journal
            .metadata()
            .map_err(|_| GuardianError::Rejected)?
            .uid()
            != spec.uid
        {
            return Err(GuardianError::Rejected);
        }
        journal
            .write_all(&raw)
            .and_then(|_| journal.sync_all())
            .map_err(|_| GuardianError::Uncertain)?;
        File::open(&spec.journal)
            .and_then(|dir| dir.sync_all())
            .map_err(|_| GuardianError::Uncertain)?;
        let existing = docker(&args(&[
            "container",
            "ls",
            "-aq",
            "--no-trunc",
            "--filter",
            &format!("name=^/{}$", spec.name()),
        ]))?;
        if !existing.is_empty() {
            return Err(GuardianError::Rejected);
        }
        let mut command = args(&[
            "container",
            "create",
            "--pull=never",
            "--name",
            &spec.name(),
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--ipc",
            "private",
            "--user",
            &spec.user(),
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--label",
            &format!("bifrost.isolated.guardian.nonce={}", spec.nonce),
            "--label",
            &format!("bifrost.isolated.guardian.session={}", spec.session_id),
        ]);
        for (source, destination) in spec.mounts() {
            command.push("--mount".into());
            command.push(format!(
                "type=bind,src={},dst={destination},readonly",
                source.display()
            ));
        }
        command.extend([
            spec.image_id.clone(),
            "--index-sha256".into(),
            spec.index_sha256.clone(),
        ]);
        if spec.sdk_socket_directory.is_some() {
            command.push("--sdk-relay".into());
        }
        let raw = docker(&command)?;
        let id = std::str::from_utf8(&raw)
            .map_err(|_| GuardianError::Uncertain)?
            .trim()
            .to_owned();
        if !digest(&id) {
            return Err(GuardianError::Uncertain);
        }
        let mut journal = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(spec.journal.join("container-id"))
            .map_err(|_| GuardianError::Uncertain)?;
        journal
            .write_all(id.as_bytes())
            .and_then(|_| journal.sync_all())
            .map_err(|_| GuardianError::Uncertain)?;
        Ok(Self {
            spec,
            id,
            may_start: true,
            attachment: None,
            kernel: None,
            custody_sha256: None,
            offered_runtime_id: None,
        })
    }

    /// Observe original, authenticated intent solely to terminate/settle it.
    /// No start method is released on a recovered instance.
    pub fn recover_for_drain(spec: LaunchSpec) -> Result<Self, GuardianError> {
        // A gateway may have lost/unlinked its socket before recovery. Original
        // intent/config/kernel custody still permits only physical stop/drain;
        // absence must not authorize another launch or SDK connection.
        if !spec.valid(false) {
            return Err(GuardianError::Rejected);
        }
        let raw = private_record(&spec, "launch-intent.json")?;
        let retained: Value = serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)?;
        if retained != spec.intent() {
            return Err(GuardianError::Rejected);
        }
        let snapshot = inspect(&spec.name())?;
        if !spec.matches(&snapshot) {
            return Err(GuardianError::Rejected);
        }
        let id = snapshot["Id"]
            .as_str()
            .filter(|s| digest(s))
            .ok_or(GuardianError::Rejected)?
            .to_owned();
        let retained = retained_kernel(&spec, &id)?;
        let kernel = if let Some(old) = retained {
            // Stop-only recovery must tolerate the original process exiting
            // between Docker observation and /proc read. Never recapture a new
            // incarnation or turn absence into a start permit.
            if snapshot["State"]["Running"] == true
                && snapshot["State"]["Pid"].as_u64() != Some(old.pid)
            {
                return Err(GuardianError::Rejected);
            }
            Some(old)
        } else if snapshot["State"]["Running"] == true {
            let current = kernel_observation(
                snapshot["State"]["Pid"]
                    .as_u64()
                    .ok_or(GuardianError::Rejected)?,
            )?;
            Some(current)
        } else {
            None
        };
        Ok(Self {
            spec,
            id,
            may_start: false,
            attachment: None,
            kernel,
            custody_sha256: None,
            offered_runtime_id: None,
        })
    }

    /// Start the inert first-party adapter once, before admission/preparation.
    /// No tenant Start, provision or private material is sent here.
    pub fn attach_dormant(&mut self) -> Result<(), GuardianError> {
        if !self.may_start {
            return Err(GuardianError::Rejected);
        }
        self.may_start = false; // consume before any possible daemon effect
        let snapshot = inspect(&self.id)?;
        if !self.spec.matches(&snapshot)
            || snapshot["State"]["Running"] != false
            || snapshot["State"]["Status"] != "created"
        {
            return Err(GuardianError::Rejected);
        }
        let mut child = docker_command()
            .args(["container", "start", "--attach", "--interactive", &self.id])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| GuardianError::Uncertain)?;
        let Some(original) = child.stdin.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(GuardianError::Uncertain);
        };
        // Reopen this exact still-owned pipe descriptor nonblocking, without
        // FFI/unsafe or any tenant-controlled pathname. CLOEXEC stays enabled.
        let reopened = OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(format!("/proc/self/fd/{}", original.as_raw_fd()));
        drop(original);
        let Ok(input) = reopened else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(GuardianError::Uncertain);
        };
        let Some(mut stdout) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err(GuardianError::Uncertain);
        };
        let (send, output) = mpsc::sync_channel(1);
        let reader = thread::spawn(move || {
            let Ok(codec) = Codec::new() else {
                return;
            };
            loop {
                let result = receive(&mut stdout, &codec);
                let finished = !matches!(result, Ok(Some(_)));
                if send.send(result).is_err() || finished {
                    break;
                }
            }
        });
        self.attachment = Some(Attachment {
            child,
            input: Some(input),
            output: Some(output),
            reader: Some(reader),
        });
        Ok(())
    }

    pub fn receive(&self, timeout: Duration) -> Result<ReceivedFrame, GuardianError> {
        self.attachment
            .as_ref()
            .ok_or(GuardianError::Rejected)?
            .output
            .as_ref()
            .ok_or(GuardianError::Rejected)?
            .recv_timeout(timeout)
            .map_err(|_| GuardianError::Uncertain)??
            .ok_or(GuardianError::Uncertain)
    }

    /// Nonblocking original-channel read for the single owning actor. Empty is
    /// a live wait only; EOF, reader loss and invalid bytes remain failures.
    pub fn receive_if_ready(&self) -> Result<Option<ReceivedFrame>, GuardianError> {
        let output = self
            .attachment
            .as_ref()
            .ok_or(GuardianError::Rejected)?
            .output
            .as_ref()
            .ok_or(GuardianError::Rejected)?;
        match output.try_recv() {
            Ok(Ok(Some(frame))) => Ok(Some(frame)),
            Err(std::sync::mpsc::TryRecvError::Empty) => Ok(None),
            _ => Err(GuardianError::Uncertain),
        }
    }

    /// Trusted coordinator only: legal common protocol state and committed owner
    /// decisions must precede any outbound frame. This transport grants neither.
    pub fn send(&mut self, frame: &Value) -> Result<(), GuardianError> {
        let attachment = self.attachment.as_mut().ok_or(GuardianError::Rejected)?;
        let input = attachment.input.as_mut().ok_or(GuardianError::Rejected)?;
        if Codec::new()
            .and_then(|codec| codec.write(input, frame))
            .is_err()
        {
            attachment.input.take(); // partial/error poisons the channel, no replay
            return Err(GuardianError::Uncertain);
        }
        Ok(())
    }

    pub fn observe_live(&mut self, offer: &ReceivedFrame) -> Result<Value, GuardianError> {
        let attachment = self.attachment.as_mut().ok_or(GuardianError::Rejected)?;
        if attachment
            .child
            .try_wait()
            .map_err(|_| GuardianError::Uncertain)?
            .is_some()
            || offer.frame["type"] != "Offer"
            || offer.frame["session_id"] != self.spec.session_id
        {
            return Err(GuardianError::Rejected);
        }
        let snapshot = inspect(&self.id)?;
        if !self.spec.matches(&snapshot) || snapshot["State"]["Running"] != true {
            return Err(GuardianError::Rejected);
        }
        let kernel = kernel_observation(
            snapshot["State"]["Pid"]
                .as_u64()
                .ok_or(GuardianError::Rejected)?,
        )?;
        if self.kernel.is_some() {
            return Err(GuardianError::Rejected);
        }
        retain_kernel(&self.spec, &self.id, &kernel)?;
        // Optional only for dormant physical probes. Actual admitted source
        // retirement is journaled by the original owner before this observation
        // and cryptographically bound to the SQL-retained custody digest.
        let retirement_custody_sha256 =
            if self.spec.journal.join("retirement-custody.json").exists() {
                Some(sha256(&private_record(
                    &self.spec,
                    "retirement-custody.json",
                )?))
            } else {
                None
            };
        let evidence = json!({"version":"isolated-guardian-custody/v1", "session_id":self.spec.session_id,
            "owner_pid":std::process::id(),"owner_start_ticks":start_ticks(u64::from(std::process::id()))?,
            "retirement_custody_sha256":retirement_custody_sha256,
            "nonce":self.spec.nonce, "container_id":self.id, "image_id":self.spec.image_id,
            "attached_cli_pid":attachment.child.id(), "attached_cli_start_ticks":start_ticks(u64::from(attachment.child.id()))?,
            "init_pid":kernel.pid, "init_start_ticks":kernel.start_ticks,
            "cgroup":kernel.cgroup, "started_at":snapshot["State"]["StartedAt"],
            "offer_sha256":sha256(&offer.payload), "index_sha256":self.spec.index_sha256,
            "mounts":snapshot["Mounts"], "network":"none"});
        self.kernel = Some(kernel);
        let raw = serde_json::to_vec(&evidence).map_err(|_| GuardianError::Rejected)?;
        retain_custody(&self.spec, &raw)?;
        let custody_sha256 = sha256(&raw);
        self.custody_sha256 = Some(custody_sha256.clone());
        self.offered_runtime_id = offer.frame["body"]["runtime_incarnation_id"]
            .as_str()
            .map(str::to_owned);
        Ok(json!({"observation":evidence,"channel_custody_sha256":custody_sha256}))
    }

    /// Private coordinator/SDK ingress precondition. The fence must name this
    /// original observed process/channel and the pinned neutral bundle binding.
    /// A recovery object cannot manufacture fresh custody from its journal.
    pub fn verify_material(
        &mut self,
        material: &crate::material::MaterialPipe,
    ) -> Result<(), GuardianError> {
        self.verify_live()?;
        material
            .verify_for(&self.spec.material, self.spec.uid)
            .map_err(|_| GuardianError::Rejected)
    }

    pub fn verify_session(&mut self, fence: &SessionFence) -> Result<Value, GuardianError> {
        let binding = self.verify_retained_session(fence)?;
        self.verify_live()?;
        Ok(binding)
    }

    // Deliberately separate original custody from liveness: a completed adapter
    // may have exited before drain. Recovery has neither original channel nor
    // offered incarnation and cannot pass this retained-session check.
    fn verify_retained_session(&self, fence: &SessionFence) -> Result<Value, GuardianError> {
        if !fence.valid()
            || fence.session_id != self.spec.session_id
            || self.custody_sha256.as_deref() != Some(fence.channel_custody_sha256.as_str())
            || self.offered_runtime_id.as_deref() != Some(fence.runtime_incarnation_id.as_str())
        {
            return Err(GuardianError::Rejected);
        }
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(self.spec.bundle.join("session-bundle.json"))
            .map_err(|_| GuardianError::Rejected)?;
        let metadata = file.metadata().map_err(|_| GuardianError::Rejected)?;
        if !metadata.is_file()
            || metadata.uid() != self.spec.uid
            || metadata.mode() & 0o777 != 0o400
        {
            return Err(GuardianError::Rejected);
        }
        let mut raw = Vec::new();
        file.take(65537)
            .read_to_end(&mut raw)
            .map_err(|_| GuardianError::Rejected)?;
        if raw.len() > 65536 || sha256(&raw) != self.spec.index_sha256 {
            return Err(GuardianError::Rejected);
        }
        let index: Value = serde_json::from_slice(&raw).map_err(|_| GuardianError::Rejected)?;
        let binding = &index["binding"];
        for (field, expected) in [
            ("execution_id", &fence.execution_id),
            ("attempt_id", &fence.attempt_id),
            ("session_id", &fence.session_id),
            (
                "supervisor_incarnation_id",
                &fence.supervisor_incarnation_id,
            ),
            ("runtime_incarnation_id", &fence.runtime_incarnation_id),
        ] {
            if binding[field] != *expected {
                return Err(GuardianError::Rejected);
            }
        }
        Ok(binding.clone())
    }

    /// Consume the original guardian and return an opaque physical observation
    /// only after kernel descendants, container and original channel are drained.
    /// This cannot authorize admission, spawn, renewal or lifecycle projection.
    pub fn drain_session(self, fence: &SessionFence) -> Result<DrainedSession, GuardianError> {
        if self.attachment.is_none() || self.kernel.is_none() {
            return Err(GuardianError::Rejected);
        }
        self.verify_retained_session(fence)?;
        let self_bundle = self.spec.bundle.clone();
        let self_material = self.spec.material.clone();
        let self_uid = self.spec.uid;
        let self_index = self.spec.index_sha256.clone();
        let observation = self.drain()?;
        if observation["namespace_drained"] != true
            || observation["container_removed"] != true
            || observation["attached_cli_waited"] != true
        {
            return Err(GuardianError::CleanupRequired);
        }
        Ok(DrainedSession {
            fence: fence.clone(),
            observation,
            bundle: self_bundle,
            material: self_material,
            uid: self_uid,
            index_sha256: self_index,
        })
    }

    /// Recheck this owned channel and original kernel incarnation immediately
    /// before owner admission/issuance or a restricted SDK request. A retained
    /// custody digest alone is insufficient. This is a physical precondition,
    /// not authorization, source eligibility or an atomic database/OS guarantee.
    /// Recovery and poisoned output channels cannot pass this check.
    pub fn verify_live(&mut self) -> Result<(), GuardianError> {
        let kernel = self.kernel.as_ref().ok_or(GuardianError::Rejected)?;
        let attachment = self.attachment.as_mut().ok_or(GuardianError::Rejected)?;
        if attachment.input.is_none()
            || attachment.output.is_none()
            || attachment
                .reader
                .as_ref()
                .is_none_or(|reader| reader.is_finished())
            || attachment
                .child
                .try_wait()
                .map_err(|_| GuardianError::Uncertain)?
                .is_some()
        {
            return Err(GuardianError::Rejected);
        }
        let snapshot = inspect(&self.id)?;
        if !self.spec.matches(&snapshot) || snapshot["State"]["Running"] != true {
            return Err(GuardianError::Rejected);
        }
        let current = kernel_observation(
            snapshot["State"]["Pid"]
                .as_u64()
                .ok_or(GuardianError::Rejected)?,
        )?;
        if !kernel.same_incarnation(&current) {
            return Err(GuardianError::Rejected);
        }
        Ok(())
    }

    /// Physical drain only, never lifecycle finalization or permission to replay.
    pub fn drain(mut self) -> Result<Value, GuardianError> {
        let snapshot = inspect(&self.id)?;
        if !self.spec.matches(&snapshot) {
            return Err(GuardianError::CleanupRequired);
        }
        if snapshot["State"]["Running"] == true && self.kernel.is_none() {
            self.kernel = Some(kernel_observation(
                snapshot["State"]["Pid"]
                    .as_u64()
                    .ok_or(GuardianError::Rejected)?,
            )?);
        }
        if let Some(attachment) = self.attachment.as_mut() {
            attachment.input.take();
        }
        docker(&args(&["container", "stop", "--time", "1", &self.id]))?;
        let stopped = inspect(&self.id)?;
        if stopped["State"]["Running"] != false || stopped["State"]["Pid"] != 0 {
            return Err(GuardianError::CleanupRequired);
        }
        if let Some(kernel) = &self.kernel {
            match fs::read_to_string(format!("/proc/{}/stat", kernel.pid)) {
                Ok(text)
                    if text
                        .rsplit_once(") ")
                        .and_then(|(_, tail)| tail.split_whitespace().nth(19))
                        == Some(kernel.start_ticks.as_str()) =>
                {
                    return Err(GuardianError::CleanupRequired);
                }
                Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
                    return Err(GuardianError::CleanupRequired);
                }
                _ => {}
            }
            match fs::read_to_string(kernel.cgroup.join("cgroup.procs")) {
                Ok(pids) if !pids.trim().is_empty() => return Err(GuardianError::CleanupRequired),
                Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
                    return Err(GuardianError::CleanupRequired);
                }
                _ => {}
            }
        }
        docker(&args(&["container", "rm", &self.id]))?;
        let remaining = docker(&args(&[
            "container",
            "ls",
            "-aq",
            "--no-trunc",
            "--filter",
            &format!("id={}", self.id),
        ]))?;
        if !remaining.is_empty() {
            return Err(GuardianError::CleanupRequired);
        }
        let attached_cli_waited = self.attachment.is_some();
        if let Some(mut attachment) = self.attachment.take() {
            let _ = attachment.child.kill();
            attachment
                .child
                .wait()
                .map_err(|_| GuardianError::CleanupRequired)?;
            attachment.output.take(); // unblock a bounded sender before joining
            if let Some(reader) = attachment.reader.take() {
                reader.join().map_err(|_| GuardianError::CleanupRequired)?;
            }
        }
        let namespace_drained =
            self.kernel.is_some() || stopped["State"]["StartedAt"] == "0001-01-01T00:00:00Z";
        Ok(
            json!({"container_id":self.id,"namespace_drained":namespace_drained,"container_removed":true,
            "attached_cli_waited":attached_cli_waited,
            "source_consumer_settlement":false,"lifecycle_finalization":false,"runtime_acceptance":false}),
        )
    }
}
