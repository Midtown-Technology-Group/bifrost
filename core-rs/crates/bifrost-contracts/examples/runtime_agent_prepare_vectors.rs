//! Bounded synthetic peer interchange; never a runtime or authority endpoint.
use std::{collections::BTreeMap,error::Error,io::{self,Read,Write,Cursor}};
use bifrost_contracts::runtime::{AgentPrepareCodec,validate_prepared_binding};
use serde::{Deserialize,Serialize};
const LIMIT:u64=1024*1024;
const PROFILE:&str="agent_prepare_profile/v1";
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixture{schema:String,synthetic:bool,wire:Vec<Vector>,binding:Binding,coverage:Vec<Coverage>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Coverage{schema:String,seed:String,path:Vec<CoveragePath>,rules:BTreeMap<String,String>,nonnull_field:String}
#[derive(Deserialize)]
#[serde(untagged)]
enum CoveragePath{Key(String),Index(u64)}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Vector{name:String,json:String,expected:String}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Binding{hello:String,prepare:String,prepared:String}
#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Exchange{profile:String,frames:Vec<Encoded>}
#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Encoded{name:String,frame_hex:String}
fn invalid()->io::Error{io::Error::other("invalid synthetic agent interchange")}
fn fixture()->Result<Fixture,Box<dyn Error>>{
    let text=include_str!("../tests/fixtures/runtime/v1/agent-prepare-vectors.json");
    if text.len()>LIMIT as usize{return Err(invalid().into());}
    let f:Fixture=serde_json::from_str(text)?;
    if f.schema!="bifrost.test.agent-prepare-vectors/v1"||!f.synthetic||f.wire.len()>256{return Err(invalid().into());}
    if f.coverage.len()!=31{return Err(invalid().into());}
    let mut schemas=std::collections::BTreeSet::new();
    for item in &f.coverage{
        if !schemas.insert(&item.schema)||item.schema.is_empty()||item.rules.is_empty()
            ||!f.wire.iter().any(|vector|vector.name==item.seed&&vector.expected=="ok")
            ||(item.nonnull_field!="$object"&&!item.rules.contains_key(&item.nonnull_field))
            ||item.path.is_empty(){return Err(invalid().into());}
        for part in &item.path{match part{
            CoveragePath::Key(key)=>{if key.is_empty(){return Err(invalid().into());}},
            CoveragePath::Index(index)=>{usize::try_from(*index).map_err(|_|invalid())?;},
        }}
    }
    Ok(f)
}
fn hex(bytes:&[u8])->String{bytes.iter().map(|b|format!("{b:02x}")).collect()}
fn unhex(value:&str)->Result<Vec<u8>,io::Error>{
    if value.len()%2!=0||value.len()>2*LIMIT as usize||!value.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)){return Err(invalid());}
    value.as_bytes().chunks_exact(2).map(|p|u8::from_str_radix(std::str::from_utf8(p).map_err(|_|invalid())?,16).map_err(|_|invalid())).collect()
}
fn run()->Result<(),Box<dyn Error>>{
    let mut arguments=std::env::args_os().skip(1);
    let mode=arguments.next().and_then(|v|v.into_string().ok()).ok_or_else(invalid)?;
    if arguments.next().is_some(){return Err(invalid().into());}
    let f=fixture()?;let codec=AgentPrepareCodec::new();let mut positive=BTreeMap::new();
    for v in f.wire{
        let result=codec.decode_json(v.json.as_bytes());
        if v.expected=="ok"{positive.insert(v.name,result?);}else{
            let actual=result.err().ok_or_else(invalid)?;
            if format!("{actual:?}")!=v.expected{return Err(invalid().into());}
        }
    }
    let hello=positive.get(&f.binding.hello).ok_or_else(invalid)?;
    let prepare=positive.get(&f.binding.prepare).ok_or_else(invalid)?;
    let prepared=positive.get(&f.binding.prepared).ok_or_else(invalid)?;
    validate_prepared_binding(prepared,prepare,hello)?;
    if mode=="emit"{
        let mut frames=Vec::new();
        for (name,decoded) in positive{let encoded=codec.encode_retained(decoded)?;let mut bytes=Vec::new();codec.write_frame(&mut bytes,&encoded)?;frames.push(Encoded{name,frame_hex:hex(&bytes)});}
        let bytes=serde_json::to_vec(&Exchange{profile:PROFILE.to_owned(),frames})?;
        if bytes.len()>LIMIT as usize{return Err(invalid().into());}io::stdout().write_all(&bytes)?;
    }else if mode=="validate"{
        let mut bytes=Vec::new();io::stdin().take(LIMIT+1).read_to_end(&mut bytes)?;
        if bytes.len()>LIMIT as usize{return Err(invalid().into());}
        let exchange:Exchange=serde_json::from_slice(&bytes)?;
        if exchange.profile!=PROFILE||exchange.frames.len()!=positive.len(){return Err(invalid().into());}
        for item in exchange.frames{let expected=positive.remove(&item.name).ok_or_else(invalid)?;let original=unhex(&item.frame_hex)?;let mut input=Cursor::new(&original);let decoded=codec.read_frame(&mut input)?.ok_or_else(invalid)?;
            if input.position()!=u64::try_from(original.len())?||decoded.payload_sha256()!=expected.payload_sha256(){return Err(invalid().into());}
            let retained=codec.encode_retained(decoded)?;let mut reemit=Vec::new();codec.write_frame(&mut reemit,&retained)?;if reemit!=original{return Err(invalid().into());}
        }
        if !positive.is_empty(){return Err(invalid().into());}
    }else{return Err(invalid().into());}Ok(())
}

fn main()->Result<(),io::Error>{run().map_err(|_|invalid())}
