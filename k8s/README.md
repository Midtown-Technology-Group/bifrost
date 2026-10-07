# Bifrost Kubernetes Deployment

This directory contains Kubernetes manifests for deploying Bifrost to a Kubernetes cluster.

## Prerequisites

Before deploying Bifrost, you need:

1. **PostgreSQL Database** - Use a managed service (RDS, Cloud SQL, Azure Database)
2. **Object Storage** - S3-compatible storage (AWS S3, GCS, or self-hosted S3-compatible storage such as SeaweedFS), or Azure Blob Storage via the native `azure_blob` object-storage provider (`BIFROST_OBJECT_STORAGE_PROVIDER=azure_blob`). Azure Blob Storage does not expose an S3 API, so it is not configured through the S3 endpoint settings.

## Directory Structure

```
k8s/
├── kustomization.yml   # Kustomize configuration
├── namespace.yaml      # Bifrost namespace
├── configmap.yaml      # Non-sensitive configuration
├── secret.yaml         # Secret template (DO NOT commit with values)
├── api/
│   ├── deployment.yaml # FastAPI application
│   └── service.yaml    # ClusterIP service (port 8000)
├── client/
│   ├── deployment.yaml # React frontend (nginx)
│   └── service.yaml    # ClusterIP service (port 80)
├── worker/
│   └── deployment.yaml # Background job workers
├── scheduler/
│   └── deployment.yaml # Scalable jobs + elected Cron trigger leader
└── rabbitmq/
    ├── deployment.yaml # RabbitMQ message broker
    └── service.yaml    # ClusterIP service (port 5672, 15672)
```

There is no separate `coding-agent` workload. The retired dedicated agent
service was replaced by unified agent execution: interactive agent requests run
in the API deployment, while queued autonomous agent runs are handled by the
worker deployment.

## Quick Start

### 1. Create Namespace

```bash
kubectl apply -f k8s/namespace.yaml
```

### 2. Configure Secrets

**Option A: kubectl create secret**

```bash
kubectl create secret generic bifrost-secrets \
  --namespace=bifrost \
  --from-literal=BIFROST_SECRET_KEY='your-32-char-secret-key-here' \
  --from-literal=BIFROST_DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/bifrost' \
  --from-literal=BIFROST_DATABASE_URL_SYNC='postgresql://user:pass@host:5432/bifrost' \
  --from-literal=BIFROST_RABBITMQ_URL='amqp://bifrost:pass@rabbitmq:5672/' \
  --from-literal=BIFROST_RABBITMQ_PASSWORD='your-rabbitmq-password' \
  --from-literal=BIFROST_REDIS_URL='redis://redis:6379/0' \
  --from-literal=BIFROST_S3_ENDPOINT_URL='https://s3.us-east-1.amazonaws.com' \
  --from-literal=BIFROST_S3_ACCESS_KEY='your-access-key' \
  --from-literal=BIFROST_S3_SECRET_KEY='your-secret-key'
```

**Option B: Edit and apply secret.yaml**

Edit `k8s/secret.yaml` with your values, then:

```bash
kubectl apply -f k8s/secret.yaml
```

### 3. Configure Settings

Edit `k8s/configmap.yaml` to set your S3 bucket and other settings:

```yaml
data:
  BIFROST_S3_BUCKET: "my-bifrost-bucket"
```

Then apply:

```bash
kubectl apply -f k8s/configmap.yaml
```

For Azure Blob Storage, replace the S3 storage settings above rather than using
an S3 endpoint. Keep the non-storage secret values, omit the `BIFROST_S3_*`
settings, and set these values in `k8s/configmap.yaml`:

```yaml
data:
  BIFROST_OBJECT_STORAGE_PROVIDER: "azure_blob"
  BIFROST_AZURE_BLOB_ACCOUNT_URL: "https://<account>.blob.core.windows.net"
  BIFROST_AZURE_BLOB_CONTAINER: "<container>"
  BIFROST_AZURE_BLOB_AUTH: "account_key"
```

For `account_key` authentication, replace the S3 flags in the secret command
with `--from-literal=BIFROST_AZURE_BLOB_ACCOUNT_KEY='your-account-key'`, or add
that key to `k8s/secret.yaml`; keep it out of the ConfigMap. Prefer
`default_credential` in production after configuring Azure identity for every
storage-using workload. These generic manifests do not configure Azure Workload
Identity, so changing the auth mode alone is insufficient; omit the account key
only once identity is available to the API, init container, and workers.

### 4. Deploy All Services

**Option A: Using Kustomize (recommended)**

```bash
kubectl apply -k k8s/
```

**Option B: Apply individual manifests**

```bash
kubectl apply -f k8s/rabbitmq/
kubectl apply -f k8s/api/
kubectl apply -f k8s/client/
kubectl apply -f k8s/worker/
kubectl apply -f k8s/scheduler/
```

### 5. Verify Deployment

```bash
# Check all pods are running
kubectl get pods -n bifrost

# Check services
kubectl get svc -n bifrost

# View API logs
kubectl logs -n bifrost -l app.kubernetes.io/name=bifrost-api -f

# View worker logs
kubectl logs -n bifrost -l app.kubernetes.io/name=bifrost-worker -f
```

## Exposing the Application

The manifests don't include an Ingress. Choose your preferred method:

### Option A: Ingress Controller (nginx-ingress)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: bifrost-ingress
  namespace: bifrost
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
    nginx.ingress.kubernetes.io/websocket-services: "api"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - bifrost.example.com
      secretName: bifrost-tls
  rules:
    - host: bifrost.example.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 8000
          - path: /auth
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 8000
          - path: /ws
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: client
                port:
                  number: 80
```

### Option B: LoadBalancer Service

Change the client service type to LoadBalancer:

```yaml
spec:
  type: LoadBalancer
```

### Option C: Port Forward (Development)

```bash
kubectl port-forward -n bifrost svc/client 3000:80
kubectl port-forward -n bifrost svc/api 8000:8000
```

## Scaling

### Isolated App Build Jobs

The optional Kubernetes App build Jobs overlay lives outside this base at
`deploy/kubernetes/builds`. It is not enabled by `kubectl apply -k k8s/`.

```bash
kubectl apply -k deploy/kubernetes/builds
```

See `docs/runbooks/kubernetes-build-jobs.md` before enabling or disabling it.

### API

```bash
kubectl scale deployment bifrost-api -n bifrost --replicas=3
```

### Workers

```bash
kubectl scale deployment bifrost-worker -n bifrost --replicas=5
```

### Scheduler

Every scheduler replica executes up to two durable platform jobs at a time. The
two internal slots are a safety ceiling rather than a deployment setting. A
fenced PostgreSQL lease elects exactly one replica to run Cron triggers and the
legacy scheduler pub/sub listener.

```bash
kubectl scale deployment bifrost-scheduler -n bifrost --replicas=3
```

Deploy the lease-aware scheduler version before scaling an existing installation.
The manifest retains the `Recreate` rollout strategy so an older singleton pod
cannot overlap the first lease-aware rollout; `Recreate` does not restrict the
steady-state replica count. Scale back to one before rolling back to a release
that predates scheduler leader election.

Long-running platform work runs through the shared durable queue on every
replica; the elected leader retains only short trigger and housekeeping work.
Scale gradually and monitor memory-pressure deferrals and queue wait time.

## Configuration Reference

### Secret Settings

| Name | Description |
|------|-------------|
| `BIFROST_SECRET_KEY` | 32+ char secret for JWT and encryption |
| `BIFROST_DATABASE_URL` | PostgreSQL async connection string |
| `BIFROST_DATABASE_URL_SYNC` | PostgreSQL sync connection string |
| `BIFROST_RABBITMQ_URL` | RabbitMQ AMQP connection string |
| `BIFROST_RABBITMQ_PASSWORD` | RabbitMQ password (for in-cluster deployment) |
| `BIFROST_REDIS_URL` | Redis connection string (for caching) |
| `BIFROST_S3_ENDPOINT_URL` | S3 provider only; optional for AWS S3, set for other S3-compatible services |
| `BIFROST_S3_ACCESS_KEY` | S3 provider only; access key |
| `BIFROST_S3_SECRET_KEY` | S3 provider only; secret key |
| `BIFROST_AZURE_BLOB_ACCOUNT_KEY` | Azure Blob provider only when `BIFROST_AZURE_BLOB_AUTH=account_key` |

### ConfigMap Settings

| Name | Default | Description |
|------|---------|-------------|
| `BIFROST_ENVIRONMENT` | `production` | Environment name |
| `BIFROST_DEBUG` | `false` | Debug mode |
| `BIFROST_OBJECT_STORAGE_PROVIDER` | `s3` | Storage provider: `s3` or `azure_blob` |
| `BIFROST_S3_BUCKET` | (required for `s3`) | S3 bucket name for workspace storage |
| `BIFROST_S3_REGION` | `us-east-1` | S3 provider only; region |
| `BIFROST_AZURE_BLOB_ACCOUNT_URL` | (required for `azure_blob`) | Blob account URL, such as `https://acct.blob.core.windows.net` |
| `BIFROST_AZURE_BLOB_CONTAINER` | (required for `azure_blob`) | Blob container for workspace and upload storage |
| `BIFROST_AZURE_BLOB_AUTH` | `default_credential` | Azure Blob auth: `default_credential` or `account_key` |
| `BIFROST_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT access token TTL |
| `BIFROST_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token TTL |
| `BIFROST_MFA_ENABLED` | `true` | Enable MFA |
| `BIFROST_MAX_CONCURRENCY` | `10` | Worker concurrency |
| `BIFROST_WEBAUTHN_RP_ID` | (required) | WebAuthn relying party ID (your domain) |
| `BIFROST_WEBAUTHN_RP_NAME` | `Bifrost` | WebAuthn display name |
| `BIFROST_WEBAUTHN_ORIGIN` | (required) | WebAuthn origin URL (e.g., https://bifrost.example.com) |
| `BIFROST_PUBLIC_URL` | (required) | Public URL for the Bifrost platform |

## Troubleshooting

### Pods stuck in Pending

Check for resource constraints:

```bash
kubectl describe pod -n bifrost <pod-name>
```

### Database connection errors

1. Verify the database is accessible from the cluster
2. Check the connection string in secrets
3. Ensure the database user has proper permissions

### Migrations not running

The API init container runs migrations. Check its logs:

```bash
kubectl logs -n bifrost <api-pod-name> -c migrate
```

### Workers not processing jobs

1. Check RabbitMQ connection
2. Verify worker logs for errors
3. Ensure the queue exists in RabbitMQ
