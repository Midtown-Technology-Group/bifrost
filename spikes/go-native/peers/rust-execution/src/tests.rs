use crate::{Codec, Error, MAX_FRAME};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Cursor, Read, Write};

fn vectors() -> Value {
    serde_json::from_str(include_str!(
        "../../../executionprofile/testdata/wire-vectors.json"
    ))
    .unwrap_or_else(|_| panic!("invalid trusted corpus"))
}
fn unhex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|p| {
            u8::from_str_radix(std::str::from_utf8(p).unwrap_or_default(), 16)
                .unwrap_or_else(|_| panic!("invalid trusted corpus hex"))
        })
        .collect()
}
fn bytes(case: &Value) -> Vec<u8> {
    if case["recipe"].is_null() {
        return unhex(case["hex"].as_str().unwrap_or_default());
    }
    let recipe = &case["recipe"];
    let mut result = unhex(recipe["prefix_hex"].as_str().unwrap_or_default());
    result.extend(
        unhex(recipe["repeat_byte_hex"].as_str().unwrap_or_default())
            .repeat(recipe["count"].as_u64().unwrap_or_default() as usize),
    );
    result.extend(unhex(recipe["suffix_hex"].as_str().unwrap_or_default()));
    result
}
fn codec() -> Codec {
    Codec::new().unwrap_or_else(|_| panic!("invalid trusted schemas"))
}
fn wire_case(index: usize) {
    let cases = vectors();
    assert_eq!(cases.as_array().map(Vec::len), Some(94));
    let case = &cases[index];
    let raw = bytes(case);
    let mut cursor = Cursor::new(&raw);
    let result = codec().read(&mut cursor);
    let error = match result {
        Ok(_) if cursor.position() as usize != raw.len() => Some("InvalidFrame".to_owned()),
        Ok(_) => None,
        Err(error) => Some(format!("{error:?}")),
    };
    assert_eq!(error.as_deref(), case["error"].as_str(), "{}", case["name"]);
}
macro_rules! case_test {
    ($name:ident, $index:expr) => {
        #[test]
        fn $name() {
            wire_case($index);
        }
    };
}

struct Fragmented {
    cursor: Cursor<Vec<u8>>,
    read_interrupt: bool,
    write_interrupt: bool,
}
impl Read for Fragmented {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.read_interrupt {
            self.read_interrupt = false;
            return Err(io::ErrorKind::Interrupted.into());
        }
        let size = buffer.len().min(1);
        self.cursor.read(&mut buffer[..size])
    }
}
impl Write for Fragmented {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if self.write_interrupt {
            self.write_interrupt = false;
            return Err(io::ErrorKind::Interrupted.into());
        }
        self.cursor.write(&buffer[..buffer.len().min(1)])
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn offer() -> Vec<u8> {
    bytes(&vectors()[0])
}

#[test]
fn fragmented_interrupted_stream_and_clean_eof() {
    let codec = codec();
    let frame = codec
        .read(&mut Cursor::new(offer()))
        .ok()
        .flatten()
        .unwrap_or_else(|| panic!("missing trusted Offer"));
    let mut sink = Fragmented {
        cursor: Cursor::new(Vec::new()),
        read_interrupt: true,
        write_interrupt: true,
    };
    codec
        .write(&mut sink, &frame.frame)
        .unwrap_or_else(|e| panic!("{e}"));
    let encoded = sink.cursor.into_inner();
    let mut stream = Fragmented {
        cursor: Cursor::new([encoded.clone(), encoded].concat()),
        read_interrupt: true,
        write_interrupt: true,
    };
    for _ in 0..2 {
        assert_eq!(
            codec.read(&mut stream).ok().flatten().map(|v| v.frame),
            Some(frame.frame.clone())
        );
    }
    assert!(codec.read(&mut stream).ok().flatten().is_none());
}
#[test]
fn immutable_raw_digest_survives_view_and_input_changes() {
    let codec = codec();
    let mut raw = offer();
    let mut decoded = codec
        .read(&mut Cursor::new(&raw))
        .ok()
        .flatten()
        .unwrap_or_else(|| panic!("missing trusted Offer"));
    let digest = format!("{:x}", Sha256::digest(&raw[4..]));
    raw.fill(0);
    decoded.frame["body"] = json!({});
    assert_eq!(decoded.payload_sha256(), digest);
}
#[test]
fn validates_before_writing() {
    let codec = codec();
    let mut frame = codec
        .read(&mut Cursor::new(offer()))
        .ok()
        .flatten()
        .unwrap_or_else(|| panic!("missing trusted Offer"))
        .frame;
    frame["sequence"] = json!(true);
    let mut sink = Vec::new();
    assert_eq!(codec.write(&mut sink, &frame), Err(Error::InvalidFrame));
    assert!(sink.is_empty());
}
#[test]
fn oversize_prefix_rejected_before_allocation() {
    assert_eq!(
        codec()
            .read(&mut Cursor::new(((MAX_FRAME + 1) as u32).to_be_bytes()))
            .err(),
        Some(Error::FrameTooLarge)
    );
}
#[test]
fn zero_progress_write_and_io_fail_closed() {
    struct Broken;
    impl Read for Broken {
        fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
            Err(io::ErrorKind::Other.into())
        }
    }
    impl Write for Broken {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Ok(0)
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    let codec = codec();
    assert_eq!(codec.read(&mut Broken).err(), Some(Error::Io));
    let frame = codec
        .read(&mut Cursor::new(offer()))
        .ok()
        .flatten()
        .unwrap_or_else(|| panic!("missing trusted Offer"))
        .frame;
    assert_eq!(codec.write(&mut Broken, &frame), Err(Error::Io));
}
case_test!(wire_000_valid_offer, 0);
case_test!(wire_001_valid_select, 1);
case_test!(wire_002_valid_prepare, 2);
case_test!(wire_003_valid_prepared, 3);
case_test!(wire_004_valid_start, 4);
case_test!(wire_005_valid_provision, 5);
case_test!(wire_006_valid_heartbeat, 6);
case_test!(wire_007_valid_logbatch, 7);
case_test!(wire_008_valid_usage, 8);
case_test!(wire_009_valid_result, 9);
case_test!(wire_010_valid_cancel, 10);
case_test!(wire_011_valid_stopped, 11);
case_test!(wire_012_valid_resultreceipt, 12);
case_test!(wire_013_valid_error, 13);
case_test!(wire_014_artifact_native_executable_v1, 14);
case_test!(wire_015_artifact_interpreted_runtime_v1, 15);
case_test!(wire_016_artifact_managed_runtime_v1, 16);
case_test!(wire_017_missing_body_field_offer, 17);
case_test!(wire_018_unknown_body_field_offer, 18);
case_test!(wire_019_missing_body_field_select, 19);
case_test!(wire_020_unknown_body_field_select, 20);
case_test!(wire_021_missing_body_field_prepare, 21);
case_test!(wire_022_unknown_body_field_prepare, 22);
case_test!(wire_023_missing_body_field_prepared, 23);
case_test!(wire_024_unknown_body_field_prepared, 24);
case_test!(wire_025_missing_body_field_start, 25);
case_test!(wire_026_unknown_body_field_start, 26);
case_test!(wire_027_missing_body_field_provision, 27);
case_test!(wire_028_unknown_body_field_provision, 28);
case_test!(wire_029_missing_body_field_heartbeat, 29);
case_test!(wire_030_unknown_body_field_heartbeat, 30);
case_test!(wire_031_missing_body_field_logbatch, 31);
case_test!(wire_032_unknown_body_field_logbatch, 32);
case_test!(wire_033_missing_body_field_usage, 33);
case_test!(wire_034_unknown_body_field_usage, 34);
case_test!(wire_035_missing_body_field_result, 35);
case_test!(wire_036_unknown_body_field_result, 36);
case_test!(wire_037_missing_body_field_cancel, 37);
case_test!(wire_038_unknown_body_field_cancel, 38);
case_test!(wire_039_missing_body_field_stopped, 39);
case_test!(wire_040_unknown_body_field_stopped, 40);
case_test!(wire_041_missing_body_field_resultreceipt, 41);
case_test!(wire_042_unknown_body_field_resultreceipt, 42);
case_test!(wire_043_missing_envelope_protocol, 43);
case_test!(wire_044_missing_envelope_type, 44);
case_test!(wire_045_missing_envelope_session_id, 45);
case_test!(wire_046_missing_envelope_message_id, 46);
case_test!(wire_047_missing_envelope_sequence, 47);
case_test!(wire_048_missing_envelope_correlation_id, 48);
case_test!(wire_049_missing_envelope_body, 49);
case_test!(wire_050_bool_sequence, 50);
case_test!(wire_051_zero_sequence, 51);
case_test!(wire_052_unsafe_sequence, 52);
case_test!(wire_053_upper_uuid, 53);
case_test!(wire_054_success_and_error, 54);
case_test!(wire_055_negative_usage, 55);
case_test!(wire_056_unbounded_log, 56);
case_test!(wire_057_duplicate_capability, 57);
case_test!(wire_058_impossible_utc, 58);
case_test!(wire_059_utc_offset, 59);
case_test!(wire_060_global_with_org, 60);
case_test!(wire_061_org_with_null, 61);
case_test!(wire_062_missing_original_provenance, 62);
case_test!(wire_063_tenant_authority_flag, 63);
case_test!(wire_064_adapter_authority_flag, 64);
case_test!(wire_065_receipt_accepted_cancel_winner, 65);
case_test!(wire_066_secret_provision, 66);
case_test!(wire_067_python_only_protocol_field, 67);
case_test!(wire_068_duplicate_key, 68);
case_test!(wire_069_lexical_negative_zero, 69);
case_test!(wire_070_float_sequence, 70);
case_test!(wire_071_integer_f64_fallback, 71);
case_test!(wire_072_numeric_overflow, 72);
case_test!(wire_073_invalid_utf8, 73);
case_test!(wire_074_lone_surrogate, 74);
case_test!(wire_075_bad_json, 75);
case_test!(wire_076_nan, 76);
case_test!(wire_077_unsupported_protocol_before_sequence, 77);
case_test!(wire_078_unsupported_type, 78);
case_test!(wire_079_depth_overflow, 79);
case_test!(wire_080_depth_boundary, 80);
case_test!(wire_081_type_error_before_unsupported_kind, 81);
case_test!(wire_082_non_string_protocol, 82);
case_test!(wire_083_wrong_start_correlation, 83);
case_test!(wire_084_clean_eof, 84);
case_test!(wire_085_partial_prefix, 85);
case_test!(wire_086_zero_length, 86);
case_test!(wire_087_oversize_prefix, 87);
case_test!(wire_088_exact_max_truncated, 88);
case_test!(wire_089_partial_body, 89);
case_test!(wire_090_exact_max_frame, 90);
case_test!(wire_091_body_uuid_trailing_newline, 91);
case_test!(wire_092_artifact_hash_trailing_newline, 92);
case_test!(wire_093_float_attempt_in_binding, 93);
