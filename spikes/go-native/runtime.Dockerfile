FROM scratch
# Executables are mounted read-only by the supervisor; this image has no tools.
ENTRYPOINT ["/probe"]
