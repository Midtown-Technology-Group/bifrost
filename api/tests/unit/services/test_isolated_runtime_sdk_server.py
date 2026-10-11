"""Real private TLS transport/drain, with mocked Rust admission only."""

import os
import ssl
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.services.isolated_runtime_sdk_server import IsolatedSDKServer
from tests.unit.services.test_isolated_runtime_sdk_http import app_inputs


def certificate_pair(root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert, private = root / "certificate.pem", root / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    return cert, private


async def test_private_tls_ingress_drains_and_cannot_restart(monkeypatch):
    app, token, body, gate, fetch, _ = app_inputs(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="isdk-", dir="/tmp") as directory:
        root = Path(directory)
        cert, private = certificate_pair(root)
        socket_dir = root / "sdk"
        socket_dir.mkdir(mode=0o700)
        uid = os.geteuid() or 1001
        os.chown(socket_dir, uid, -1)
        server = IsolatedSDKServer(
            app,
            directory=socket_dir,
            socket_uid=uid,
            certificate=cert,
            private_key=private,
        )
        await server.start()
        try:
            mode = server.path.lstat()
            assert mode.st_uid == uid and stat.S_IMODE(mode.st_mode) == 0o600
            assert list(socket_dir.iterdir()) == [server.path]
            transport = httpx.AsyncHTTPTransport(
                uds=str(server.path),
                verify=ssl.create_default_context(cafile=str(cert)),
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="https://localhost"
            ) as client:
                response = await client.post(
                    "/api/sdk/integrations/get",
                    content=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert response.status_code == 200 and response.json()["config"] == {
                "ready": True
            }
            gate.assert_awaited_once()
            fetch.assert_awaited_once()
        finally:
            await server.stop()
        assert not server.path.exists()
        await server.stop()
        with pytest.raises(RuntimeError, match="cannot restart"):
            await server.start()


async def test_replaced_socket_is_preserved_and_cleanup_fails(monkeypatch):
    app, _, _, _, _, _ = app_inputs(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="isdk-", dir="/tmp") as directory:
        root = Path(directory)
        cert, private = certificate_pair(root)
        socket_dir = root / "sdk"
        socket_dir.mkdir(mode=0o700)
        uid = os.geteuid() or 1001
        os.chown(socket_dir, uid, -1)
        server = IsolatedSDKServer(
            app,
            directory=socket_dir,
            socket_uid=uid,
            certificate=cert,
            private_key=private,
        )
        await server.start()
        server.path.unlink()
        server.path.write_bytes(b"replacement must not be removed")
        with pytest.raises(RuntimeError, match="cleanup custody changed"):
            await server.stop()
        assert server.path.read_bytes() == b"replacement must not be removed"


async def test_broad_application_cannot_be_hosted_on_runtime_socket(monkeypatch):
    app, _, _, _, _, _ = app_inputs(monkeypatch)
    app.get("/auth/refresh")(lambda: {})
    with tempfile.TemporaryDirectory(prefix="isdk-", dir="/tmp") as directory:
        root = Path(directory)
        cert, private = certificate_pair(root)
        socket_dir = root / "sdk"
        socket_dir.mkdir(mode=0o700)
        uid = os.geteuid() or 1001
        os.chown(socket_dir, uid, -1)
        server = IsolatedSDKServer(
            app,
            directory=socket_dir,
            socket_uid=uid,
            certificate=cert,
            private_key=private,
        )
        with pytest.raises(RuntimeError, match="custody denied"):
            await server.start()
        assert not server.path.exists()
