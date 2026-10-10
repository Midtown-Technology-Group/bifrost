//! Independent closed USTAR consumer for isolated-native-bundle/v1.
//! Pure byte verification; no extraction, process, admission or lifecycle write.
use sha2::{Digest, Sha256};

pub const NAMES: [&str; 6] = [
    "adapter",
    "build-evidence.json",
    "input-schema.json",
    "module-graph.txt",
    "output-schema.json",
    "workflow",
];
pub const MAX_ARCHIVE: usize = 96 * 1024 * 1024;
const LIMITS: [usize; 6] = [32 << 20, 16 << 20, 1 << 20, 1 << 20, 1 << 20, 32 << 20];

#[derive(Debug, PartialEq, Eq)]
pub struct Rejected;

/// Pins must come from authenticated producer evidence and accepted association,
/// never the workload, archive contents or an unauthenticated caller.
pub struct ArchivePins {
    pub archive_sha256: String,
    pub entries_sha256: [String; 6],
}

pub fn sha256(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

fn header(name: &str, size: usize) -> [u8; 512] {
    let mut raw = [0; 512];
    raw[..name.len()].copy_from_slice(name.as_bytes());
    raw[100..108].copy_from_slice(if name == "adapter" || name == "workflow" {
        b"0000500\0"
    } else {
        b"0000400\0"
    });
    raw[108..116].copy_from_slice(b"0000000\0");
    raw[116..124].copy_from_slice(b"0000000\0");
    raw[124..136].copy_from_slice(format!("{size:011o}\0").as_bytes());
    raw[136..148].copy_from_slice(b"00000000000\0");
    raw[148..156].copy_from_slice(b"        ");
    raw[156] = b'0';
    raw[257..265].copy_from_slice(b"ustar\x0000");
    let checksum: usize = raw.iter().map(|b| usize::from(*b)).sum();
    raw[148..156].copy_from_slice(format!("{checksum:06o}\0 ").as_bytes());
    raw
}

/// Return borrowed immutable contents only after checking exact archive identity,
/// every accepted entry pin, canonical headers, all padding and closed file set.
pub fn verify_archive<'a>(raw: &'a [u8], pins: &ArchivePins) -> Result<[&'a [u8]; 6], Rejected> {
    if raw.is_empty()
        || raw.len() > MAX_ARCHIVE
        || !crate::digest(&pins.archive_sha256)
        || pins.entries_sha256.iter().any(|pin| !crate::digest(pin))
        || sha256(raw) != pins.archive_sha256
    {
        return Err(Rejected);
    }
    let mut entries = [&[][..]; 6];
    let mut offset = 0usize;
    for (i, name) in NAMES.iter().enumerate() {
        let block = raw.get(offset..offset + 512).ok_or(Rejected)?;
        let size_text = std::str::from_utf8(&block[124..135]).map_err(|_| Rejected)?;
        if !size_text.bytes().all(|b| (b'0'..=b'7').contains(&b)) {
            return Err(Rejected);
        }
        let size = usize::from_str_radix(size_text, 8).map_err(|_| Rejected)?;
        if size == 0 || size > LIMITS[i] || block != header(name, size) {
            return Err(Rejected);
        }
        offset += 512;
        let content = raw.get(offset..offset + size).ok_or(Rejected)?;
        if sha256(content) != pins.entries_sha256[i] {
            return Err(Rejected);
        }
        entries[i] = content;
        offset += size;
        let padding = (512 - size % 512) % 512;
        if raw
            .get(offset..offset + padding)
            .ok_or(Rejected)?
            .iter()
            .any(|b| *b != 0)
        {
            return Err(Rejected);
        }
        offset += padding;
    }
    let expected_length = (offset + 1024).div_ceil(10240) * 10240;
    if raw.len() != expected_length || raw[offset..].iter().any(|b| *b != 0) {
        return Err(Rejected);
    }
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Vec<u8>, ArchivePins) {
        let mut raw = Vec::new();
        let mut entries_sha256 = std::array::from_fn(|_| String::new());
        for (i, name) in NAMES.iter().enumerate() {
            let contents = format!("independent {name} fixture");
            entries_sha256[i] = sha256(contents.as_bytes());
            raw.extend_from_slice(&header(name, contents.len()));
            raw.extend_from_slice(contents.as_bytes());
            raw.resize(raw.len().div_ceil(512) * 512, 0);
        }
        raw.resize((raw.len() + 1024).div_ceil(10240) * 10240, 0);
        let pins = ArchivePins {
            archive_sha256: sha256(&raw),
            entries_sha256,
        };
        (raw, pins)
    }
    #[test]
    fn closed_archive_checks_pins_headers_and_hidden_data() {
        let (raw, pins) = fixture();
        assert!(verify_archive(&raw, &pins).is_ok());
        for offset in [0, 100, 108, 136, 156, 257, 512, raw.len() - 1] {
            let mut changed = raw.clone();
            changed[offset] ^= 1;
            let new_pins = ArchivePins {
                archive_sha256: sha256(&changed),
                entries_sha256: pins.entries_sha256.clone(),
            };
            assert!(verify_archive(&changed, &new_pins).is_err());
            assert!(verify_archive(&changed, &pins).is_err());
        }
        let mut extra = raw.clone();
        extra.extend_from_slice(&[0; 10240]);
        let extra_pins = ArchivePins {
            archive_sha256: sha256(&extra),
            entries_sha256: pins.entries_sha256.clone(),
        };
        assert!(verify_archive(&extra, &extra_pins).is_err());
    }
}
