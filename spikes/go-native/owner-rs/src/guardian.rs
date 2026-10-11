//! Private Linux guardian custody. No lifecycle/SDK authority or production dispatch.
//! The initial live lane permits only a dormant, network-none carrier. A retained
//! intent permits stop-only recovery, never another start or tenant initialization.
use crate::{archive::sha256, canonical_uuid, digest};
use bifrost_execution_wire_spike::{Codec, MAX_FRAME};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::AsRawFd,
        unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt},
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
        };
        assert_eq!(guardian.attach_dormant(), Err(GuardianError::Rejected));
        assert_eq!(guardian.verify_live(), Err(GuardianError::Rejected));
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
    fn valid(&self) -> bool {
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
                            items.len() == 1 && items[0].file_name() == "ingress.sock"
                        })
                    })
                    && fs::symlink_metadata(directory.join("ingress.sock")).is_ok_and(|m| {
                        m.file_type().is_socket()
                            && m.uid() == self.uid
                            && m.mode() & 0o777 == 0o600
                    })
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
    if !meta.is_file() || meta.uid() != spec.uid || meta.mode() & 0o777 != 0o600 {
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
    pub fn create(spec: LaunchSpec) -> Result<Self, GuardianError> {
        if !spec.valid() {
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
        })
    }

    /// Observe original, authenticated intent solely to terminate/settle it.
    /// No start method is released on a recovered instance.
    pub fn recover_for_drain(spec: LaunchSpec) -> Result<Self, GuardianError> {
        if !spec.valid() {
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
        let evidence = json!({"version":"isolated-guardian-custody/v1", "session_id":self.spec.session_id,
            "nonce":self.spec.nonce, "container_id":self.id, "image_id":self.spec.image_id,
            "attached_cli_pid":attachment.child.id(), "attached_cli_start_ticks":start_ticks(u64::from(attachment.child.id()))?,
            "init_pid":kernel.pid, "init_start_ticks":kernel.start_ticks,
            "cgroup":kernel.cgroup, "started_at":snapshot["State"]["StartedAt"],
            "offer_sha256":sha256(&offer.payload), "index_sha256":self.spec.index_sha256,
            "mounts":snapshot["Mounts"], "network":"none"});
        self.kernel = Some(kernel);
        let raw = serde_json::to_vec(&evidence).map_err(|_| GuardianError::Rejected)?;
        Ok(json!({"observation":evidence,"channel_custody_sha256":sha256(&raw)}))
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
