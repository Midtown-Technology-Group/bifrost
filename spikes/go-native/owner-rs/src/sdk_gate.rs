//! Private, read-only bridge from verified SDK ingress to the owning guardian.
//! No JWT/signing material, lifecycle command or production route is exposed.
use crate::{
    IntegrationGetRequest, SessionFence, archive::sha256, authorize_integration_get_candidate,
    canonical_uuid, digest, guardian::Guardian,
};
use serde_json::{Value, json};
use sqlx::{PgPool, Row};
use std::{
    fs,
    io::{Read, Write},
    os::unix::{
        ffi::OsStrExt,
        fs::{FileTypeExt, MetadataExt, PermissionsExt},
        net::{UnixListener, UnixStream},
    },
    path::{Path, PathBuf},
    time::Duration,
};

const MAX_REQUEST: usize = 4096;
const MAX_CONNECTIONS: usize = 16;

#[derive(Debug, PartialEq, Eq)]
pub enum GateError {
    Rejected,
    Uncertain,
    CleanupRequired,
}

/// Owned by the same trusted actor as Guardian. The path is never mounted into
/// the runtime; only the separate, authenticated restricted ingress can reach
/// it. A response is bound to one connection/request and is not a bearer token.
pub struct SDKGate {
    listener: Option<UnixListener>,
    path: PathBuf,
    device: u64,
    inode: u64,
    uid: u32,
    connections: usize,
}

fn request(raw: &[u8]) -> Result<IntegrationGetRequest, GateError> {
    if raw.is_empty() || raw.len() > MAX_REQUEST {
        return Err(GateError::Rejected);
    }
    let value: Value = serde_json::from_slice(raw).map_err(|_| GateError::Rejected)?;
    let fields = value
        .as_object()
        .filter(|o| o.len() == 5)
        .ok_or(GateError::Rejected)?;
    // Independently specified private IPC uses sorted compact UTF-8 JSON. This
    // exact-byte check also rejects duplicate keys and ambiguous normalization.
    if serde_json::to_vec(&value).map_err(|_| GateError::Rejected)? != raw {
        return Err(GateError::Rejected);
    }
    let text = |name: &str| {
        fields
            .get(name)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or(GateError::Rejected)
    };
    let request = IntegrationGetRequest {
        grant_id: text("grant_id")?,
        grant_digest: text("grant_digest")?,
        integration_name: text("integration_name")?,
        organization_id: text("organization_id")?,
        solution_id: text("solution_id")?,
    };
    if !canonical_uuid(&request.grant_id)
        || !digest(&request.grant_digest)
        || !canonical_uuid(&request.organization_id)
        || !canonical_uuid(&request.solution_id)
        || request.integration_name.is_empty()
        || request.integration_name.len() > 255
        || request.integration_name.contains('\0')
        || request.integration_name.trim() != request.integration_name
    {
        return Err(GateError::Rejected);
    }
    Ok(request)
}

fn read_request(stream: &mut UnixStream) -> Result<Vec<u8>, GateError> {
    let mut prefix = [0; 4];
    stream
        .read_exact(&mut prefix)
        .map_err(|_| GateError::Rejected)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if size == 0 || size > MAX_REQUEST {
        return Err(GateError::Rejected);
    }
    let mut raw = vec![0; size];
    stream
        .read_exact(&mut raw)
        .map_err(|_| GateError::Rejected)?;
    // Sender must half-close its write side. Additional/truncated requests can
    // never become an admitted intent or a second command on this connection.
    let mut extra = [0];
    if stream.read(&mut extra).map_err(|_| GateError::Rejected)? != 0 {
        return Err(GateError::Rejected);
    }
    Ok(raw)
}

impl SDKGate {
    pub fn create(directory: &Path, uid: u32) -> Result<Self, GateError> {
        let metadata = fs::symlink_metadata(directory).map_err(|_| GateError::Rejected)?;
        if uid == 0
            || !metadata.is_dir()
            || metadata.uid() != uid
            || metadata.mode() & 0o777 != 0o700
            || directory.canonicalize().map_err(|_| GateError::Rejected)? != directory
        {
            return Err(GateError::Rejected);
        }
        if fs::read_dir(directory)
            .map_err(|_| GateError::Rejected)?
            .next()
            .is_some()
        {
            return Err(GateError::Rejected);
        }
        let path = directory.join("gate.sock");
        if path.as_os_str().as_bytes().len() >= 108 {
            return Err(GateError::Rejected);
        }
        let listener = UnixListener::bind(&path).map_err(|_| GateError::Uncertain)?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))
            .map_err(|_| GateError::Uncertain)?;
        let metadata = fs::symlink_metadata(&path).map_err(|_| GateError::Uncertain)?;
        if !metadata.file_type().is_socket()
            || metadata.uid() != uid
            || metadata.mode() & 0o777 != 0o600
        {
            return Err(GateError::Uncertain);
        }
        listener
            .set_nonblocking(true)
            .map_err(|_| GateError::Uncertain)?;
        Ok(Self {
            listener: Some(listener),
            path,
            device: metadata.dev(),
            inode: metadata.ino(),
            uid,
            connections: 0,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Poll one bounded ingress connection while serializing with this owned
    /// guardian. Ingress must already verify finite JWT and all preimages. The
    /// fenced session is coordinator state, never supplied by the requester.
    pub async fn serve_one_if_ready(
        &mut self,
        guardian: &mut Guardian,
        pool: &PgPool,
        fence: &SessionFence,
    ) -> Result<Option<bool>, GateError> {
        if self.connections >= MAX_CONNECTIONS {
            return Err(GateError::Rejected);
        }
        let (mut stream, _) = match self.listener.as_ref().ok_or(GateError::Rejected)?.accept() {
            Ok(connection) => connection,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => return Ok(None),
            Err(_) => return Err(GateError::Uncertain),
        };
        self.connections += 1; // consumed before any decode/SQL; never replay
        stream
            .set_read_timeout(Some(Duration::from_secs(1)))
            .map_err(|_| GateError::Uncertain)?;
        stream
            .set_write_timeout(Some(Duration::from_secs(1)))
            .map_err(|_| GateError::Uncertain)?;
        let raw = match read_request(&mut stream) {
            Ok(raw) => raw,
            Err(_) => return Ok(Some(false)), // EOF/close, no positive response
        };
        let admitted = match request(&raw) {
            Ok(request) => Self::admit(guardian, pool, fence, &request).await,
            Err(_) => false,
        };
        let response =
            serde_json::to_vec(&json!({"admitted":admitted,"request_sha256":sha256(&raw)}))
                .map_err(|_| GateError::Uncertain)?;
        stream
            .write_all(&(response.len() as u32).to_be_bytes())
            .and_then(|_| stream.write_all(&response))
            .map_err(|_| GateError::Uncertain)?;
        Ok(Some(admitted))
    }

    async fn admit(
        guardian: &mut Guardian,
        pool: &PgPool,
        fence: &SessionFence,
        request: &IntegrationGetRequest,
    ) -> bool {
        let Ok(binding) = guardian.verify_session(fence) else {
            return false;
        };
        if binding["kind"] != "execution-binding/v1"
            || binding["execution_kind"] != "workflow"
            || binding["effective_scope"]["kind"] != "organization"
            || binding["effective_scope"]["organization_id"] != request.organization_id
            || binding["solution_id"] != request.solution_id
        {
            return false;
        }
        // Retained owner source fields are immutable. Match the actual pinned
        // bundle's neutral binding; these reads cannot assign/change an owner.
        let owner = sqlx::query(
            "SELECT artifact_id,deployment_id::text AS deployment, \
            d.solution_id::text AS solution FROM runtime_execution_owners o \
            JOIN solution_deployments d ON d.id=o.deployment_id \
            WHERE o.execution_id=$1::text::uuid AND o.owner_incarnation_id=$2::text::uuid",
        )
        .bind(&fence.execution_id)
        .bind(&fence.owner_incarnation_id)
        .fetch_optional(pool)
        .await;
        let Ok(Some(owner)) = owner else {
            return false;
        };
        for (column, field) in [
            ("artifact_id", "artifact_id"),
            ("deployment", "deployment_id"),
            ("solution", "solution_id"),
        ] {
            if owner.try_get::<String, _>(column).ok().as_deref() != binding[field].as_str() {
                return false;
            }
        }
        if authorize_integration_get_candidate(pool, fence, request)
            .await
            .is_err()
        {
            return false;
        }
        // A process/channel loss during SQL cannot qualify an external fetch.
        // This is not an atomic DB/OS claim or permission to restore custody.
        guardian.verify_session(fence).is_ok()
    }

    /// Parent closes ingress and proves physical/source drain independently.
    /// Remove only this original private gate, never a substituted pathname.
    pub fn retire(mut self) -> Result<(), GateError> {
        let metadata = fs::symlink_metadata(&self.path).map_err(|_| GateError::CleanupRequired)?;
        if !metadata.file_type().is_socket()
            || metadata.dev() != self.device
            || metadata.ino() != self.inode
            || metadata.uid() != self.uid
            || metadata.mode() & 0o777 != 0o600
        {
            return Err(GateError::CleanupRequired);
        }
        self.listener.take();
        fs::remove_file(&self.path).map_err(|_| GateError::CleanupRequired)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Shutdown;
    use std::thread;

    fn valid() -> Value {
        json!({"grant_id":"00000000-0000-0000-0000-000000000001","grant_digest":"a".repeat(64),
            "integration_name":"Fixture","organization_id":"00000000-0000-0000-0000-000000000002",
            "solution_id":"00000000-0000-0000-0000-000000000003"})
    }
    #[test]
    fn closed_private_intent_rejects_ambiguity_lifecycle_and_scope_drift() {
        let value = valid();
        let raw = serde_json::to_vec(&value).unwrap_or_default();
        assert!(request(&raw).is_ok());
        let mut unknown = value.clone();
        unknown["command"] = json!("Start");
        assert!(request(&serde_json::to_vec(&unknown).unwrap_or_default()).is_err());
        let mut nil = value.clone();
        nil["organization_id"] = Value::Null;
        assert!(request(&serde_json::to_vec(&nil).unwrap_or_default()).is_err());
        let duplicate = String::from_utf8(raw.clone()).unwrap_or_default().replacen(
            "{",
            "{\"grant_id\":\"discarded\",",
            1,
        );
        assert!(request(duplicate.as_bytes()).is_err());
        let mut whitespace = vec![b' '];
        whitespace.extend(raw);
        assert!(request(&whitespace).is_err());
        assert!(request(&vec![b'a'; MAX_REQUEST + 1]).is_err());
    }
    #[test]
    fn length_and_sender_eof_fence_one_private_request() {
        let payload = serde_json::to_vec(&valid()).unwrap_or_default();
        for extra in [false, true] {
            let (mut reader, mut writer) =
                UnixStream::pair().unwrap_or_else(|_| panic!("owned pair"));
            let raw = payload.clone();
            let sender = thread::spawn(move || {
                writer
                    .write_all(&(raw.len() as u32).to_be_bytes())
                    .unwrap_or_else(|_| panic!("prefix"));
                writer.write_all(&raw).unwrap_or_else(|_| panic!("request"));
                if extra {
                    writer
                        .write_all(b"second command")
                        .unwrap_or_else(|_| panic!("extra"));
                }
                writer
                    .shutdown(Shutdown::Write)
                    .unwrap_or_else(|_| panic!("EOF"));
            });
            assert_eq!(read_request(&mut reader).is_ok(), !extra);
            sender.join().unwrap_or_else(|_| panic!("sender"));
        }
    }
}
