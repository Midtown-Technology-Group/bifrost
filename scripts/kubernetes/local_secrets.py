"""Create private credentials only for an explicitly selected local Kind cluster."""

import argparse
import json
import re
import secrets
import subprocess
from pathlib import Path

NAMESPACE = "bifrost-local"
NAME = "bifrost-local-secrets"


def document() -> dict:
    database = secrets.token_hex(32)
    broker = secrets.token_hex(32)
    storage = secrets.token_hex(32)
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": NAME, "namespace": NAMESPACE}, "stringData": {
        "POSTGRES_PASSWORD": database, "RABBITMQ_PASSWORD": broker,
        "SEAWEEDFS_SECRET_KEY": storage, "BIFROST_S3_SECRET_KEY": storage,
        "BIFROST_SECRET_KEY": secrets.token_hex(32),
        "BIFROST_DEFAULT_USER_EMAIL": "dev@gobifrost.com",
        "BIFROST_DEFAULT_USER_PASSWORD": secrets.token_hex(24),
        "BIFROST_DATABASE_URL": f"postgresql+asyncpg://bifrost:{database}@pgbouncer:5432/bifrost",
        "BIFROST_DATABASE_URL_SYNC": f"postgresql://bifrost:{database}@pgbouncer:5432/bifrost",
        "BIFROST_RABBITMQ_URL": f"amqp://bifrost:{broker}@rabbitmq.bifrost-local.svc.cluster.local:5672/",
    }}


def ensure(kubeconfig: Path, context: str) -> None:
    if not re.fullmatch(r"kind-[a-z0-9][a-z0-9-]{0,62}", context):
        raise ValueError("Credentials require an explicit local Kind context")
    if ".." in kubeconfig.parts or not kubeconfig.is_absolute():
        raise ValueError("Kubeconfig must be an absolute local file path")
    command = ["kubectl", "--kubeconfig", str(kubeconfig.resolve(strict=True)),
               "--context", context, "-n", NAMESPACE]
    result = subprocess.run([*command, "get", "secret", NAME, "--ignore-not-found", "-o", "json"],
                            capture_output=True, text=True, check=True)
    if result.stdout.strip():
        existing = json.loads(result.stdout)
        if existing.get("metadata", {}).get("name") != NAME or existing["metadata"].get("namespace") != NAMESPACE:
            raise ValueError("Unexpected local secret identity")
        data = existing.get("data", {})
        if not all(isinstance(data.get(key), str) and data[key] for key in document()["stringData"]):
            raise ValueError("Existing local secret is incomplete; inspect it without overwriting credentials")
        return
    subprocess.run([*command, "create", "-f", "-"], input=json.dumps(document()), text=True, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--context", required=True)
    args = parser.parse_args()
    ensure(args.kubeconfig, args.context)
