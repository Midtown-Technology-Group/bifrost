# Build context: core-rs/. The runtime never contains source, Cargo or DB secrets.
FROM rust:1.98.1-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730 AS toolchain
WORKDIR /workspace/core-rs
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN rustup component add clippy rustfmt
COPY . .

FROM toolchain AS checks
RUN cargo fmt --check
RUN cargo clippy --locked --all-targets --all-features -- -D warnings
RUN cargo test --locked --all

FROM toolchain AS build
ARG BIFROST_BUILD_SHA=development
ENV BIFROST_BUILD_SHA=${BIFROST_BUILD_SHA}
RUN cargo build --locked --release --bin bifrost-core

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /workspace/core-rs/target/release/bifrost-core /usr/local/bin/bifrost-core
USER 65532:65532
EXPOSE 8001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/bifrost-core"]
