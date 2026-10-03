# Bifrost Integrations

An open-source platform for building and running integrations across customers. Bifrost combines Python workflows and reusable integrations with the connection, configuration, secret, and monitoring tools needed to operate them. It is built for Integration Services teams that want to own their automation stack.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![CI](https://github.com/Midtown-Technology-Group/bifrost/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Midtown-Technology-Group/bifrost/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Midtown-Technology-Group/bifrost/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Midtown-Technology-Group/bifrost/actions/workflows/codeql.yml)
[![Coverage](https://codecov.io/gh/Midtown-Technology-Group/bifrost/graph/badge.svg?branch=main)](https://codecov.io/gh/Midtown-Technology-Group/bifrost)
[![Sonar quality gate](https://img.shields.io/sonar/quality_gate/Midtown-Technology-Group_bifrost?server=https%3A%2F%2Fsonarcloud.io)](https://sonarcloud.io/summary/new_code?id=Midtown-Technology-Group_bifrost)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/Midtown-Technology-Group/bifrost/badge)](https://securityscorecards.dev/viewer/?uri=github.com/Midtown-Technology-Group/bifrost)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13022/badge)](https://www.bestpractices.dev/en/projects/13022)

## What Bifrost provides

- **Reusable Python workflows and integrations** across organizations, with Git-based source management.
- **Connection and secret management**, including OAuth token refresh and organization-scoped configuration.
- **Forms and apps** for collecting inputs and exposing workflows to users.
- **Multi-tenant execution** with workers, schedules, monitoring, and per-organization access controls.

The web client is in `client/`; the FastAPI service, workers, and shared Python code are in `api/`. PostgreSQL stores application data, Redis supports caching and coordination, and S3-compatible storage holds objects. Work delivery is configurable: the Docker Compose stacks default to PostgreSQL (matching MTG production); set `BIFROST_WORK_DELIVERY_BACKEND=rabbitmq` to use the broker instead.

## Quick start

You need Git and Docker with Compose.

```bash
git clone https://github.com/Midtown-Technology-Group/bifrost.git
cd bifrost
./setup.sh
docker compose up -d
```

For the default `localhost` setup, open [the client](http://localhost:3000) or [API documentation](http://localhost:3000/api/docs). `./setup.sh` creates the local `.env` file and prompts for a domain; use the domain you chose if you changed the default. Stop the stack with `docker compose down`.

## Development

The development launcher gives each worktree its own stack and reloads API and client source changes:

```bash
./debug.sh up
./debug.sh status       # Shows the URL and development login
./debug.sh logs api     # Follow API logs (or omit api for all services)
./debug.sh down
```

Use the URL from `./debug.sh status`; the local port or NetBird URL depends on your environment. For testing and contribution rules, see [CONTRIBUTING.md](CONTRIBUTING.md) and the [developer guide](CLAUDE.md#development-environment-critical---read-first).

## Documentation

- [API reference](http://localhost:3000/api/docs) when the default Compose stack is running
- [Developer and test commands](CLAUDE.md#testing--quality)
- [Architecture decisions](docs/architecture/) and [execution details](api/src/services/execution/README.md)
- [Versioning and releases](docs/VERSIONING.md)
- [Security policy](SECURITY.md)

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, tests, and DCO sign-off. See the [code of conduct](CODE_OF_CONDUCT.md) and [governance policy](GOVERNANCE.md) for community guidance.

## License

Bifrost is licensed under the [GNU Affero General Public License v3.0](LICENSE).
