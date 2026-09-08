#!/bin/sh
set -eu

cd /app

# Full-repository analysis exceeds Node's default 2 GiB heap. Keep a bounded
# budget while allowing operators to supply their own Node settings.
NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}" \
    pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python
ruff check .
