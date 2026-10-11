//! Authenticated one-use finite issuer IPC, outside the public runtime contract.
//! Neither a credential response nor this connection is release authority.
use crate::{FiniteIssuanceAdmission, archive::sha256, peer::OriginalPeer};
use serde_json::{Value, json};
use std::{
    fs,
    io::{Read, Write},
    net::Shutdown,
    os::unix::{
        fs::{FileTypeExt, MetadataExt},
        net::UnixStream,
    },
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

#[derive(Debug, PartialEq, Eq)]
pub enum IssuerError {
    Rejected,
}

pub struct IssuerChannel {
    path: PathBuf,
    uid: u32,
    inode: (u64, u64),
    peer: OriginalPeer,
    ca_pem: String,
    attempted: bool,
}

/// No Debug/Clone/serialization: scoped opaque material is not portable authority.
pub struct IssuedMaterial {
    configuration: Value,
    expires_at: String,
    reference: crate::IntegrationGetRequest,
    operations_digest: String,
}
impl IssuedMaterial {
    pub(crate) fn reference(&self) -> &crate::IntegrationGetRequest {
        &self.reference
    }
    pub fn operations_digest(&self) -> &str {
        &self.operations_digest
    }
    pub fn configuration(&self) -> &Value {
        &self.configuration
    }
    pub fn expires_at(&self) -> &str {
        &self.expires_at
    }
    pub fn grant_id(&self) -> &str {
        &self.reference.grant_id
    }
}

impl IssuerChannel {
    /// The trusted parent supplies original process custody and accepted test CA.
    /// Nothing here accepts those values from an HTTP or runtime request.
    pub fn pin(
        path: &Path,
        uid: u32,
        peer: OriginalPeer,
        ca_pem: String,
    ) -> Result<Self, IssuerError> {
        if peer.uid() != uid || ca_pem.is_empty() || ca_pem.len() > 8192 {
            return Err(IssuerError::Rejected);
        }
        let inode = private_socket(path, uid)?;
        Ok(Self {
            path: path.into(),
            uid,
            inode,
            peer,
            ca_pem,
            attempted: false,
        })
    }

    pub fn issue_once(
        &mut self,
        admission: &FiniteIssuanceAdmission,
    ) -> Result<IssuedMaterial, IssuerError> {
        if self.attempted {
            return Err(IssuerError::Rejected);
        }
        self.attempted = true;
        if !crate::digest(admission.grant_digest())
            || private_socket(&self.path, self.uid)? != self.inode
        {
            return Err(IssuerError::Rejected);
        }
        let request = serde_json::to_vec(&json!({"snapshot":admission.snapshot(),
            "caller":admission.caller(),"grant_id":admission.snapshot()["id"],
            "grant_digest":admission.grant_digest(),"expires_at":admission.expires_at()}))
        .map_err(|_| IssuerError::Rejected)?;
        if request.is_empty() || request.len() > 8192 {
            return Err(IssuerError::Rejected);
        }
        // Nonblocking Unix connect fails closed if a backlog is full. No wait,
        // reconnect, retry or raw-FD conversion; OwnedFd transfer remains safe.
        let fd = rustix::net::socket_with(
            rustix::net::AddressFamily::UNIX,
            rustix::net::SocketType::STREAM,
            rustix::net::SocketFlags::CLOEXEC | rustix::net::SocketFlags::NONBLOCK,
            None,
        )
        .map_err(|_| IssuerError::Rejected)?;
        let address =
            rustix::net::SocketAddrUnix::new(&self.path).map_err(|_| IssuerError::Rejected)?;
        rustix::net::connect(&fd, &address).map_err(|_| IssuerError::Rejected)?;
        let mut stream = UnixStream::from(fd);
        stream
            .set_nonblocking(false)
            .map_err(|_| IssuerError::Rejected)?;
        let deadline = Instant::now() + Duration::from_secs(3);
        self.peer
            .verify(&stream)
            .map_err(|_| IssuerError::Rejected)?;
        if private_socket(&self.path, self.uid)? != self.inode {
            return Err(IssuerError::Rejected);
        }
        write_before(&mut stream, &(request.len() as u32).to_be_bytes(), deadline)?;
        write_before(&mut stream, &request, deadline)?;
        stream
            .shutdown(Shutdown::Write)
            .map_err(|_| IssuerError::Rejected)?;
        let mut size = [0; 4];
        read_before(&mut stream, &mut size, deadline)?;
        let size = u32::from_be_bytes(size) as usize;
        if size == 0 || size > 16384 {
            return Err(IssuerError::Rejected);
        }
        let mut response = vec![0; size];
        read_before(&mut stream, &mut response, deadline)?;
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(|_| IssuerError::Rejected)?;
        let mut extra = [0];
        if stream.read(&mut extra).map_err(|_| IssuerError::Rejected)? != 0 {
            return Err(IssuerError::Rejected);
        }
        self.peer
            .verify(&stream)
            .map_err(|_| IssuerError::Rejected)?;
        if private_socket(&self.path, self.uid)? != self.inode {
            return Err(IssuerError::Rejected);
        }
        let configuration = accepted_response(
            &response,
            &request,
            admission.snapshot(),
            admission.expires_at(),
            &self.ca_pem,
        )?;
        let snapshot = admission.snapshot();
        Ok(IssuedMaterial {
            configuration,
            expires_at: admission.expires_at().into(),
            reference: crate::IntegrationGetRequest {
                grant_id: snapshot["id"].as_str().ok_or(IssuerError::Rejected)?.into(),
                grant_digest: admission.grant_digest().into(),
                integration_name: admission.integration_name().into(),
                organization_id: snapshot["effective_organization_id"]
                    .as_str()
                    .ok_or(IssuerError::Rejected)?
                    .into(),
                solution_id: snapshot["solution_install_id"]
                    .as_str()
                    .ok_or(IssuerError::Rejected)?
                    .into(),
            },
            operations_digest: snapshot["operations_digest"]
                .as_str()
                .ok_or(IssuerError::Rejected)?
                .into(),
        })
    }
}

fn private_socket(path: &Path, uid: u32) -> Result<(u64, u64), IssuerError> {
    if uid == 0
        || !path.is_absolute()
        || path.as_os_str().len() >= 108
        || path.canonicalize().map_err(|_| IssuerError::Rejected)? != path
    {
        return Err(IssuerError::Rejected);
    }
    let parent = fs::symlink_metadata(path.parent().ok_or(IssuerError::Rejected)?)
        .map_err(|_| IssuerError::Rejected)?;
    let socket = fs::symlink_metadata(path).map_err(|_| IssuerError::Rejected)?;
    if !parent.is_dir()
        || parent.uid() != uid
        || parent.mode() & 0o777 != 0o700
        || !socket.file_type().is_socket()
        || socket.uid() != uid
        || socket.mode() & 0o777 != 0o600
    {
        return Err(IssuerError::Rejected);
    }
    Ok((socket.dev(), socket.ino()))
}

fn accepted_response(
    raw: &[u8],
    request: &[u8],
    snapshot: &Value,
    expires_at: &str,
    ca: &str,
) -> Result<Value, IssuerError> {
    let value: Value = serde_json::from_slice(raw).map_err(|_| IssuerError::Rejected)?;
    // Canonical byte equality also rejects duplicate keys/normalization/trailing bytes.
    if serde_json::to_vec(&value).map_err(|_| IssuerError::Rejected)? != raw
        || value.as_object().is_none_or(|v| v.len() != 3)
        || value["request_sha256"] != sha256(request)
        || value["expires_at"] != expires_at
    {
        return Err(IssuerError::Rejected);
    }
    let config = &value["sdk_configuration"];
    let bearer = config["bearer"].as_str().ok_or(IssuerError::Rejected)?;
    if config.as_object().is_none_or(|v| v.len() != 5)
        || config["endpoint"] != "https://127.0.0.1:8443"
        || config["organization_id"] != snapshot["effective_organization_id"]
        || config["solution_id"] != snapshot["solution_install_id"]
        || config["test_ca_pem"] != ca
        || bearer.is_empty()
        || bearer.len() > 4096
        || !bearer.bytes().all(|b| (33..=126).contains(&b))
    {
        return Err(IssuerError::Rejected);
    }
    Ok(config.clone())
}

fn remaining(deadline: Instant) -> Result<Duration, IssuerError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|d| !d.is_zero())
        .ok_or(IssuerError::Rejected)
}
fn read_before(
    stream: &mut UnixStream,
    mut bytes: &mut [u8],
    deadline: Instant,
) -> Result<(), IssuerError> {
    while !bytes.is_empty() {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(|_| IssuerError::Rejected)?;
        let n = stream.read(bytes).map_err(|_| IssuerError::Rejected)?;
        if n == 0 {
            return Err(IssuerError::Rejected);
        }
        bytes = &mut bytes[n..];
    }
    Ok(())
}
fn write_before(
    stream: &mut UnixStream,
    mut bytes: &[u8],
    deadline: Instant,
) -> Result<(), IssuerError> {
    while !bytes.is_empty() {
        stream
            .set_write_timeout(Some(remaining(deadline)?))
            .map_err(|_| IssuerError::Rejected)?;
        let n = stream.write(bytes).map_err(|_| IssuerError::Rejected)?;
        if n == 0 {
            return Err(IssuerError::Rejected);
        }
        bytes = &bytes[n..];
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn issuer_reply_cannot_rebind_expiry_scope_origin_ca_or_request()
    -> Result<(), Box<dyn std::error::Error>> {
        let request = b"actual request bytes";
        let snapshot = json!({"id":"grant","effective_organization_id":"org","solution_install_id":"solution"});
        let expires = "2026-10-11T00:00:00.000000Z";
        let response = json!({"request_sha256":sha256(request),"expires_at":expires,
            "sdk_configuration":{"endpoint":"https://127.0.0.1:8443","bearer":"opaque-finite-token",
            "organization_id":"org","solution_id":"solution","test_ca_pem":"accepted CA"}});
        assert!(
            accepted_response(
                &serde_json::to_vec(&response)?,
                request,
                &snapshot,
                expires,
                "accepted CA"
            )
            .is_ok()
        );
        for key in ["expires_at", "request_sha256"] {
            let mut changed = response.clone();
            changed[key] = json!("changed");
            assert!(
                accepted_response(
                    &serde_json::to_vec(&changed)?,
                    request,
                    &snapshot,
                    expires,
                    "accepted CA"
                )
                .is_err()
            );
        }
        for key in [
            "endpoint",
            "organization_id",
            "solution_id",
            "test_ca_pem",
            "bearer",
        ] {
            let mut changed = response.clone();
            changed["sdk_configuration"][key] = json!(if key == "bearer" {
                "invalid token"
            } else {
                "changed"
            });
            assert!(
                accepted_response(
                    &serde_json::to_vec(&changed)?,
                    request,
                    &snapshot,
                    expires,
                    "accepted CA"
                )
                .is_err()
            );
        }
        let raw = serde_json::to_vec(&response)?;
        let mut duplicate = b"{\"expires_at\":\"other\",".to_vec();
        duplicate.extend_from_slice(&raw[1..]);
        assert!(accepted_response(&duplicate, request, &snapshot, expires, "accepted CA").is_err());
        let mut unknown = response.clone();
        unknown["renewal"] = json!(true);
        assert!(
            accepted_response(
                &serde_json::to_vec(&unknown)?,
                request,
                &snapshot,
                expires,
                "accepted CA"
            )
            .is_err()
        );
        Ok(())
    }
}
