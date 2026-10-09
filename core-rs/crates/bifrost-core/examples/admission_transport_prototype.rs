//! Synthetic, loopback-only transport experiment. Never calls the Core bootstrap or database.
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use axum::body::to_bytes;
use axum::extract::{Extension, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use hyper_util::rt::{TokioIo, TokioTimer};
use hyper_util::service::TowerToHyperService;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, UnixTime};
use rustls::server::danger::ClientCertVerifier;
use rustls::{RootCertStore, ServerConfig};
use serde::{Deserialize, Serialize};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{oneshot, watch};
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::{Instant, timeout_at};
use tokio_rustls::TlsAcceptor;

const FIVE: Duration = Duration::from_secs(5);
const TWO: Duration = Duration::from_secs(2);
const SUITE: Duration = Duration::from_secs(180);
const BODY_LIMIT: usize = 1024;
const FILE_LIMIT: usize = 65_536;
const FIXTURE_LIMIT: usize = 1_048_576;
const MAX_FILES: usize = 32;
const MAX_CONNECTIONS: usize = 8;
static INPUT_BYTES: AtomicUsize = AtomicUsize::new(0);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Fault {
    Startup,
    Transport,
    Cleanup,
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Identity {
    dev: u64,
    ino: u64,
    uid: u32,
    mode: u32,
    size: u64,
    links: u64,
}

fn identity(metadata: &fs::Metadata) -> Identity {
    Identity {
        dev: metadata.dev(),
        ino: metadata.ino(),
        uid: metadata.uid(),
        mode: metadata.mode(),
        size: metadata.size(),
        links: metadata.nlink(),
    }
}

/// File owns its FD before validation. Explicit Drop is safe ownership disposal, not a
/// measured kernel-close-success certificate. Python independently retains its directory UID.
struct Fixtures {
    directory: PathBuf,
    directory_identity: Identity,
    files_read: usize,
    bytes_read: usize,
}

impl Fixtures {
    fn new(directory: &Path) -> Result<Self, Fault> {
        let spelling = directory.as_os_str().as_encoded_bytes();
        if !directory.is_absolute()
            || spelling.len() > 512
            || !spelling.is_ascii()
            || spelling
                .iter()
                .any(|byte| matches!(byte, 0 | b'\r' | b'\n'))
        {
            return Err(Fault::Startup);
        }
        let metadata = fs::symlink_metadata(directory).map_err(|_| Fault::Startup)?;
        if !metadata.is_dir() || metadata.mode() & 0o7777 != 0o700 {
            return Err(Fault::Startup);
        }
        for ancestor in directory.ancestors() {
            let current = fs::symlink_metadata(ancestor).map_err(|_| Fault::Startup)?;
            if !current.is_dir() {
                return Err(Fault::Startup);
            }
        }
        let mut count = 0;
        for entry in fs::read_dir(directory).map_err(|_| Fault::Startup)? {
            entry.map_err(|_| Fault::Startup)?;
            count += 1;
            if count > MAX_FILES {
                return Err(Fault::Startup);
            }
        }
        Ok(Self {
            directory: directory.to_owned(),
            directory_identity: identity(&metadata),
            files_read: 0,
            bytes_read: 0,
        })
    }

    fn read(&mut self, name: &str, private: bool) -> Result<Vec<u8>, Fault> {
        self.files_read = self.files_read.checked_add(1).ok_or(Fault::Startup)?;
        if self.files_read > MAX_FILES {
            return Err(Fault::Startup);
        }
        let current = fs::symlink_metadata(&self.directory).map_err(|_| Fault::Startup)?;
        if identity(&current) != self.directory_identity {
            return Err(Fault::Startup);
        }
        let path = self.directory.join(name);
        // Linux supported venue: NOFOLLOW | NONBLOCK | CLOEXEC. No new libc/unsafe dependency.
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(0x20000 | 0x800 | 0x80000)
            .open(&path)
            .map_err(|_| Fault::Startup)?;
        let result = (|| {
            let before = file.metadata().map_err(|_| Fault::Startup)?;
            let expected_mode = if private { 0o600 } else { 0o644 };
            if !before.is_file()
                || before.uid() != self.directory_identity.uid
                || before.nlink() != 1
                || before.mode() & 0o7777 != expected_mode
                || before.len() == 0
                || before.len() > FILE_LIMIT as u64
                || identity(&fs::symlink_metadata(&path).map_err(|_| Fault::Startup)?)
                    != identity(&before)
            {
                return Err(Fault::Startup);
            }
            let mut bytes = Vec::new();
            let mut chunk = [0_u8; 4096];
            loop {
                let count = file.read(&mut chunk).map_err(|_| Fault::Startup)?;
                if count == 0 {
                    break;
                }
                self.bytes_read = self.bytes_read.checked_add(count).ok_or(Fault::Startup)?;
                let total = INPUT_BYTES.fetch_add(count, Ordering::Relaxed);
                if total > FIXTURE_LIMIT.saturating_sub(count)
                    || bytes.len() > FILE_LIMIT.saturating_sub(count)
                {
                    return Err(Fault::Startup);
                }
                bytes.extend_from_slice(&chunk[..count]);
            }
            if bytes.len() != before.len() as usize
                || bytes.len() > FILE_LIMIT
                || self.bytes_read > FIXTURE_LIMIT
                || identity(&file.metadata().map_err(|_| Fault::Startup)?) != identity(&before)
                || identity(&fs::symlink_metadata(&path).map_err(|_| Fault::Startup)?)
                    != identity(&before)
                || identity(&fs::symlink_metadata(&self.directory).map_err(|_| Fault::Startup)?)
                    != self.directory_identity
            {
                return Err(Fault::Startup);
            }
            Ok(bytes)
        })();
        drop(file);
        result
    }
}

#[derive(Clone)]
struct Peer {
    certificates: Vec<CertificateDer<'static>>,
    end: Instant,
}

struct Policy {
    generation: u64,
    allowed: Vec<CertificateDer<'static>>,
}

struct Admission {
    verifier: Arc<dyn ClientCertVerifier>,
    policy: RwLock<Policy>,
}

impl Admission {
    #[cfg(test)]
    fn replace(&self, generation: u64, allowed: Vec<CertificateDer<'static>>) -> Result<(), Fault> {
        if allowed.len() > 2
            || allowed
                .iter()
                .any(|leaf| leaf.is_empty() || leaf.len() > 16_384)
        {
            return Err(Fault::Startup);
        }
        let mut policy = self.policy.write().map_err(|_| Fault::Transport)?;
        if generation <= policy.generation {
            return Err(Fault::Startup);
        }
        policy.generation = generation;
        policy.allowed = allowed;
        Ok(())
    }

    fn admit(
        &self,
        peer: &Peer,
        operation: &str,
        request_id: String,
    ) -> Result<ProbeResponse, StatusCode> {
        let policy = self
            .policy
            .read()
            .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
        let Some(leaf) = peer.certificates.first() else {
            return Err(StatusCode::FORBIDDEN);
        };
        if policy.generation == 0
            || Instant::now() >= peer.end
            || !policy.allowed.iter().any(|allowed| allowed == leaf)
            || self
                .verifier
                .verify_client_cert(leaf, &peer.certificates[1..], UnixTime::now())
                .is_err()
        {
            return Err(StatusCode::FORBIDDEN);
        }
        if Instant::now() >= peer.end {
            return Err(StatusCode::FORBIDDEN);
        }
        // The read guard is the admission linearization point; no await or business effect.
        Ok(ProbeResponse {
            schema: "bifrost.test.m1-transport-probe-response/v1".to_owned(),
            operation: operation.to_owned(),
            request_id,
        })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeRequest {
    schema: String,
    request_id: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeResponse {
    schema: String,
    operation: String,
    request_id: String,
}

async fn probe(
    State(admission): State<Arc<Admission>>,
    Extension(peer): Extension<Peer>,
    request: Request,
    operation: &'static str,
) -> Response {
    let end = (Instant::now() + FIVE).min(peer.end);
    let body = match timeout_at(end, to_bytes(request.into_body(), BODY_LIMIT)).await {
        Ok(Ok(body)) => body,
        _ => return StatusCode::BAD_REQUEST.into_response(),
    };
    let input = match Json::<ProbeRequest>::from_bytes(&body) {
        Ok(Json(input)) => input,
        Err(_) => return StatusCode::BAD_REQUEST.into_response(),
    };
    if input.schema != "bifrost.test.m1-transport-probe/v1"
        || input.request_id.len() != 64
        || !input
            .request_id
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || Instant::now() >= end
    {
        return StatusCode::BAD_REQUEST.into_response();
    }
    match admission.admit(&peer, operation, input.request_id) {
        Ok(response) => Json(response).into_response(),
        Err(status) => status.into_response(),
    }
}

async fn workflow(
    state: State<Arc<Admission>>,
    peer: Extension<Peer>,
    request: Request,
) -> Response {
    probe(state, peer, request, "workflow_probe").await
}

async fn agent(state: State<Arc<Admission>>, peer: Extension<Peer>, request: Request) -> Response {
    probe(state, peer, request, "agent_probe").await
}

struct Server {
    address: SocketAddr,
    admission: Arc<Admission>,
    stop: Option<oneshot::Sender<Instant>>,
    task: JoinHandle<Result<(), Fault>>,
    suite_end: Instant,
}

fn configuration(fixtures: &mut Fixtures) -> Result<(Arc<ServerConfig>, Arc<Admission>), Fault> {
    let server = CertificateDer::from(fixtures.read("server.der", false)?);
    let key = PrivateKeyDer::try_from(fixtures.read("server-key.der", true)?)
        .map_err(|_| Fault::Startup)?;
    let ca = CertificateDer::from(fixtures.read("ca.der", false)?);
    let a = CertificateDer::from(fixtures.read("client-a.der", false)?);
    let b = CertificateDer::from(fixtures.read("client-b.der", false)?);
    if [&server, &ca, &a, &b]
        .iter()
        .any(|cert| cert.len() > 16_384)
    {
        return Err(Fault::Startup);
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let mut roots = RootCertStore::empty();
    roots.add(ca).map_err(|_| Fault::Startup)?;
    let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
        Arc::new(roots),
        Arc::clone(&provider),
    )
    .build()
    .map_err(|_| Fault::Startup)?;
    let mut config = ServerConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
        .map_err(|_| Fault::Startup)?
        .with_client_cert_verifier(Arc::clone(&verifier))
        .with_single_cert(vec![server], key)
        .map_err(|_| Fault::Startup)?;
    config.session_storage = Arc::new(rustls::server::NoServerSessionStorage {});
    config.send_tls13_tickets = 0;
    let admission = Arc::new(Admission {
        verifier,
        policy: RwLock::new(Policy {
            generation: 1,
            allowed: vec![a, b],
        }),
    });
    Ok((Arc::new(config), admission))
}

async fn connection(
    stream: TcpStream,
    acceptor: TlsAcceptor,
    admission: Arc<Admission>,
    end: Instant,
    mut shutdown: watch::Receiver<bool>,
) -> Result<(), Fault> {
    let tls = match acceptor.accept(stream).await {
        Ok(tls) => tls,
        // Native handshake denial closes this owned stream; no favorable admission is recorded.
        Err(_) => return Ok(()),
    };
    let chain = tls
        .get_ref()
        .1
        .peer_certificates()
        .ok_or(Fault::Transport)?;
    if chain.is_empty()
        || chain.len() > 4
        || chain.iter().any(|cert| cert.len() > 16_384)
        || chain.iter().map(|cert| cert.len()).sum::<usize>() > 65_536
    {
        return Err(Fault::Transport);
    }
    let peer = Peer {
        certificates: chain.to_vec(),
        end,
    };
    let router = Router::new()
        .route("/_prototype/v1/workflow_probe", post(workflow))
        .route("/_prototype/v1/agent_probe", post(agent))
        .layer(Extension(peer))
        .with_state(admission);
    let mut builder = hyper::server::conn::http1::Builder::new();
    builder
        .timer(TokioTimer::new())
        .header_read_timeout(FIVE)
        .max_headers(32)
        .max_buf_size(8192);
    let serving = builder.serve_connection(TokioIo::new(tls), TowerToHyperService::new(router));
    tokio::pin!(serving);
    tokio::select! {
        result = &mut serving => result.map_err(|_| Fault::Transport),
        changed = shutdown.changed() => {
            changed.map_err(|_| Fault::Cleanup)?;
            serving.as_mut().graceful_shutdown();
            serving.await.map_err(|_| Fault::Transport)
        },
    }
}

impl Server {
    async fn start(directory: &Path, suite_end: Instant) -> Result<Self, Fault> {
        let mut fixtures = Fixtures::new(directory)?;
        let (config, admission) = configuration(&mut fixtures)?;
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .await
            .map_err(|_| Fault::Startup)?;
        let address = listener.local_addr().map_err(|_| Fault::Startup)?;
        let (stop, mut stopped) = oneshot::channel::<Instant>();
        let state = Arc::clone(&admission);
        let task = tokio::spawn(async move {
            let mut tasks = JoinSet::new();
            let (shutdown, shutdown_receiver) = watch::channel(false);
            let mut first = None;
            let mut cleanup_end = suite_end;
            loop {
                tokio::select! {
                    stopped = &mut stopped => {
                        match stopped {
                            Ok(end) => cleanup_end = end.min(suite_end),
                            Err(_) => { first.get_or_insert(Fault::Cleanup); },
                        }
                        break;
                    },
                    _ = tokio::time::sleep_until(suite_end) => {
                        first.get_or_insert(Fault::Transport);
                        break;
                    },
                    joined = tasks.join_next(), if !tasks.is_empty() => {
                        if !matches!(joined, Some(Ok(Ok(())))) {
                            first.get_or_insert(Fault::Transport);
                        }
                    },
                    accepted = listener.accept() => {
                        match accepted {
                            Ok((stream, _)) => {
                                if tasks.len() >= MAX_CONNECTIONS {
                                    drop(stream);
                                    continue;
                                }
                                let shutdown = shutdown_receiver.clone();
                                let end = (Instant::now() + FIVE).min(suite_end);
                                let acceptor = TlsAcceptor::from(Arc::clone(&config));
                                let admission = Arc::clone(&state);
                                tasks.spawn(async move {
                                    // Expiry drops the actual handshake/connection future and owned stream.
                                    match timeout_at(end, connection(stream, acceptor, admission, end, shutdown)).await {
                                        Ok(result) => result,
                                        Err(_) => Ok(()),
                                    }
                                });
                            },
                            Err(_) => {
                                first.get_or_insert(Fault::Transport);
                                break;
                            },
                        }
                    },
                }
            }
            drop(listener);
            let _ = shutdown.send(true);
            let drain_end = (Instant::now() + TWO).min(cleanup_end);
            while !tasks.is_empty() {
                match timeout_at(drain_end, tasks.join_next()).await {
                    Ok(Some(Ok(Ok(())))) => {}
                    Ok(Some(_)) => {
                        first.get_or_insert(Fault::Transport);
                    }
                    Ok(None) => break,
                    Err(_) => break,
                }
            }
            tasks.abort_all();
            while !tasks.is_empty() {
                match timeout_at(cleanup_end, tasks.join_next()).await {
                    Ok(Some(Err(error))) if error.is_cancelled() => {}
                    Ok(Some(Ok(Ok(())))) => {}
                    Ok(Some(_)) => {
                        first.get_or_insert(Fault::Cleanup);
                    }
                    Ok(None) => break,
                    Err(_) => {
                        first.get_or_insert(Fault::Cleanup);
                        break;
                    }
                }
            }
            first.map_or(Ok(()), Err)
        });
        Ok(Self {
            address,
            admission,
            stop: Some(stop),
            task,
            suite_end,
        })
    }

    async fn close(mut self, end: Instant) -> Result<(), Fault> {
        let end = end.min(self.suite_end);
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(end);
        }
        match timeout_at(end, &mut self.task).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(Fault::Cleanup),
            Err(_) => {
                self.task.abort();
                let _ = timeout_at(end, &mut self.task).await;
                Err(Fault::Cleanup)
            }
        }
    }
}

async fn run() -> Result<(), Fault> {
    let suite_end = Instant::now() + SUITE;
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() != 3 || args[1] != "--fixture-dir" {
        return Err(Fault::Startup);
    }
    let server = Server::start(Path::new(&args[2]), suite_end).await?;
    // This example has no production policy administration. Tests use the same owned Arc.
    let _admission_owner = &server.admission;
    let ready = format!(
        "{{\"schema\":\"bifrost.test.m1-transport-ready/v1\",\"port\":{}}}\n",
        server.address.port()
    );
    let mut first = None;
    let mut signal = match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
    {
        Ok(signal) => Some(signal),
        Err(_) => {
            first = Some(Fault::Startup);
            None
        }
    };
    if first.is_none()
        && (ready.len() > 256 || std::io::stdout().write_all(ready.as_bytes()).is_err())
    {
        first = Some(Fault::Startup);
    }
    if first.is_none()
        && let Some(signal) = signal.as_mut()
        && timeout_at(suite_end, signal.recv()).await.is_err()
    {
        first = Some(Fault::Transport);
    }
    let shutdown_end = (Instant::now() + FIVE).min(suite_end);
    if let Err(error) = server.close(shutdown_end).await {
        first.get_or_insert(error);
    }
    first.map_or(Ok(()), Err)
}

fn main() {
    let result = match tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime.block_on(run()),
        Err(_) => Err(Fault::Startup),
    };
    if let Err(error) = result {
        let label = match error {
            Fault::Startup => "startup_failed\n",
            Fault::Transport => "transport_failed\n",
            Fault::Cleanup => "cleanup_failed\n",
        };
        let _ = std::io::stderr().write_all(label.as_bytes());
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request as HttpRequest;
    use http_body_util::{BodyExt, Limited};
    use hyper::client::conn::http1::SendRequest;
    use rustls::pki_types::ServerName;
    use tokio_rustls::TlsConnector;

    struct Client {
        sender: Option<SendRequest<Body>>,
        driver: JoinHandle<Result<(), hyper::Error>>,
        end: Instant,
    }

    impl Client {
        async fn connect(
            directory: &Path,
            address: SocketAddr,
            suite_end: Instant,
        ) -> Result<Self, Fault> {
            let end = (Instant::now() + FIVE).min(suite_end);
            let mut fixtures = Fixtures::new(directory)?;
            let mut roots = RootCertStore::empty();
            roots
                .add(CertificateDer::from(fixtures.read("ca.der", false)?))
                .map_err(|_| Fault::Startup)?;
            let certificate = CertificateDer::from(fixtures.read("client-a.der", false)?);
            let key = PrivateKeyDer::try_from(fixtures.read("client-a-key.der", true)?)
                .map_err(|_| Fault::Startup)?;
            let provider = Arc::new(rustls::crypto::ring::default_provider());
            let mut config = rustls::ClientConfig::builder_with_provider(provider)
                .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
                .map_err(|_| Fault::Startup)?
                .with_root_certificates(roots)
                .with_client_auth_cert(vec![certificate], key)
                .map_err(|_| Fault::Startup)?;
            config.resumption = rustls::client::Resumption::disabled();
            let name = ServerName::try_from("localhost").map_err(|_| Fault::Startup)?;
            let connector = TlsConnector::from(Arc::new(config));
            let established = timeout_at(end, async {
                let stream = TcpStream::connect(address)
                    .await
                    .map_err(|_| Fault::Transport)?;
                let tls = connector
                    .connect(name, stream)
                    .await
                    .map_err(|_| Fault::Transport)?;
                hyper::client::conn::http1::handshake::<_, Body>(TokioIo::new(tls))
                    .await
                    .map_err(|_| Fault::Transport)
            })
            .await
            .map_err(|_| Fault::Transport)??;
            let (sender, connection) = established;
            let driver = tokio::spawn(connection);
            Ok(Self {
                sender: Some(sender),
                driver,
                end,
            })
        }

        async fn request(
            &mut self,
            path: &str,
            body: &str,
        ) -> Result<(StatusCode, Vec<u8>), Fault> {
            let end = (Instant::now() + FIVE).min(self.end);
            let request = HttpRequest::builder()
                .method("POST")
                .uri(path)
                .header("host", "localhost")
                .header("content-type", "application/json")
                .body(Body::from(body.to_owned()))
                .map_err(|_| Fault::Transport)?;
            let sender = self.sender.as_mut().ok_or(Fault::Transport)?;
            timeout_at(end, async {
                let response = sender
                    .send_request(request)
                    .await
                    .map_err(|_| Fault::Transport)?;
                let status = response.status();
                let body = Limited::new(response.into_body(), BODY_LIMIT)
                    .collect()
                    .await
                    .map_err(|_| Fault::Transport)?;
                if Instant::now() >= end {
                    return Err(Fault::Transport);
                }
                Ok((status, body.to_bytes().to_vec()))
            })
            .await
            .map_err(|_| Fault::Transport)?
        }

        async fn close(mut self, cleanup_end: Instant) -> Result<(), Fault> {
            drop(self.sender.take());
            match timeout_at(cleanup_end, &mut self.driver).await {
                Ok(Ok(Ok(()))) => Ok(()),
                Ok(_) => Err(Fault::Cleanup),
                Err(_) => {
                    self.driver.abort();
                    let joined = timeout_at(cleanup_end, &mut self.driver).await;
                    if !matches!(joined, Ok(Err(error)) if error.is_cancelled()) {
                        return Err(Fault::Cleanup);
                    }
                    Err(Fault::Cleanup)
                }
            }
        }
    }

    struct World {
        server: Option<Server>,
        client: Option<Client>,
        suite_end: Instant,
    }

    impl World {
        fn new() -> Self {
            Self {
                server: None,
                client: None,
                suite_end: Instant::now() + SUITE,
            }
        }

        async fn start(&mut self, directory: &Path) -> Result<(), Fault> {
            self.server = Some(Server::start(directory, self.suite_end).await?);
            let address = self.server.as_ref().ok_or(Fault::Startup)?.address;
            self.client = Some(Client::connect(directory, address, self.suite_end).await?);
            Ok(())
        }

        async fn finish(mut self, result: Result<(), Fault>) -> Result<(), Fault> {
            let cleanup_end = (Instant::now() + FIVE).min(self.suite_end);
            let mut first = result.err();
            if let Some(client) = self.client.take()
                && let Err(error) = client.close(cleanup_end).await
            {
                first.get_or_insert(error);
            }
            if let Some(server) = self.server.take() {
                if let Err(error) = server.close(cleanup_end).await {
                    first.get_or_insert(error);
                }
            }
            first.map_or(Ok(()), Err)
        }

        fn client(&mut self) -> Result<&mut Client, Fault> {
            self.client.as_mut().ok_or(Fault::Transport)
        }
    }

    fn directory() -> Result<PathBuf, Fault> {
        std::env::var_os("M1_PROTOCOL_FIXTURE_DIR")
            .map(PathBuf::from)
            .ok_or(Fault::Startup)
    }

    fn valid_body() -> String {
        format!(
            "{{\"schema\":\"bifrost.test.m1-transport-probe/v1\",\"request_id\":\"{}\"}}",
            "a".repeat(64)
        )
    }

    fn check(value: bool) -> Result<(), Fault> {
        if value { Ok(()) } else { Err(Fault::Transport) }
    }

    #[tokio::test]
    async fn both_inert_probes_exact_response() -> Result<(), Fault> {
        let mut world = World::new();
        let result = async {
            world.start(&directory()?).await?;
            for operation in ["workflow_probe", "agent_probe"] {
                let path = format!("/_prototype/v1/{operation}");
                let (status, body) = world.client()?.request(&path, &valid_body()).await?;
                check(status == StatusCode::OK)?;
                let Json(response) =
                    Json::<ProbeResponse>::from_bytes(&body).map_err(|_| Fault::Transport)?;
                check(
                    response.schema == "bifrost.test.m1-transport-probe-response/v1"
                        && response.operation == operation
                        && response.request_id == "a".repeat(64),
                )?;
            }
            Ok(())
        }
        .await;
        world.finish(result).await
    }

    #[tokio::test]
    async fn same_connection_rotation_and_generation_rejection() -> Result<(), Fault> {
        let mut world = World::new();
        let result = async {
            let directory = directory()?;
            world.start(&directory).await?;
            let (status, _) = world
                .client()?
                .request("/_prototype/v1/workflow_probe", &valid_body())
                .await?;
            check(status == StatusCode::OK)?;
            let admission = Arc::clone(&world.server.as_ref().ok_or(Fault::Startup)?.admission);
            let mut fixtures = Fixtures::new(&directory)?;
            let a = CertificateDer::from(fixtures.read("client-a.der", false)?);
            let b = CertificateDer::from(fixtures.read("client-b.der", false)?);
            check(admission.replace(1, vec![a.clone()]) == Err(Fault::Startup))?;
            admission.replace(2, vec![a, b.clone()])?;
            let (overlap, _) = world
                .client()?
                .request("/_prototype/v1/agent_probe", &valid_body())
                .await?;
            check(overlap == StatusCode::OK)?;
            admission.replace(3, vec![b])?;
            let (revoked, _) = world
                .client()?
                .request("/_prototype/v1/agent_probe", &valid_body())
                .await?;
            check(revoked == StatusCode::FORBIDDEN)?;
            Ok(())
        }
        .await;
        world.finish(result).await
    }

    #[tokio::test]
    async fn same_connection_real_certificate_expiry() -> Result<(), Fault> {
        let mut world = World::new();
        let result = async {
            let directory = directory()?;
            world.start(&directory).await?;
            let (initial, _) = world
                .client()?
                .request("/_prototype/v1/workflow_probe", &valid_body())
                .await?;
            check(initial == StatusCode::OK)?;
            let mut fixtures = Fixtures::new(&directory)?;
            let expiry_bytes = fixtures.read("client-a-expiry.txt", false)?;
            let expiry_text = std::str::from_utf8(&expiry_bytes).map_err(|_| Fault::Startup)?;
            check(
                expiry_text.len() <= 20 && expiry_text.bytes().all(|byte| byte.is_ascii_digit()),
            )?;
            let expiry: u64 = expiry_text.parse().map_err(|_| Fault::Startup)?;
            let end = world.client()?.end;
            timeout_at(end, async {
                while UnixTime::now().as_secs() <= expiry {
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
            })
            .await
            .map_err(|_| Fault::Transport)?;
            let (expired, _) = world
                .client()?
                .request("/_prototype/v1/workflow_probe", &valid_body())
                .await?;
            check(expired == StatusCode::FORBIDDEN)?;
            Ok(())
        }
        .await;
        world.finish(result).await
    }

    #[tokio::test]
    async fn strict_request_and_route_boundaries() -> Result<(), Fault> {
        let mut world = World::new();
        let result = async {
            let directory = directory()?;
            world.start(&directory).await?;
            let mut files = Fixtures::new(&directory)?;
            check(files.read("client-a-key.der", false) == Err(Fault::Startup))?;
            check(files.read("missing.der", false) == Err(Fault::Startup))?;
            let leaf = CertificateDer::from(files.read("client-a.der", false)?);
            let admission = Arc::clone(&world.server.as_ref().ok_or(Fault::Startup)?.admission);
            let expired_end = Peer {
                certificates: vec![leaf],
                end: Instant::now(),
            };
            check(matches!(
                admission.admit(&expired_end, "workflow_probe", "a".repeat(64)),
                Err(StatusCode::FORBIDDEN)
            ))?;
            let absent_chain = Peer {
                certificates: Vec::new(),
                end: Instant::now() + FIVE,
            };
            check(matches!(
                admission.admit(&absent_chain, "workflow_probe", "a".repeat(64)),
                Err(StatusCode::FORBIDDEN)
            ))?;
            let duplicate = format!("{{\"schema\":\"bifrost.test.m1-transport-probe/v1\",\"schema\":\"bifrost.test.m1-transport-probe/v1\",\"request_id\":\"{}\"}}", "a".repeat(64));
            for body in [
                duplicate,
                "{}".to_owned(),
                "{\"schema\":true,\"request_id\":null}".to_owned(),
                valid_body().replace(&"a".repeat(64)[..], &"A".repeat(64)),
                format!("{}{}", valid_body(), " ".repeat(1025)),
            ] {
                let (status, _) = world.client()?.request("/_prototype/v1/workflow_probe", &body).await?;
                check(status == StatusCode::BAD_REQUEST)?;
            }
            let (unknown, _) = world.client()?.request("/_prototype/v1/unknown", &valid_body()).await?;
            check(unknown == StatusCode::NOT_FOUND)?;
            Ok(())
        }
        .await;
        world.finish(result).await
    }
}
