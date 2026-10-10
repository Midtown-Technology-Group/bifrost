FROM scratch
# Trusted build-plane output only; no compiler, source, shell or credentials.
COPY launcher /launcher
ENTRYPOINT ["/launcher"]
