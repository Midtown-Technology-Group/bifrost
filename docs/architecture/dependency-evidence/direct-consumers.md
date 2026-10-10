# Direct dependency consumers at `02d1ca3`

Generated from `baseline-02d1ca3.json.gz`. `imports[index]` resolves each `consumer_sites` entry. Import aliases and direct calls are static evidence; instance methods, reexports and runtime dispatch remain manual review. Counts below are resolver-provenance descendants, **not exclusive removable dependencies**.

| Declaration | Main lock | Observed consumer scopes | Provenance descendants |
|---|---|---|---|
| `fastapi` | 0.139.0 | runtime, test, tooling-or-other-runtime | 11 |
| `uvicorn[standard]` | 0.46.0 | runtime | 10 |
| `python-multipart>=0.0.27` | 0.0.32 | No static consumer; review framework/command/transitive use | 0 |
| `sqlalchemy[asyncio]` | 2.0.49 | runtime, sdk-cli, test, tooling-or-other-runtime | 2 |
| `asyncpg>=0.31.0` | 0.31.0 | tooling-or-other-runtime | 0 |
| `alembic` | 1.18.4 | test, tooling-or-other-runtime | 5 |
| `greenlet` | 3.4.0 | No static consumer; review framework/command/transitive use | 0 |
| `aio-pika` | 9.6.2 | runtime, test, tooling-or-other-runtime | 6 |
| `redis` | 7.4.0 | runtime, sdk-cli, test, tooling-or-other-runtime | 0 |
| `pyjwt[crypto]>=2.15.1` | 2.15.1 | runtime, test | 3 |
| `pwdlib[bcrypt]` | 0.3.0 | runtime | 1 |
| `cryptography>=50.0.0` | 50.0.0 | runtime, test | 2 |
| `pyotp` | 2.9.0 | runtime, test | 0 |
| `webauthn>=2.7.1` | 2.7.1 | runtime, test | 6 |
| `altcha>=2.1.0` | 2.1.0 | runtime, test | 0 |
| `pydantic>=2.12` | 2.13.3 | runtime, sdk-cli, test, tooling-or-other-runtime | 7 |
| `pydantic-settings` | 2.14.0 | runtime | 9 |
| `python-dotenv` | 1.2.2 | sdk-cli | 0 |
| `packaging` | 26.2 | runtime | 0 |
| `keyring>=24.0` | 25.7.0 | sdk-cli, test | 9 |
| `email-validator` | 2.3.0 | runtime | 2 |
| `httpx` | 0.28.1 | runtime, sdk-cli, test, tooling-or-other-runtime | 5 |
| `aiohttp>=3.14.3` | 3.14.3 | runtime, sdk-cli, test | 8 |
| `PyYAML` | 6.0.3 | runtime, sdk-cli, test | 0 |
| `croniter` | 6.2.2 | runtime | 2 |
| `tzdata` | 2026.3 | No static consumer; review framework/command/transitive use | 0 |
| `defusedxml>=0.7.1` | 0.7.1 | runtime | 0 |
| `Pillow` | 12.3.0 | runtime, test | 0 |
| `CairoSVG` | 2.9.0 | runtime | 8 |
| `reportlab>=4.4.0` | 5.0.0 | runtime | 2 |
| `python-docx>=1.2.0` | 1.2.0 | runtime, test | 2 |
| `openpyxl>=3.1.5` | 3.1.5 | runtime, test | 1 |
| `jmespath>=1.0.1` | 1.1.0 | runtime | 0 |
| `libcst` | 1.8.6 | runtime | 1 |
| `regex` | 2026.7.19 | runtime | 0 |
| `jinja2` | 3.1.6 | runtime, test | 1 |
| `PyGithub` | 2.9.1 | test | 11 |
| `GitPython>=3.1.60` | 3.1.60 | runtime, test | 2 |
| `apscheduler` | 3.11.2 | runtime, test | 1 |
| `psutil` | 7.2.2 | runtime | 0 |
| `sentry-sdk[fastapi]==2.60.0` | 2.60.0 | runtime | 14 |
| `opentelemetry-sdk==1.41.1` | 1.41.1 | runtime, test | 5 |
| `opentelemetry-exporter-otlp-proto-grpc==1.41.1` | 1.41.1 | runtime | 11 |
| `pytest` | 9.0.3 | test, tooling-or-other-runtime | 4 |
| `pytest-asyncio` | 1.3.0 | test | 5 |
| `pytest-cov` | 7.1.0 | No static consumer; review framework/command/transitive use | 6 |
| `pytest-env` | 1.6.0 | No static consumer; review framework/command/transitive use | 6 |
| `hypothesis` | 6.165.10 | test | 1 |
| `pytest-timeout` | 2.4.0 | No static consumer; review framework/command/transitive use | 5 |
| `websockets` | 16.0 | sdk-cli, test | 0 |
| `aiobotocore` | 3.5.0 | runtime, test | 16 |
| `types-aiobotocore[s3]` | 3.5.0 | No static consumer; review framework/command/transitive use | 3 |
| `azure-identity` | 1.25.3 | runtime, test | 13 |
| `azure-storage-blob` | 12.29.0 | runtime, test | 11 |
| `msal>=1.37.0` | 1.37.0 | No static consumer; review framework/command/transitive use | 9 |
| `pydantic-ai-slim[openai,anthropic,google,retries]==2.35.3` | 2.35.3 | runtime, test | 43 |
| `pydantic-ai-harness==0.27.0` | 0.27.0 | runtime, test | 44 |
| `mcp==2.0.0` | 2.0.0 | runtime, test | 37 |
| `fastmcp==4.0.0b1` | 4.0.0b1 | runtime, test | 74 |
| `jsonschema>=4.26.0,<5` | 4.26.0 | runtime, test, tooling-or-other-runtime | 4 |
| `pgvector` | 0.4.2 | No static consumer; review framework/command/transitive use | 1 |
| `textual` | 8.2.4 | sdk-cli | 9 |
| `debugpy` | 1.8.20 | No static consumer; review framework/command/transitive use | 0 |
| `ruff` | 0.15.12 | No static consumer; review framework/command/transitive use | 0 |
| `watchdog` | 6.0.0 | sdk-cli, test | 0 |
| `pathspec` | 1.1.0 | runtime, sdk-cli | 0 |
| `Mako>=1.3.12` | 1.4.1 | No static consumer; review framework/command/transitive use | 1 |
| `urllib3>=2.8.0` | 2.8.0 | No static consumer; review framework/command/transitive use | 0 |
| `joserfc>=1.6.8` | 1.7.4 | No static consumer; review framework/command/transitive use | 3 |
| `pyasn1>=0.6.4` | 0.6.4 | No static consumer; review framework/command/transitive use | 0 |
| `setuptools>=78.1.1` | OMITTED (unsafe setuptools) | No static consumer; review framework/command/transitive use | 0 |
| `tqdm>=4.66.3` | 4.67.3 | No static consumer; review framework/command/transitive use | 0 |

Other declarations (including duplicate SDK `packaging`):

| Manifest/group | Declaration | Resolution by lock |
|---|---|---|
| `api/bifrost/pyproject.toml` / dependencies | `aiohttp>=3.9.0` | requirements.lock: 3.14.3 |
| `api/bifrost/pyproject.toml` / dependencies | `click>=8.1.0` | .github/requirements/api-contract-fuzzing.lock: 8.4.2; doc_renderer_service/requirements.lock: 8.4.2; requirements-piptools.lock: 8.3.3; requirements.lock: 8.3.3 |
| `api/bifrost/pyproject.toml` / dependencies | `httpx>=0.24.0` | requirements.lock: 0.28.1 |
| `api/bifrost/pyproject.toml` / dependencies | `packaging>=24.0` | .github/requirements/api-contract-fuzzing.lock: 26.2; requirements-piptools.lock: 26.2; requirements.lock: 26.2 |
| `api/bifrost/pyproject.toml` / dependencies | `pathspec>=0.11.0` | requirements.lock: 1.1.0 |
| `api/bifrost/pyproject.toml` / dependencies | `pydantic>=2.12` | .clusterfuzzlite/requirements.lock: 2.13.3; doc_renderer_service/requirements.lock: 2.13.4; requirements.lock: 2.13.3 |
| `api/bifrost/pyproject.toml` / dependencies | `watchdog>=4.0.0` | requirements.lock: 6.0.0 |
| `api/bifrost/pyproject.toml` / dependencies | `PyYAML>=6.0` | .github/requirements/api-contract-fuzzing.lock: 6.0.3; doc_renderer_service/requirements.lock: 6.0.3; requirements.lock: 6.0.3 |
| `api/bifrost/pyproject.toml` / dependencies | `websockets>=12.0` | doc_renderer_service/requirements.lock: 16.1; requirements.lock: 16.0 |
| `api/bifrost/pyproject.toml` / dependencies | `textual>=1.0.0` | requirements.lock: 8.2.4 |
| `api/bifrost/pyproject.toml` / dependencies | `python-dotenv>=1.0.0` | doc_renderer_service/requirements.lock: 1.2.2; requirements.lock: 1.2.2 |
| `api/bifrost/pyproject.toml` / dependencies | `keyring>=24.0.0` | requirements.lock: 25.7.0 |
| `api/bifrost/pyproject.toml` / dependencies | `packaging>=24.0` | .github/requirements/api-contract-fuzzing.lock: 26.2; requirements-piptools.lock: 26.2; requirements.lock: 26.2 |
| `api/bifrost/pyproject.toml` / dev | `pytest>=7.0.0` | .github/requirements/api-contract-fuzzing.lock: 9.1.1; requirements.lock: 9.0.3 |
| `api/bifrost/pyproject.toml` / dev | `pytest-asyncio>=0.21.0` | requirements.lock: 1.3.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `croniter==6.2.2` | .clusterfuzzlite/requirements.lock: 6.2.2; requirements.lock: 6.2.2 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `regex==2026.5.9` | .clusterfuzzlite/requirements.lock: 2026.5.9; requirements.lock: 2026.7.19 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `sqlalchemy==2.0.49` | .clusterfuzzlite/requirements.lock: 2.0.49; requirements.lock: 2.0.49 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `greenlet==3.4.0` | .clusterfuzzlite/requirements.lock: 3.4.0; requirements.lock: 3.4.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `starlette==1.3.1` | .clusterfuzzlite/requirements.lock: 1.3.1; .github/requirements/api-contract-fuzzing.lock: 1.3.1; doc_renderer_service/requirements.lock: 1.3.1; requirements.lock: 1.3.1 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `pydantic==2.13.3` | .clusterfuzzlite/requirements.lock: 2.13.3; doc_renderer_service/requirements.lock: 2.13.4; requirements.lock: 2.13.3 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `pydantic-core==2.46.3` | .clusterfuzzlite/requirements.lock: 2.46.3; doc_renderer_service/requirements.lock: 2.46.4; requirements.lock: 2.46.3 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `annotated-types==0.7.0` | .clusterfuzzlite/requirements.lock: 0.7.0; doc_renderer_service/requirements.lock: 0.7.0; requirements.lock: 0.7.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `dnspython==2.8.0` | .clusterfuzzlite/requirements.lock: 2.8.0; requirements.lock: 2.8.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `email-validator==2.3.0` | .clusterfuzzlite/requirements.lock: 2.3.0; requirements.lock: 2.3.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `idna==3.18` | .clusterfuzzlite/requirements.lock: 3.18; .github/requirements/api-contract-fuzzing.lock: 3.18; doc_renderer_service/requirements.lock: 3.18; requirements.lock: 3.19 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `typing-extensions==4.15.0` | .clusterfuzzlite/requirements.lock: 4.15.0; .github/requirements/api-contract-fuzzing.lock: 4.16.0; doc_renderer_service/requirements.lock: 4.16.0; requirements-pyright.lock: 4.15.0; requirements.lock: 4.15.0 |
| `.clusterfuzzlite/requirements.txt` / requirements-source | `typing-inspection==0.4.2` | .clusterfuzzlite/requirements.lock: 0.4.2; doc_renderer_service/requirements.lock: 0.4.2; requirements.lock: 0.4.2 |
| `.github/requirements/api-contract-fuzzing.in` / requirements-source | `schemathesis==4.22.3` | .github/requirements/api-contract-fuzzing.lock: 4.22.3 |
| `doc_renderer_service/requirements.txt` / requirements-source | `fastapi==0.139.0` | doc_renderer_service/requirements.lock: 0.139.0; requirements.lock: 0.139.0 |
| `doc_renderer_service/requirements.txt` / requirements-source | `uvicorn[standard]==0.51.0` | doc_renderer_service/requirements.lock: 0.51.0; requirements.lock: 0.46.0 |
| `doc_renderer_service/requirements.txt` / requirements-source | `weasyprint==69.0` | doc_renderer_service/requirements.lock: 69.0 |
| `doc_renderer_service/requirements.txt` / requirements-source | `pydantic==2.13.4` | .clusterfuzzlite/requirements.lock: 2.13.3; doc_renderer_service/requirements.lock: 2.13.4; requirements.lock: 2.13.3 |
