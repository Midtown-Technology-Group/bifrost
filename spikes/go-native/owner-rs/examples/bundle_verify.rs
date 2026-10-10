//! Trusted hosted recipe consumer; no artifact acceptance, extraction or launch.
use bifrost_isolated_owner_spike::archive::{
    ArchivePins, MAX_ARCHIVE, NAMES, sha256, verify_archive,
};
use serde_json::{Value, json};
use std::{fs::File, io::Read};

fn read(path: &str, bound: usize) -> Result<Vec<u8>, ()> {
    let mut raw = Vec::new();
    File::open(path)
        .map_err(|_| ())?
        .take((bound + 1) as u64)
        .read_to_end(&mut raw)
        .map_err(|_| ())?;
    if raw.is_empty() || raw.len() > bound {
        return Err(());
    }
    Ok(raw)
}

fn run() -> Result<Value, ()> {
    let arguments: Vec<_> = std::env::args().collect();
    if arguments.len() != 3 {
        return Err(());
    }
    // The fixed CI caller verifies authenticated producer/signature custody
    // before invoking this consumer with its external descriptor. JSON fields
    // do not become public runtime arguments, authorization or live custody.
    let raw = read(&arguments[1], MAX_ARCHIVE)?;
    let descriptor: Value = serde_json::from_slice(&read(&arguments[2], 65536)?).map_err(|_| ())?;
    if descriptor["bundle_recipe"] != "isolated-native-bundle/v1"
        || descriptor["runtime_acceptance"] != false
        || descriptor["reviewed_deployment"] != false
    {
        return Err(());
    }
    let entries = descriptor["entries"]
        .as_array()
        .filter(|e| e.len() == 6)
        .ok_or(())?;
    let mut entries_sha256 = std::array::from_fn(|_| String::new());
    for (i, name) in NAMES.iter().enumerate() {
        if entries[i]["path"] != *name {
            return Err(());
        }
        entries_sha256[i] = entries[i]["sha256"].as_str().ok_or(())?.into();
    }
    let artifact = &descriptor["artifact"];
    if artifact["kind"] != "native-executable/v1"
        || artifact["runtime_protocol"] != "bifrost.runtime/v1"
    {
        return Err(());
    }
    let archive_sha256 = artifact["artifact_id"]
        .as_str()
        .and_then(|s| s.strip_prefix("sha256:"))
        .ok_or(())?
        .into();
    let pins = ArchivePins {
        archive_sha256,
        entries_sha256,
    };
    let contents = verify_archive(&raw, &pins).map_err(|_| ())?;
    if descriptor["archive_size_bytes"].as_u64() != Some(raw.len() as u64)
        || artifact["adapter_sha256"] != sha256(contents[0])
        || artifact["build_evidence_sha256"] != sha256(contents[1])
        || descriptor["input_schema_sha256"] != sha256(contents[2])
        || artifact["dependencies"]["digest"] != format!("sha256:{}", sha256(contents[3]))
        || descriptor["output_schema_sha256"] != sha256(contents[4])
        || artifact["executable_sha256"] != sha256(contents[5])
        || sha256(contents[5]) != "160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34"
    {
        return Err(());
    }
    for (i, entry) in entries.iter().enumerate() {
        if entry["size_bytes"].as_u64() != Some(contents[i].len() as u64) {
            return Err(());
        }
    }
    Ok(
        json!({"bundle_recipe":"isolated-native-bundle/v1", "artifact_id": artifact["artifact_id"], "unchanged_workflow_sha256": sha256(contents[5]), "entries_verified":6, "runtime_acceptance": false}),
    )
}

fn main() {
    match run() {
        Ok(evidence) => println!("{evidence}"),
        Err(_) => {
            eprintln!("native bundle rejected");
            std::process::exit(1);
        }
    }
}
