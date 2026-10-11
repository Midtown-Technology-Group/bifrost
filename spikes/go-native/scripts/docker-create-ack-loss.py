"""First-party hosted fixture: one actual Docker create reply is withheld.

No tenant socket, general daemon proxy, SDK or lifecycle authority is provided.
"""

import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import sys
import threading
from urllib.parse import parse_qs, urlsplit


def publish(path, value):
    with path.open("x") as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class DaemonConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect("/var/run/docker.sock")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.relay()

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def relay(self):
        target = urlsplit(self.path)
        create = self.command == "POST" and re.fullmatch(
            r"/(?:v[0-9]+\.[0-9]+/)?containers/create", target.path
        )
        observe = self.command in ("HEAD", "GET") and target.path in (
            "/_ping",
            "/version",
        )
        if not create and not observe:
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 65536 or "Transfer-Encoding" in self.headers:
            self.send_error(400)
            return
        body = self.rfile.read(length)
        spec = self.server.spec
        if create:
            payload = json.loads(body)
            if (
                parse_qs(target.query)
                != {"name": ["bifrost-guardian-" + spec["session_id"]]}
                or payload.get("Image") != spec["image_id"]
                or payload.get("Labels")
                != {
                    "bifrost.isolated.guardian.nonce": spec["nonce"],
                    "bifrost.isolated.guardian.session": spec["session_id"],
                }
            ):
                self.send_error(403)
                return
            with self.server.create_lock:
                self.server.create_requests += 1
                publish(
                    Path(spec["journal"])
                    / f"docker-create-request-{self.server.create_requests}.json",
                    {
                        "session_id": spec["session_id"],
                        "request_number": self.server.create_requests,
                    },
                )
                if self.server.create_requests != 1:
                    self.send_error(409)
                    return
        upstream = DaemonConnection("localhost", timeout=5)
        try:
            headers = {"Connection": "close"}
            if self.headers.get("Content-Type"):
                headers["Content-Type"] = self.headers["Content-Type"]
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError("oversized daemon reply")
            if create:
                container = json.loads(raw).get("Id")
                if response.status != 201 or not re.fullmatch(
                    r"[a-f0-9]{64}", container or ""
                ):
                    raise ValueError("create was not accepted")
                publish(
                    Path(spec["journal"]) / "docker-create-ack-dropped.json",
                    {
                        "event": "docker_create_ack_dropped",
                        "session_id": spec["session_id"],
                        "nonce": spec["nonce"],
                        "container_id": container,
                        "server_status": response.status,
                        "request_sha256": hashlib.sha256(body).hexdigest(),
                        "server_create_observed": True,
                        "client_reply_forwarded": False,
                        "create_requests": self.server.create_requests,
                    },
                )
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in (
                    "content-length",
                    "connection",
                    "transfer-encoding",
                ):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
            self.close_connection = True
        finally:
            upstream.close()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    if os.getuid() == 0 or os.environ.get("BIFROST_ISOLATED_DOCKER_ACK_TEST") != "1":
        raise ValueError("isolated nonroot fixture required")
    os.umask(0o077)
    spec = json.loads(Path(sys.argv[1]).read_text())
    root = Path("/tmp") / ("bifrost-create-ack-" + spec["session_id"])
    if not re.fullmatch(r"[a-f0-9-]{36}", spec["session_id"]):
        raise ValueError("invalid session")
    with Server(str(root / "ack.sock"), Handler) as server:
        os.chmod(root / "ack.sock", 0o600)
        server.spec = spec
        server.create_requests = 0
        server.create_lock = threading.Lock()
        publish(
            Path(spec["journal"]) / "docker-create-ack-proxy-ready.json",
            {
                "event": "docker_create_ack_proxy_ready",
                "session_id": spec["session_id"],
            },
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
