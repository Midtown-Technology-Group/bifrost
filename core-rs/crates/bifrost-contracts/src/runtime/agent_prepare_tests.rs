use std::{error::Error as StdError,io::Cursor};
use serde::Deserialize;
use sha2::{Digest,Sha256};
use super::{AgentPrepareCodec,Error,MAX_FRAME_BYTES,decode_json,decode_ordinary_json,validate_prepared_binding};
#[derive(Deserialize)]
struct Corpus{wire:Vec<Vector>}
#[derive(Deserialize)]
struct Vector{name:String,json:String,expected:String}
fn vectors()->Result<Vec<Vector>,Box<dyn StdError>>{Ok(serde_json::from_str::<Corpus>(include_str!("../../tests/fixtures/runtime/v1/agent-prepare-vectors.json"))?.wire)}
fn named(name:&str)->Result<String,Box<dyn StdError>>{vectors()?.into_iter().find(|v|v.name==name).map(|v|v.json).ok_or_else(||std::io::Error::other("missing synthetic vector").into())}
#[test]
fn full_shared_corpus()->Result<(),Box<dyn StdError>>{
    let codec=AgentPrepareCodec::new();for v in vectors()?{let result=codec.decode_json(v.json.as_bytes());if v.expected=="ok"{result?;}else{assert_eq!(result.err().map(|e|format!("{e:?}")),Some(v.expected));}}Ok(())
}
#[test]
fn original_bytes_not_normalized()->Result<(),Box<dyn StdError>>{
    let codec=AgentPrepareCodec::new();for name in ["prepare-parent","raw-whitespace","raw-float-spellings"]{let raw=named(name)?;let decoded=codec.decode_json(raw.as_bytes())?;let expected=decoded.payload_sha256().to_owned();assert_eq!(expected,format!("{:x}",Sha256::digest(raw.as_bytes())));let encoded=codec.encode_retained(decoded)?;assert_eq!(encoded.payload_sha256(),expected);let mut wire=Vec::new();codec.write_frame(&mut wire,&encoded)?;assert_eq!(&wire[4..],raw.as_bytes());}Ok(())
}
#[test]
fn byte_different_hashes()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let a=codec.decode_json(named("prepare-parent")?.as_bytes())?;let b=codec.decode_json(named("raw-whitespace")?.as_bytes())?;assert_ne!(a.payload_sha256(),b.payload_sha256());Ok(())}
#[test]
fn fresh_encode_revalidates_opaque_floats()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let original=codec.decode_json(named("raw-float-spellings")?.as_bytes())?;let output=codec.encode_typed(original.into_frame())?;let mut wire=Vec::new();codec.write_frame(&mut wire,&output)?;let decoded=codec.read_frame(&mut Cursor::new(wire))?.ok_or_else(||std::io::Error::other("empty synthetic frame"))?;assert_eq!(decoded.payload_sha256(),output.payload_sha256());Ok(())}
#[test]
fn binding_uses_retained_actual_hashes()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let hello=codec.decode_json(named("hello")?.as_bytes())?;let prepare=codec.decode_json(named("prepare-parent")?.as_bytes())?;let prepared=codec.decode_json(named("prepared-unexpected-observations")?.as_bytes())?;validate_prepared_binding(&prepared,&prepare,&hello)?;let changed=codec.decode_json(named("raw-whitespace")?.as_bytes())?;assert!(validate_prepared_binding(&prepared,&changed,&hello).is_err());Ok(())}
#[test]
fn old_control_rejects_new_bodies()->Result<(),Box<dyn StdError>>{for name in ["prepare-parent","prepared-unexpected-observations"]{assert_eq!(decode_json(named(name)?.as_bytes()).err(),Some(Error::UnsupportedFrame));}Ok(())}
#[test]
fn missing_every_present_required_field()->Result<(),Box<dyn StdError>>{
    let codec=AgentPrepareCodec::new();let original=decode_ordinary_json(named("prepare-parent")?.as_bytes())?;
    for key in ["binding","source_baseline","staged_closure","admission","limits","agent","prompt","tools","model_chain","provision_slot_id","loop_profile","type"]{let mut tree=original.clone();tree["body"].as_object_mut().ok_or(Error::InvalidFrame)?.remove(key);let bytes=serde_json::to_vec(&tree)?;assert_eq!(codec.decode_json(&bytes).err(),Some(Error::InvalidFrame));}Ok(())
}
#[test]
fn optional_absence_survives_fresh_encoding()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let decoded=codec.decode_json(named("prepare-solution_deployment")?.as_bytes())?;let encoded=codec.encode_typed(decoded.into_frame())?;let mut wire=Vec::new();codec.write_frame(&mut wire,&encoded)?;let tree=decode_ordinary_json(&wire[4..])?;assert!(tree["body"]["staged_closure"]["execution_evidence"]["data"].get("workflow_runtime_bounds").is_none());Ok(())}
#[test]
fn invalid_utf8()->Result<(),Box<dyn StdError>>{assert_eq!(AgentPrepareCodec::new().decode_json(&[255]).err(),Some(Error::InvalidJson));Ok(())}
#[test]
fn framing_empty_truncated_oversized()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();assert!(codec.read_frame(&mut Cursor::new([]))?.is_none());assert_eq!(codec.read_frame(&mut Cursor::new([0,0])).err(),Some(Error::TruncatedFrame));assert_eq!(codec.read_frame(&mut Cursor::new([0,0,0,0])).err(),Some(Error::InvalidFrame));let too_large=u32::try_from(MAX_FRAME_BYTES+1)?.to_be_bytes();assert_eq!(codec.read_frame(&mut Cursor::new(too_large)).err(),Some(Error::FrameTooLarge));Ok(())}
#[test]
fn direct_complete_frame_boundary()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let raw=named("prepare-parent")?;let pad=MAX_FRAME_BYTES-raw.len();let full=raw.replace("synthetic only",&format!("synthetic only{}","x".repeat(pad)));assert_eq!(full.len(),MAX_FRAME_BYTES);codec.decode_json(full.as_bytes())?;assert_eq!(codec.decode_json(format!("{full} ").as_bytes()).err(),Some(Error::FrameTooLarge));Ok(())}
#[test]
fn depth_bound()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();for depth in [63,64,65]{let raw=format!("{}0{}","[".repeat(depth),"]".repeat(depth));let result=codec.decode_json(raw.as_bytes());assert_eq!(result.err(),Some(if depth<=64{Error::InvalidFrame}else{Error::InvalidJson}));}Ok(())}
#[test]
fn business_private_markers_remain_literal()->Result<(),Box<dyn StdError>>{let codec=AgentPrepareCodec::new();let business=codec.parse_business_json(br#"{"$serde_json::private::RawValue":"literal","adjacent":{"n":1.2300e2}}"#)?;let view=business.view()?;assert_eq!(view.kind(),"object");let literal=view.field("$serde_json::private::RawValue")?.ok_or(Error::InvalidFrame)?;assert_eq!(literal.string()?,"literal");Ok(())}
#[test]
fn numeric_looking_strings_do_not_trip_preflight()->Result<(),Box<dyn StdError>>{AgentPrepareCodec::new().parse_business_json(br#"{"string":"9999999999999999999999 and \"quoted\""}"#)?;Ok(())}

#[derive(Deserialize)]
struct CoverageCorpus{coverage:Vec<Coverage>}
#[derive(Deserialize)]
struct Coverage{schema:String,seed:String,path:Vec<serde_json::Value>,rules:std::collections::BTreeMap<String,String>}
fn at_mut<'a>(tree:&'a mut serde_json::Value,path:&[serde_json::Value])->Result<&'a mut serde_json::Value,Error>{
let mut node=tree;for part in path{node=if let Some(key)=part.as_str(){node.get_mut(key).ok_or(Error::InvalidFrame)?}else{let index=part.as_u64().ok_or(Error::InvalidFrame)?;node.get_mut(usize::try_from(index).map_err(|_|Error::InvalidFrame)?).ok_or(Error::InvalidFrame)?};}Ok(node)
}
#[test]
fn every_nullable_optional_field_distinction()->Result<(),Box<dyn StdError>>{
let corpus:CoverageCorpus=serde_json::from_str(include_str!("../../tests/fixtures/runtime/v1/agent-prepare-vectors.json"))?;let codec=AgentPrepareCodec::new();assert_eq!(corpus.coverage.len(),31);
assert_eq!(corpus.coverage.iter().flat_map(|item|item.rules.values()).filter(|rule|rule.starts_with('?')).count(),30);
assert_eq!(corpus.coverage.iter().flat_map(|item|item.rules.values()).filter(|rule|rule.starts_with('~')).count(),3);
for item in corpus.coverage{for (field,rule) in item.rules{if !rule.starts_with('?')&&!rule.starts_with('~'){continue;}
let mut base=decode_ordinary_json(named(&item.seed)?.as_bytes())?;at_mut(&mut base,&item.path)?.as_object_mut().ok_or(Error::InvalidFrame)?.remove(&field);
let absent=serde_json::to_vec(&base)?;if rule.starts_with('~'){codec.decode_json(&absent)?;}else{assert_eq!(codec.decode_json(&absent).err(),Some(Error::InvalidFrame));}
at_mut(&mut base,&item.path)?.as_object_mut().ok_or(Error::InvalidFrame)?.insert(field.clone(),serde_json::Value::Null);
if item.schema=="AgentBinding"&&field=="expected_image_digest"{base["body"]["staged_closure"]["runtime_expected"]["image_digest"]=serde_json::Value::Null;}
if item.schema=="PrepareArtifact"&&field=="image_digest"{base["body"]["binding"]["expected_image_digest"]=serde_json::Value::Null;}
let present=serde_json::to_vec(&base)?;if rule.starts_with('?'){codec.decode_json(&present)?;}else{assert_eq!(codec.decode_json(&present).err(),Some(Error::InvalidFrame));}
}}Ok(())}
#[test]
fn distinct_retained_binding_owners()->Result<(),Box<dyn StdError>>{
let codec=AgentPrepareCodec::new();let other=serde_json::Value::String("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".to_owned());
for case in ["hello-message","hello-session","prepare-message","slot","prepare-hash","hello-hash"]{
let mut hello=decode_ordinary_json(named("hello")?.as_bytes())?;let mut prepare=decode_ordinary_json(named("prepare-parent")?.as_bytes())?;let mut prepared=decode_ordinary_json(named("prepared-unexpected-observations")?.as_bytes())?;
match case{"hello-message"=>hello["message_id"]=other.clone(),"hello-session"=>hello["session_id"]=other.clone(),"prepare-message"=>{prepare["message_id"]=other.clone();prepare["body"]["binding"]["prepare_message_id"]=other.clone();},"slot"=>prepared["body"]["provision_slot_id"]=other.clone(),_=>{}}
let hello_bytes=if case.starts_with("hello-"){let mut bytes=serde_json::to_vec(&hello)?;if case=="hello-hash"{bytes.push(b' ');}bytes}else{named("hello")?.into_bytes()};
let prepare_bytes=if case.starts_with("prepare-"){let mut bytes=serde_json::to_vec(&prepare)?;if case=="prepare-hash"{bytes.push(b' ');}bytes}else{named("prepare-parent")?.into_bytes()};
if case!="prepare-hash"&&case!="hello-hash"{prepared["body"]["hello_payload_sha256"]=serde_json::Value::String(format!("{:x}",Sha256::digest(&hello_bytes)));prepared["body"]["prepare_payload_sha256"]=serde_json::Value::String(format!("{:x}",Sha256::digest(&prepare_bytes)));}
let hello_owner=codec.decode_json(&hello_bytes)?;let prepare_owner=codec.decode_json(&prepare_bytes)?;let prepared_owner=codec.decode_json(&serde_json::to_vec(&prepared)?)?;
assert_eq!(validate_prepared_binding(&prepared_owner,&prepare_owner,&hello_owner).err(),Some(Error::InvalidFrame));
}Ok(())}
#[test]
fn reordered_and_business_extension_fresh_retained()->Result<(),Box<dyn StdError>>{
let codec=AgentPrepareCodec::new();for name in ["retained-reordered-prepare","fresh-business-map-extra-key"]{
let raw=named(name)?;let retained=codec.encode_retained(codec.decode_json(raw.as_bytes())?)?;let mut wire=Vec::new();codec.write_frame(&mut wire,&retained)?;assert_eq!(&wire[4..],raw.as_bytes());assert_eq!(retained.payload_sha256(),format!("{:x}",Sha256::digest(raw.as_bytes())));
let fresh=codec.encode_typed(codec.decode_json(raw.as_bytes())?.into_frame())?;let mut output=Vec::new();codec.write_frame(&mut output,&fresh)?;codec.decode_json(&output[4..])?;assert_eq!(fresh.payload_sha256(),format!("{:x}",Sha256::digest(&output[4..])));
}Ok(())}
