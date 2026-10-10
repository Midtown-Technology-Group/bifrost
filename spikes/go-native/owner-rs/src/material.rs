//! Private guardian transport, not admission, issuance or release authority.
//! The caller must establish live custody and newly committed release before
//! delivery. Retained rows, matching UUIDs and an open FIFO cannot do that.
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

pub const MAX_MATERIAL: usize = 65536;

#[derive(Debug, PartialEq, Eq)]
pub enum MaterialError {
    Rejected,
    /// Bytes may have reached the peer. No second attempt is permitted.
    UncertainDelivery,
    CleanupRequired,
}

/// No Clone/Debug implementation: this owns one writer and never formats bytes.
/// Drop closes the writer, but deliberately does not claim physical cleanup.
pub struct MaterialPipe {
    directory: PathBuf,
    fifo: PathBuf,
    writer: Option<File>,
    device: u64,
    inode: u64,
}

impl MaterialPipe {
    /// `parent` is a trusted guardian-owned private staging directory, outside
    /// every tenant mount. Creation is exclusive; existing identities deny.
    /// Only the returned `directory()` is mounted into this execution's carrier.
    pub fn create(parent: &Path, session: &str, owner_uid: u32) -> Result<Self, MaterialError> {
        if owner_uid == 0 || !crate::canonical_uuid(session) || !parent.is_absolute() {
            return Err(MaterialError::Rejected);
        }
        let metadata = fs::symlink_metadata(parent).map_err(|_| MaterialError::Rejected)?;
        if !metadata.is_dir()
            || metadata.uid() != owner_uid
            || metadata.mode() & 0o777 != 0o700
            || parent.canonicalize().map_err(|_| MaterialError::Rejected)? != parent
        {
            return Err(MaterialError::Rejected);
        }
        let directory = parent.join(session);
        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        builder
            .create(&directory)
            .map_err(|_| MaterialError::Rejected)?;
        let fifo = directory.join("sdk.pipe");
        // Fixed first-party tool, no shell, source execution or inherited env.
        // Any failed/uncertain creation retains its directory for inspection;
        // it is never retried or adopted as a fresh session.
        let status = Command::new("/usr/bin/mkfifo")
            .env_clear()
            .args(["--mode=0600", "--"])
            .arg(&fifo)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|_| MaterialError::CleanupRequired)?;
        if !status.success() {
            return Err(MaterialError::CleanupRequired);
        }
        // RDWR keeper prevents EOF while the inert adapter prepares. The
        // carrier separately opens a readonly FD3. No blocking open or write.
        let writer = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&fifo)
            .map_err(|_| MaterialError::CleanupRequired)?;
        let metadata = writer
            .metadata()
            .map_err(|_| MaterialError::CleanupRequired)?;
        if !metadata.file_type().is_fifo()
            || metadata.uid() != owner_uid
            || metadata.mode() & 0o777 != 0o600
        {
            return Err(MaterialError::CleanupRequired);
        }
        Ok(Self {
            directory,
            fifo,
            writer: Some(writer),
            device: metadata.dev(),
            inode: metadata.ino(),
        })
    }

    pub fn directory(&self) -> &Path {
        &self.directory
    }

    /// One bounded length-prefixed private envelope, then EOF. Consumes the
    /// writer before validation or I/O: every outcome forbids retransmission.
    /// This does not validate SDK authority or JSON; the issuer/guardian must.
    /// Nonblocking partial progress continues only at the unsent byte offset;
    /// zero/error/EAGAIN/EINTR is uncertain and requires drain, never replay.
    pub fn deliver(&mut self, payload: &[u8]) -> Result<(), MaterialError> {
        let mut writer = self.writer.take().ok_or(MaterialError::Rejected)?;
        write_once(&mut writer, payload)
        // File drops here, including on error, so a live peer observes EOF.
    }

    /// Close without delivering (e.g. cancellation before material).
    pub fn close(&mut self) {
        self.writer.take();
    }

    /// Call only after independent container/descendant drain and mount release.
    /// This proves local pathname removal, not kernel/source-consumer drain.
    /// Replaced files or unexpected directory contents deny cleanup.
    pub fn retire(mut self) -> Result<(), MaterialError> {
        self.close();
        let metadata =
            fs::symlink_metadata(&self.fifo).map_err(|_| MaterialError::CleanupRequired)?;
        if !metadata.file_type().is_fifo()
            || metadata.dev() != self.device
            || metadata.ino() != self.inode
        {
            return Err(MaterialError::CleanupRequired);
        }
        fs::remove_file(&self.fifo).map_err(|_| MaterialError::CleanupRequired)?;
        fs::remove_dir(&self.directory).map_err(|_| MaterialError::CleanupRequired)
    }
}

fn write_once(writer: &mut impl Write, payload: &[u8]) -> Result<(), MaterialError> {
    if payload.is_empty() || payload.len() > MAX_MATERIAL {
        return Err(MaterialError::Rejected);
    }
    let prefix = (payload.len() as u32).to_be_bytes();
    for part in [prefix.as_slice(), payload] {
        let mut pending = part;
        while !pending.is_empty() {
            match writer.write(pending) {
                Ok(n) if n > 0 && n <= pending.len() => pending = &pending[n..],
                _ => return Err(MaterialError::UncertainDelivery),
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{self, Read},
        os::unix::fs::{PermissionsExt, symlink},
        sync::atomic::{AtomicU64, Ordering},
    };

    static NEXT: AtomicU64 = AtomicU64::new(0);
    const SESSION: &str = "00000000-0000-0000-0000-000000000001";

    struct Fixture {
        parent: PathBuf,
        uid: u32,
    }
    impl Fixture {
        fn new() -> Self {
            let parent = std::env::temp_dir().join(format!(
                "guardian-material-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let mut builder = fs::DirBuilder::new();
            builder.mode(0o700);
            assert!(builder.create(&parent).is_ok());
            let metadata = fs::metadata(&parent).unwrap_or_else(|e| panic!("private fixture: {e}"));
            assert_ne!(
                metadata.uid(),
                0,
                "kernel evidence must run in the nonroot isolated lane"
            );
            Self {
                parent,
                uid: metadata.uid(),
            }
        }
        fn pipe(&self) -> MaterialPipe {
            MaterialPipe::create(&self.parent, SESSION, self.uid)
                .unwrap_or_else(|e| panic!("private FIFO: {e:?}"))
        }
        fn reader(&self, pipe: &MaterialPipe) -> File {
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW)
                .open(pipe.directory().join("sdk.pipe"))
                .unwrap_or_else(|e| panic!("peer FD: {e}"))
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            assert!(fs::remove_dir_all(&self.parent).is_ok());
        }
    }

    #[test]
    fn live_fifo_single_delivery_eof_and_exclusive_identity() {
        let fixture = Fixture::new();
        let mut pipe = fixture.pipe();
        assert!(matches!(
            MaterialPipe::create(&fixture.parent, SESSION, fixture.uid),
            Err(MaterialError::Rejected)
        ));
        let mut reader = fixture.reader(&pipe);
        let mut byte = [0];
        assert_eq!(
            reader.read(&mut byte).err().map(|e| e.kind()),
            Some(io::ErrorKind::WouldBlock)
        );
        assert_eq!(pipe.deliver(b"synthetic-private-envelope"), Ok(()));
        let mut received = Vec::new();
        assert!(reader.read_to_end(&mut received).is_ok());
        assert_eq!(&received[..4], &(26_u32.to_be_bytes()));
        assert_eq!(&received[4..], b"synthetic-private-envelope");
        assert_eq!(pipe.deliver(b"second"), Err(MaterialError::Rejected));
        drop(reader);
        assert_eq!(pipe.retire(), Ok(()));
        assert!(!fixture.parent.join(SESSION).exists());
    }

    #[test]
    fn cancelled_or_invalid_delivery_consumes_writer() {
        for invalid in [Vec::new(), vec![0; MAX_MATERIAL + 1]] {
            let fixture = Fixture::new();
            let mut pipe = fixture.pipe();
            let mut reader = fixture.reader(&pipe);
            assert_eq!(pipe.deliver(&invalid), Err(MaterialError::Rejected));
            assert_eq!(reader.read(&mut [0]), Ok(0));
            assert_eq!(pipe.deliver(b"fresh"), Err(MaterialError::Rejected));
            drop(reader);
            assert_eq!(pipe.retire(), Ok(()));
        }
        let fixture = Fixture::new();
        let mut pipe = fixture.pipe();
        let mut reader = fixture.reader(&pipe);
        pipe.close();
        assert_eq!(reader.read(&mut [0]), Ok(0));
        assert_eq!(pipe.deliver(b"fresh"), Err(MaterialError::Rejected));
        drop(reader);
        assert_eq!(pipe.retire(), Ok(()));
    }

    #[test]
    fn real_fifo_backpressure_is_uncertain_and_cannot_replay() {
        let fixture = Fixture::new();
        let mut pipe = fixture.pipe();
        let mut reader = fixture.reader(&pipe);
        assert_eq!(
            pipe.deliver(&vec![1; MAX_MATERIAL]),
            Err(MaterialError::UncertainDelivery)
        );
        assert_eq!(pipe.deliver(b"fresh"), Err(MaterialError::Rejected));
        let mut partial = Vec::new();
        assert!(reader.read_to_end(&mut partial).is_ok());
        assert!(!partial.is_empty() && partial.len() < MAX_MATERIAL + 4);
        assert_eq!(&partial[..4], &(MAX_MATERIAL as u32).to_be_bytes());
        drop(reader);
        assert_eq!(pipe.retire(), Ok(()));
    }

    #[test]
    fn unsafe_staging_parent_denies_without_creating_session() {
        let fixture = Fixture::new();
        for uid in [0, fixture.uid + 1] {
            assert!(MaterialPipe::create(&fixture.parent, SESSION, uid).is_err());
        }
        assert!(MaterialPipe::create(&fixture.parent, "../escape", fixture.uid).is_err());
        let alias = fixture.parent.with_extension("alias");
        assert!(symlink(&fixture.parent, &alias).is_ok());
        assert!(MaterialPipe::create(&alias, SESSION, fixture.uid).is_err());
        assert!(fs::remove_file(alias).is_ok());
        assert!(fs::set_permissions(&fixture.parent, fs::Permissions::from_mode(0o750)).is_ok());
        assert!(MaterialPipe::create(&fixture.parent, SESSION, fixture.uid).is_err());
        assert!(!fixture.parent.join(SESSION).exists());
    }

    #[test]
    fn replacement_is_not_adopted_or_deleted_as_owned_fifo() {
        let fixture = Fixture::new();
        let mut pipe = fixture.pipe();
        pipe.close();
        let path = pipe.directory().join("sdk.pipe");
        assert!(fs::remove_file(&path).is_ok());
        assert!(fs::write(&path, b"unowned replacement").is_ok());
        assert_eq!(pipe.retire(), Err(MaterialError::CleanupRequired));
        assert_eq!(fs::read(path).ok(), Some(b"unowned replacement".to_vec()));
    }

    #[test]
    fn short_writes_preserve_offset_and_errors_do_not_retry() {
        struct Short {
            bytes: Vec<u8>,
            fail_after: Option<usize>,
            calls: usize,
        }
        impl Write for Short {
            fn write(&mut self, raw: &[u8]) -> io::Result<usize> {
                self.calls += 1;
                if self
                    .fail_after
                    .is_some_and(|limit| self.bytes.len() >= limit)
                {
                    return Err(io::ErrorKind::Interrupted.into());
                }
                self.bytes.push(raw[0]);
                Ok(1)
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        let mut writer = Short {
            bytes: Vec::new(),
            fail_after: None,
            calls: 0,
        };
        assert_eq!(write_once(&mut writer, b"abc"), Ok(()));
        assert_eq!(writer.bytes, b"\0\0\0\x03abc");
        let mut writer = Short {
            bytes: Vec::new(),
            fail_after: Some(5),
            calls: 0,
        };
        assert_eq!(
            write_once(&mut writer, b"abc"),
            Err(MaterialError::UncertainDelivery)
        );
        assert_eq!(writer.bytes, b"\0\0\0\x03a");
        assert_eq!(writer.calls, 6);
    }
}
