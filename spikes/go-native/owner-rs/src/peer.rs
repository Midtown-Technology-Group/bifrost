//! Trusted coordinator IPC peer identity; never a runtime wire field or grant.
//! Original PID/UID/ticks come from parent process custody, not tenant JSON.
use std::{
    fs::OpenOptions,
    io::Read,
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt},
        net::UnixStream,
    },
};

#[derive(Debug, PartialEq, Eq)]
pub enum PeerError {
    Rejected,
}

/// Process-local pin. No serialization, cloning, reconnect or recovery policy.
/// Matching this peer is necessary for IPC, not sufficient signing authority.
pub struct OriginalPeer {
    pid: u32,
    uid: u32,
    start_ticks: String,
}

impl OriginalPeer {
    pub fn uid(&self) -> u32 {
        self.uid
    }
    pub fn pin(pid: u32, uid: u32, start_ticks: &str) -> Result<Self, PeerError> {
        if pid == 0
            || uid == 0
            || start_ticks.is_empty()
            || !start_ticks.bytes().all(|byte| byte.is_ascii_digit())
            || process_ticks(pid, uid)? != start_ticks
        {
            return Err(PeerError::Rejected);
        }
        Ok(Self {
            pid,
            uid,
            start_ticks: start_ticks.into(),
        })
    }

    /// Check the kernel-connected peer and original process incarnation on
    /// every exchange. An invisible/changed process or namespace denies.
    /// The safe OS wrapper preserves this crate's unsafe_code=forbid gate.
    pub fn verify(&self, stream: &UnixStream) -> Result<(), PeerError> {
        let credentials =
            rustix::net::sockopt::socket_peercred(stream).map_err(|_| PeerError::Rejected)?;
        if credentials.pid.as_raw_pid()
            != i32::try_from(self.pid).map_err(|_| PeerError::Rejected)?
            || credentials.uid.as_raw() != self.uid
            || process_ticks(self.pid, self.uid)? != self.start_ticks
        {
            return Err(PeerError::Rejected);
        }
        Ok(())
    }
}

fn process_ticks(pid: u32, uid: u32) -> Result<String, PeerError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(format!("/proc/{pid}/stat"))
        .map_err(|_| PeerError::Rejected)?;
    if file.metadata().map_err(|_| PeerError::Rejected)?.uid() != uid {
        return Err(PeerError::Rejected);
    }
    let mut raw = String::new();
    file.take(4097)
        .read_to_string(&mut raw)
        .map_err(|_| PeerError::Rejected)?;
    if raw.len() > 4096 {
        return Err(PeerError::Rejected);
    }
    let (_, fields) = raw.rsplit_once(") ").ok_or(PeerError::Rejected)?;
    let ticks = fields
        .split_ascii_whitespace()
        .nth(19)
        .ok_or(PeerError::Rejected)?;
    if ticks.is_empty() || !ticks.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(PeerError::Rejected);
    }
    Ok(ticks.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn actual_socket_peer_requires_original_pid_uid_and_start_ticks()
    -> Result<(), Box<dyn std::error::Error>> {
        let pid = std::process::id();
        let uid = fs::metadata(format!("/proc/{pid}"))?.uid();
        let (left, right) = UnixStream::pair()?;
        assert_ne!(uid, 0, "peer proof requires the nonroot isolated Rust lane");
        assert!(OriginalPeer::pin(pid, 0, "1").is_err());
        let ticks = process_ticks(pid, uid).map_err(|_| "missing test process")?;
        let peer = OriginalPeer::pin(pid, uid, &ticks).map_err(|_| "cannot pin test process")?;
        assert_eq!(peer.verify(&left), Ok(()));
        assert_eq!(peer.verify(&right), Ok(()));
        assert!(OriginalPeer::pin(pid, uid, "0").is_err());
        assert!(OriginalPeer::pin(pid, uid, "not ticks").is_err());
        assert!(OriginalPeer::pin(0, uid, &ticks).is_err());
        let changed_pid = OriginalPeer {
            pid: pid.saturating_add(1),
            uid,
            start_ticks: ticks.clone(),
        };
        let changed_uid = OriginalPeer {
            pid,
            uid: uid.saturating_add(1),
            start_ticks: ticks,
        };
        let changed_ticks = OriginalPeer {
            pid,
            uid,
            start_ticks: "0".into(),
        };
        assert_eq!(changed_pid.verify(&left), Err(PeerError::Rejected));
        assert_eq!(changed_uid.verify(&left), Err(PeerError::Rejected));
        assert_eq!(changed_ticks.verify(&left), Err(PeerError::Rejected));
        Ok(())
    }
}
