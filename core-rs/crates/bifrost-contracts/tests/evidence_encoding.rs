//! Tests-only byte compatibility. No runtime, installation, or authority claim.

#[path = "support/evidence_encoding/mod.rs"]
mod evidence_encoding;

use evidence_encoding::{
    EncodingLimits, EvidenceEncodeError as Error, Limit, ProjectedRoot, ProjectedValue as V,
    encode_delivery_ascii_fixture, encode_finite_sorted_utf8, encode_sorted_ascii_fixture,
    encode_workspace_source_fixture,
    fixtures::{self, Description},
};

type TestResult = Result<(), Box<dyn std::error::Error>>;
fn root(value: V) -> ProjectedRoot {
    ProjectedRoot(vec![("v".to_owned(), value)])
}

#[test]
fn all_64_source_vectors_preserve_bytes_types_and_failure_categories() -> TestResult {
    let vectors = fixtures::load()?;
    let mut encoded = 0;
    let mut exceptions = 0;
    for vector in vectors {
        match vector.outcome.as_str() {
            "encoded" => {
                let bytes = vector.render()?;
                assert_eq!(
                    Some(bytes.as_slice()),
                    vector.utf8.as_deref().map(str::as_bytes),
                    "{}:{}",
                    vector.profile,
                    vector.name
                );
                assert_eq!(Some(fixtures::hex(&bytes)), vector.hex);
                let hash = vector.sha256.as_deref().ok_or(Error::InvalidFixture)?;
                assert_eq!(hash.len(), 64);
                assert!(
                    hash.bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                );
                assert_eq!(
                    vector.digest.as_deref(),
                    Some(format!("sha256:{hash}").as_str())
                );
                if let Some(helper) = &vector.helper_digest {
                    assert_eq!(Some(helper), vector.digest.as_ref());
                }
                assert!(vector.exception_type.is_none());
                assert!(vector.expected_failure_observed.is_none());
                if vector.profile == "PythonWorkspaceIdentityV1"
                    && matches!(
                        vector.name.as_str(),
                        "nan" | "positive-infinity" | "negative-infinity"
                    )
                {
                    assert_eq!(
                        encode_finite_sorted_utf8(
                            &fixtures::decode_root(&vector.input)?,
                            EncodingLimits::default()
                        ),
                        Err(Error::NonFinite)
                    );
                }
                encoded += 1;
            }
            "exception" => {
                assert_eq!(vector.profile, "PythonDeploymentEvidenceV1");
                assert!(matches!(
                    vector.name.as_str(),
                    "nan" | "positive-infinity" | "negative-infinity"
                ));
                assert_eq!(vector.render(), Err(Error::NonFinite));
                assert_eq!(vector.exception_type.as_deref(), Some("ValueError"));
                assert_eq!(vector.expected_failure_observed, Some(true));
                assert!(
                    vector.utf8.is_none()
                        && vector.hex.is_none()
                        && vector.sha256.is_none()
                        && vector.digest.is_none()
                );
                exceptions += 1;
            }
            _ => return Err(Error::InvalidFixture.into()),
        }
    }
    assert_eq!((encoded, exceptions), (61, 3));
    // Hash calculation is independent in the pinned Python peer, over Rust-returned bytes.
    Ok(())
}

#[test]
fn integer_kind_sign_zero_and_bits_are_preserved() -> TestResult {
    for (value, expected) in [
        (V::I64(i64::MIN), "{\"v\":-9223372036854775808}"),
        (V::U64(u64::MAX), "{\"v\":18446744073709551615}"),
        (V::I64(0), "{\"v\":0}"),
        (V::F64(0.0), "{\"v\":0.0}"),
        (V::F64(-0.0), "{\"v\":-0.0}"),
    ] {
        assert_eq!(
            encode_finite_sorted_utf8(&root(value), EncodingLimits::default())?,
            expected.as_bytes()
        );
    }
    assert_ne!(V::F64(0.0), V::F64(-0.0));
    let description: Description = serde_json::from_str(
        r#"{"kind":"object","entries":[["v",{"kind":"f64","bits_be_hex":"3ee4f8b588e368f1","repr":"deliberately not numeric"}]]}"#,
    )?;
    assert_eq!(
        encode_finite_sorted_utf8(
            &fixtures::decode_root(&description)?,
            EncodingLimits::default()
        )?,
        br#"{"v":1e-05}"#
    );
    for decimal in [
        "-0",
        "01",
        "+1",
        "1.0",
        "18446744073709551616",
        "-9223372036854775809",
    ] {
        let description = Description::Object {
            entries: vec![(
                "v".to_owned(),
                Description::Int {
                    decimal: decimal.to_owned(),
                },
            )],
        };
        assert_eq!(
            fixtures::decode_root(&description),
            Err(Error::InvalidFixture)
        );
    }
    Ok(())
}

#[test]
fn unicode_scalar_order_ascii_del_and_array_order_are_separate() -> TestResult {
    let input = ProjectedRoot(vec![
        ("\u{10000}".to_owned(), V::String("\u{7f}é".to_owned())),
        ("\u{e000}".to_owned(), V::Array(vec![V::I64(2), V::I64(1)])),
    ]);
    assert_eq!(
        encode_finite_sorted_utf8(&input, EncodingLimits::default())?,
        "{\"\u{e000}\":[2,1],\"\u{10000}\":\"\u{7f}é\"}".as_bytes()
    );
    assert_eq!(
        encode_sorted_ascii_fixture(&input, EncodingLimits::default())?,
        br#"{"\ue000":[2,1],"\ud800\udc00":"\u007f\u00e9"}"#
    );
    assert_eq!(
        encode_delivery_ascii_fixture(&input, EncodingLimits::default())?,
        br#"{"\ud800\udc00": "\u007f\u00e9", "\ue000": [2, 1]}"#
    );
    let mut permuted = input.clone();
    permuted.0.reverse();
    assert_eq!(
        encode_finite_sorted_utf8(&input, EncodingLimits::default())?,
        encode_finite_sorted_utf8(&permuted, EncodingLimits::default())?
    );
    assert_ne!(
        encode_delivery_ascii_fixture(&input, EncodingLimits::default())?,
        encode_delivery_ascii_fixture(&permuted, EncodingLimits::default())?
    );
    assert_ne!(
        encode_finite_sorted_utf8(&root(V::String("é".to_owned())), EncodingLimits::default())?,
        encode_finite_sorted_utf8(
            &root(V::String("e\u{301}".to_owned())),
            EncodingLimits::default()
        )?
    );
    Ok(())
}

#[test]
fn duplicate_keys_and_nested_nonfinite_never_become_null() {
    type Encoder = fn(&ProjectedRoot, EncodingLimits) -> Result<Vec<u8>, Error>;
    let encoders: [Encoder; 4] = [
        encode_finite_sorted_utf8,
        encode_sorted_ascii_fixture,
        encode_delivery_ascii_fixture,
        encode_workspace_source_fixture,
    ];
    let duplicate = root(V::Object(vec![
        ("same".to_owned(), V::Null),
        ("same".to_owned(), V::Bool(false)),
    ]));
    for encoder in encoders {
        assert_eq!(
            encoder(&duplicate, EncodingLimits::default()),
            Err(Error::DuplicateKey)
        );
    }
    for raw in [
        0x7ff0_0000_0000_0000,
        0xfff0_0000_0000_0000,
        0x7ff8_0000_0000_0000,
        0xfff8_0000_0000_0001,
    ] {
        let input = root(V::Array(vec![V::Object(vec![(
            "n".to_owned(),
            V::F64(f64::from_bits(raw)),
        )])]));
        for encoder in &encoders[..3] {
            assert_eq!(
                encoder(&input, EncodingLimits::default()),
                Err(Error::NonFinite)
            );
        }
    }
}

#[test]
fn limits_are_inclusive_checked_before_returning_bytes_and_cannot_be_raised() -> TestResult {
    let input = root(V::String("\n".to_owned()));
    let bytes = encode_finite_sorted_utf8(&input, EncodingLimits::default())?;
    for (limits, category) in [
        (
            EncodingLimits {
                nodes: 1,
                ..EncodingLimits::default()
            },
            Limit::Nodes,
        ),
        (
            EncodingLimits {
                depth: 0,
                ..EncodingLimits::default()
            },
            Limit::Depth,
        ),
        (
            EncodingLimits {
                object_members: 0,
                ..EncodingLimits::default()
            },
            Limit::ObjectMembers,
        ),
        (
            EncodingLimits {
                input_text: 1,
                ..EncodingLimits::default()
            },
            Limit::InputText,
        ),
        (
            EncodingLimits {
                output_bytes: bytes.len() - 1,
                ..EncodingLimits::default()
            },
            Limit::OutputBytes,
        ),
    ] {
        assert_eq!(
            encode_finite_sorted_utf8(&input, limits),
            Err(Error::LimitExceeded(category))
        );
    }
    assert_eq!(
        encode_finite_sorted_utf8(
            &input,
            EncodingLimits {
                nodes: 2,
                depth: 1,
                object_members: 1,
                input_text: 2,
                output_bytes: bytes.len()
            }
        )?,
        bytes
    );
    for (limits, category) in [
        (
            EncodingLimits {
                depth: 65,
                ..EncodingLimits::default()
            },
            Limit::Depth,
        ),
        (
            EncodingLimits {
                nodes: 65_537,
                ..EncodingLimits::default()
            },
            Limit::Nodes,
        ),
        (
            EncodingLimits {
                object_members: 4_097,
                ..EncodingLimits::default()
            },
            Limit::ObjectMembers,
        ),
        (
            EncodingLimits {
                input_text: 1024 * 1024 + 1,
                ..EncodingLimits::default()
            },
            Limit::InputText,
        ),
        (
            EncodingLimits {
                output_bytes: 1024 * 1024 + 1,
                ..EncodingLimits::default()
            },
            Limit::OutputBytes,
        ),
    ] {
        assert_eq!(
            encode_finite_sorted_utf8(&ProjectedRoot(vec![]), limits),
            Err(Error::LimitExceeded(category))
        );
    }
    Ok(())
}

#[test]
fn maximum_depth_nodes_members_and_escape_output_have_negative_neighbors() -> TestResult {
    let mut nested = V::Null;
    for _ in 0..63 {
        nested = V::Array(vec![nested]);
    }
    encode_finite_sorted_utf8(&root(nested.clone()), EncodingLimits::default())?;
    assert_eq!(
        encode_finite_sorted_utf8(&root(V::Array(vec![nested])), EncodingLimits::default()),
        Err(Error::LimitExceeded(Limit::Depth))
    );
    let mut nodes = vec![V::Null; 65_534];
    encode_finite_sorted_utf8(&root(V::Array(nodes.clone())), EncodingLimits::default())?;
    nodes.push(V::Null);
    assert_eq!(
        encode_finite_sorted_utf8(&root(V::Array(nodes)), EncodingLimits::default()),
        Err(Error::LimitExceeded(Limit::Nodes))
    );
    let mut entries: Vec<_> = (0..4096).map(|i| (i.to_string(), V::Null)).collect();
    encode_finite_sorted_utf8(&ProjectedRoot(entries.clone()), EncodingLimits::default())?;
    entries.push(("extra".to_owned(), V::Null));
    assert_eq!(
        encode_finite_sorted_utf8(&ProjectedRoot(entries), EncodingLimits::default()),
        Err(Error::LimitExceeded(Limit::ObjectMembers))
    );
    let max_bytes = EncodingLimits::default().output_bytes;
    assert_eq!(
        encode_finite_sorted_utf8(
            &root(V::String("a".repeat(max_bytes - 8))),
            EncodingLimits::default()
        )?
        .len(),
        max_bytes
    );
    assert_eq!(
        encode_finite_sorted_utf8(
            &root(V::String("a".repeat(max_bytes - 7))),
            EncodingLimits::default()
        ),
        Err(Error::LimitExceeded(Limit::OutputBytes))
    );
    assert_eq!(
        encode_finite_sorted_utf8(
            &root(V::String("a".repeat(max_bytes))),
            EncodingLimits::default()
        ),
        Err(Error::LimitExceeded(Limit::InputText))
    );
    assert_eq!(
        encode_sorted_ascii_fixture(
            &root(V::String("😀".to_owned())),
            EncodingLimits {
                output_bytes: 19,
                ..EncodingLimits::default()
            }
        ),
        Err(Error::LimitExceeded(Limit::OutputBytes))
    );
    Ok(())
}

#[test]
fn new_model_fields_classes_and_shared_table_content_stop_projection() -> TestResult {
    let mut vectors = fixtures::load()?;
    let model = vectors
        .iter_mut()
        .find(|v| v.name == "controls-default")
        .ok_or(Error::InvalidFixture)?;
    if let Description::Model {
        model_dump_json, ..
    } = &mut model.input
    {
        if let Description::Object { entries } = model_dump_json.as_mut() {
            entries.push(("additional_scopes".to_owned(), Description::Null {}));
        } else {
            return Err(Error::InvalidFixture.into());
        }
    } else {
        return Err(Error::InvalidFixture.into());
    }
    assert_eq!(model.render(), Err(Error::InvalidFixture));
    if let Description::Model { class, .. } = &mut model.input {
        *class = "SharedRootTableGrant".to_owned();
    }
    assert_eq!(model.render(), Err(Error::InvalidFixture));
    let recipe = vectors
        .iter_mut()
        .find(|v| v.name == "selected-A-shaped-recipe")
        .ok_or(Error::InvalidFixture)?;
    if let Description::Model {
        model_dump_json, ..
    } = &mut recipe.input
    {
        if let Description::Object { entries } = model_dump_json.as_mut() {
            let (_, tables) = entries
                .iter_mut()
                .find(|(key, _)| key == "shared_tables")
                .ok_or(Error::InvalidFixture)?;
            *tables = Description::Object {
                entries: vec![(
                    "unobserved".to_owned(),
                    Description::Object { entries: vec![] },
                )],
            };
        } else {
            return Err(Error::InvalidFixture.into());
        }
    } else {
        return Err(Error::InvalidFixture.into());
    }
    assert_eq!(recipe.render(), Err(Error::InvalidFixture));
    Ok(())
}
