#!/usr/bin/env bash
set -euo pipefail
exec python3 -I -S -B - "$@" <<'PY'
"""Specific F4 supervisor. Source-only reviewed; runtime requires a separate release.

The literal command roster is v027/abba5657. Native commands retain one-use
labels; a Docker CLI exit never substitutes object/process/IPC settlement.
The controller never imports a product module or replays a constructor/commit.
"""

from __future__ import annotations

import ast
import configparser
import hashlib
import ipaddress
import itertools
import json
import math
import os
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROSTER = {
    "git.version": ("git", "--version"),
    "git.origin": ("git", "remote", "get-url", "origin"),
    "git.root": ("git", "rev-parse", "--show-toplevel"),
    "git.head": ("git", "rev-parse", "HEAD"),
    "git.tree": ("git", "rev-parse", "HEAD^{tree}"),
    "git.status": ("git", "status", "--porcelain=v1", "--untracked-files=all", "-z"),
    "git.fetch": (
        "git",
        "fetch",
        "--no-tags",
        "https://github.com/Midtown-Technology-Group/bifrost.git",
        "refs/heads/main:refs/remotes/origin/main",
    ),
    "git.main": ("git", "rev-parse", "refs/remotes/origin/main"),
    "git.ancestor": ("git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"),
    "git.tree_roster": ("git", "ls-tree", "-r", "-z", "HEAD"),
    "git.object_read": ("git", "cat-file", "--batch"),
    "git.final_head": ("git", "rev-parse", "HEAD"),
    "git.final_tree": ("git", "rev-parse", "HEAD^{tree}"),
    "git.final_status": ("git", "status", "--porcelain=v1", "--untracked-files=all", "-z"),
    "workspace.init": ("git", "init", "--bare", "<owned_workspace_git>"),
    "workspace.fetch": (
        "git",
        "--git-dir",
        "<owned_workspace_git>",
        "fetch",
        "--no-tags",
        "--depth=1",
        "https://github.com/MTG-Thomas/bifrost-workspace.git",
        "refs/heads/main:refs/remotes/origin/main",
    ),
    "workspace.main": ("git", "--git-dir", "<owned_workspace_git>", "rev-parse", "refs/remotes/origin/main"),
    "workspace.tree_roster": (
        "git",
        "--git-dir",
        "<owned_workspace_git>",
        "ls-tree",
        "-r",
        "-z",
        "refs/remotes/origin/main",
    ),
    "workspace.object_read": ("git", "--git-dir", "<owned_workspace_git>", "cat-file", "--batch"),
    "tool.docker.version": ("docker", "version", "--format", "{{json .}}"),
    "tool.compose.version": ("docker", "compose", "version", "--short"),
    "toolchain.version.create": (
        "docker",
        "create",
        "--name",
        "<tool_version_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=tool-version",
        "--network",
        "none",
        "--entrypoint",
        "/bin/sh",
        "<owned_toolchain_id>",
        "-c",
        "set -e; rustc -vV; cargo -V; rustfmt --version; cargo clippy --version",
    ),
    "toolchain.version.qualified": ("docker", "inspect", "--type", "container", "<tool_version_cid>"),
    "toolchain.version.start": ("docker", "start", "--attach", "<tool_version_cid>"),
    "toolchain.version.terminal": ("docker", "inspect", "--type", "container", "<tool_version_cid>"),
    "toolchain.version.remove": ("docker", "rm", "<tool_version_cid>"),
    "toolchain.version.absent": ("docker", "inspect", "--type", "container", "<tool_version_cid>"),
    "toolchain.version.gap": ("docker", "inspect", "--type", "container", "<tool_version_name>"),
    "toolchain.version.prekill": ("docker", "inspect", "--type", "container", "<tool_version_cid>"),
    "toolchain.version.kill": ("docker", "kill", "<tool_version_cid>"),
    "toolchain.version.postkill": ("docker", "inspect", "--type", "container", "<tool_version_cid>"),
    "profile.context": ("docker", "context", "inspect", "default"),
    "profile.daemon": ("docker", "info", "--format", "{{json .}}"),
    "initial.inventory": (
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.project=<owned_project>",
        "--format",
        "{{json .}}",
    ),
    "initial.images": (
        "docker",
        "image",
        "ls",
        "--all",
        "--no-trunc",
        "--format",
        '{"id":{{json .ID}},"repository":{{json .Repository}},"tag":{{json .Tag}}}',
    ),
    "compose.base": (
        "docker",
        "compose",
        "--project-directory",
        "<candidate_root>",
        "-p",
        "<owned_project>",
        "-f",
        "<candidate_root>/docker-compose.test.yml",
        "--profile",
        "e2e",
        "--profile",
        "test",
        "--profile",
        "client",
        "--profile",
        "client-check",
        "config",
        "--format",
        "json",
    ),
    "compose.resolved": (
        "docker",
        "compose",
        "--project-directory",
        "<candidate_root>",
        "-p",
        "<owned_project>",
        "-f",
        "<owned_resolved_json>",
        "--profile",
        "e2e",
        "--profile",
        "test",
        "--profile",
        "client",
        "--profile",
        "client-check",
        "config",
        "--format",
        "json",
    ),
    "prepr": ("./test.sh", "pre-pr"),
    "api.build": ("docker", "compose", "-f", "<original_base>", "build", "api"),
    "native.toolchain.build": (
        "docker",
        "build",
        "--target",
        "toolchain",
        "--file",
        "<candidate_root>/core-rs/Dockerfile",
        "--tag",
        "<owned_toolchain_tag>",
        "<candidate_root>/core-rs",
    ),
    "prerequisite.images.inspect": ("docker", "image", "inspect", "<owned_toolchain_tag>"),
    "stack.up": ("./test.sh", "stack", "up"),
    "stack.services.discover": (
        "docker",
        "ps",
        "--all",
        "--no-trunc",
        "--filter",
        "label=com.docker.compose.project=<owned_project>",
        "--format",
        "{{json .}}",
    ),
    "stack.services.inspect": (
        "docker",
        "inspect",
        "--type",
        "container",
        "<actual_CIDs_sorted_by_frozen_service_order>",
    ),
    "network.inspect": ("docker", "network", "inspect", "<owned_network_id>"),
    "runner.start": ("./test.sh", "tests/diagnostics/workflow_commit_fault.py", "-v"),
    "runner.discover": (
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.project=<owned_project>",
        "--filter",
        "label=com.docker.compose.service=test-runner",
        "--filter",
        "label=com.docker.compose.oneoff=True",
        "--format",
        "{{json .}}",
    ),
    "runner.inspect": ("docker", "inspect", "--type", "container", "<runner_cid>"),
    "runner.top": ("docker", "top", "<runner_cid>", "-eo", "pid,ppid,uid,gid"),
    "runner.source": (
        "docker",
        "exec",
        "--user",
        "1000:1000",
        "<runner_cid>",
        "python",
        "-c",
        "<STATIC_READER_50ed8e4d>",
        "observer",
    ),
    "frontend.version": ("docker", "exec", "<pgbouncer_cid>", "/proc/1/exe", "--version"),
    "frontend.entrypoint": ("docker", "exec", "<pgbouncer_cid>", "/bin/sh", "-c", "<STATIC_ENTRYPOINT_DIGEST>"),
    "frontend.before.1": ("docker", "inspect", "--type", "container", "<pgbouncer_cid>"),
    "frontend.before.2": ("docker", "top", "<pgbouncer_cid>", "-eo", "pid,ppid"),
    "frontend.before.3": ("docker", "exec", "<pgbouncer_cid>", "/bin/sh", "-c", "<STATIC_PROCESS_READBACK>"),
    "frontend.before.4": ("docker", "exec", "<pgbouncer_cid>", "/bin/sh", "-c", "<STATIC_TCP_READBACK>"),
    "frontend.before.5": (
        "docker",
        "exec",
        "<pgbouncer_cid>",
        "/bin/sh",
        "-c",
        "<STATIC_CONFIG_READBACK>",
        "observer",
        "<config_path>",
    ),
    "frontend.before.6": ("docker", "inspect", "--type", "network", "<owned_network_id>"),
    "frontend.before.7": (
        "docker",
        "exec",
        "<runner_cid>",
        "python",
        "-c",
        "<STATIC_DNS_READBACK>",
        "<actual_observer_constructor_hostname>",
    ),
    "frontend.before.8": ("docker", "inspect", "--type", "container", "<pgbouncer_cid>"),
    "frontend.after.1": ("docker", "inspect", "--type", "container", "<pgbouncer_cid>"),
    "frontend.after.2": ("docker", "top", "<pgbouncer_cid>", "-eo", "pid,ppid"),
    "frontend.after.3": ("docker", "exec", "<pgbouncer_cid>", "/bin/sh", "-c", "<STATIC_PROCESS_READBACK>"),
    "frontend.after.4": ("docker", "exec", "<pgbouncer_cid>", "/bin/sh", "-c", "<STATIC_TCP_READBACK>"),
    "frontend.after.5": (
        "docker",
        "exec",
        "<pgbouncer_cid>",
        "/bin/sh",
        "-c",
        "<STATIC_CONFIG_READBACK>",
        "observer",
        "<config_path>",
    ),
    "frontend.after.6": ("docker", "inspect", "--type", "network", "<owned_network_id>"),
    "frontend.after.7": (
        "docker",
        "exec",
        "<runner_cid>",
        "python",
        "-c",
        "<STATIC_DNS_READBACK>",
        "<actual_observer_constructor_hostname>",
    ),
    "frontend.after.8": ("docker", "inspect", "--type", "container", "<pgbouncer_cid>"),
    "relay.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<relay_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.actor=relay",
        "--network",
        "<owned_network_id>",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "--mount",
        "type=bind,src=<relay_socket>,dst=/run/f4-control.sock,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/src,dst=/app/src,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/tests,dst=/app/tests,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/shared,dst=/app/shared,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/bifrost,dst=/app/bifrost,readonly",
        "--entrypoint",
        "python",
        "<qualified_api_id>",
        "/app/tests/parity/workflow_commit_fault_child.py",
        "relay",
    ),
    "relay.start": ("docker", "start", "--attach", "--interactive", "<relay_cid>"),
    "relay.live": ("docker", "inspect", "--type", "container", "<relay_cid>"),
    "relay.top": ("docker", "top", "<relay_cid>", "-eo", "pid,ppid,uid,gid"),
    "relay.source": (
        "docker",
        "exec",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "<relay_cid>",
        "python",
        "-c",
        "<STATIC_READER_50ed8e4d>",
        "relay",
    ),
    "relay.terminal": ("docker", "inspect", "--type", "container", "<relay_cid>"),
    "relay.remove": ("docker", "rm", "<relay_cid>"),
    "relay.absent": ("docker", "inspect", "--type", "container", "<relay_cid>"),
    "relay.creation_gap": ("docker", "inspect", "--type", "container", "<relay_name>"),
    "relay.prekill": ("docker", "inspect", "--type", "container", "<relay_cid>"),
    "relay.kill": ("docker", "kill", "<relay_cid>"),
    "relay.postkill": ("docker", "inspect", "--type", "container", "<relay_cid>"),
    "python.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<python_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.actor=python",
        "--network",
        "<owned_network_id>",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "--mount",
        "type=bind,src=<python_socket>,dst=/run/f4-control.sock,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/src,dst=/app/src,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/tests,dst=/app/tests,readonly",
        "--add-host",
        "<original_hostname>:<actual_relay_ip>",
        "--env-file",
        "<python_env_file>",
        "--mount",
        "type=bind,src=<candidate_root>/api/shared,dst=/app/shared,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/bifrost,dst=/app/bifrost,readonly",
        "--entrypoint",
        "python",
        "<qualified_api_id>",
        "/app/tests/parity/workflow_commit_fault_child.py",
        "python",
    ),
    "python.start": ("docker", "start", "--attach", "--interactive", "<python_cid>"),
    "python.live": ("docker", "inspect", "--type", "container", "<python_cid>"),
    "python.top": ("docker", "top", "<python_cid>", "-eo", "pid,ppid,uid,gid"),
    "python.source": (
        "docker",
        "exec",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "<python_cid>",
        "python",
        "-c",
        "<STATIC_READER_50ed8e4d>",
        "python",
    ),
    "python.terminal": ("docker", "inspect", "--type", "container", "<python_cid>"),
    "python.remove": ("docker", "rm", "<python_cid>"),
    "python.absent": ("docker", "inspect", "--type", "container", "<python_cid>"),
    "python.creation_gap": ("docker", "inspect", "--type", "container", "<python_name>"),
    "python.prekill": ("docker", "inspect", "--type", "container", "<python_cid>"),
    "python.kill": ("docker", "kill", "<python_cid>"),
    "python.postkill": ("docker", "inspect", "--type", "container", "<python_cid>"),
    "rust.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<rust_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.actor=rust",
        "--network",
        "<owned_network_id>",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "--mount",
        "type=bind,src=<rust_socket>,dst=/run/f4-control.sock,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/src,dst=/app/src,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/tests,dst=/app/tests,readonly",
        "--add-host",
        "<original_hostname>:<actual_relay_ip>",
        "--env-file",
        "<rust_env_file>",
        "--mount",
        "type=bind,src=<qualified_binary>,dst=/f4/bin/workflow_sql_vectors,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/shared,dst=/app/shared,readonly",
        "--mount",
        "type=bind,src=<candidate_root>/api/bifrost,dst=/app/bifrost,readonly",
        "--entrypoint",
        "/f4/bin/workflow_sql_vectors",
        "<qualified_api_id>",
        "apply-result-fault",
    ),
    "rust.start": ("docker", "start", "--attach", "--interactive", "<rust_cid>"),
    "rust.live": ("docker", "inspect", "--type", "container", "<rust_cid>"),
    "rust.top": ("docker", "top", "<rust_cid>", "-eo", "pid,ppid,uid,gid"),
    "rust.source": (
        "docker",
        "exec",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "<rust_cid>",
        "python",
        "-c",
        "<STATIC_READER_50ed8e4d>",
        "rust",
    ),
    "rust.terminal": ("docker", "inspect", "--type", "container", "<rust_cid>"),
    "rust.remove": ("docker", "rm", "<rust_cid>"),
    "rust.absent": ("docker", "inspect", "--type", "container", "<rust_cid>"),
    "rust.creation_gap": ("docker", "inspect", "--type", "container", "<rust_name>"),
    "rust.prekill": ("docker", "inspect", "--type", "container", "<rust_cid>"),
    "rust.kill": ("docker", "kill", "<rust_cid>"),
    "rust.postkill": ("docker", "inspect", "--type", "container", "<rust_cid>"),
    "runner.failure.inspect": ("docker", "inspect", "--type", "container", "<runner_cid>"),
    "runner.failure.kill": ("docker", "kill", "<runner_cid>"),
    "stack.down": ("./test.sh", "stack", "down"),
    "inventory.initial.containers": (
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.project=<target_project>",
        "--format",
        "{{json .}}",
    ),
    "inventory.initial.volumes": (
        "docker",
        "volume",
        "ls",
        "--format",
        '{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}}}',
    ),
    "inventory.initial.networks": (
        "docker",
        "network",
        "ls",
        "--filter",
        "label=com.docker.compose.project=<target_project>",
        "--format",
        "{{json .}}",
    ),
    "inventory.prepr_final.containers": (
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.project=<prepr_project>",
        "--format",
        "{{json .}}",
    ),
    "inventory.prepr_final.volumes": (
        "docker",
        "volume",
        "ls",
        "--format",
        '{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}}}',
    ),
    "inventory.prepr_final.networks": (
        "docker",
        "network",
        "ls",
        "--filter",
        "label=com.docker.compose.project=<prepr_project>",
        "--format",
        "{{json .}}",
    ),
    "inventory.target_final.containers": (
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=com.docker.compose.project=<target_project>",
        "--format",
        "{{json .}}",
    ),
    "inventory.target_final.volumes": (
        "docker",
        "volume",
        "ls",
        "--format",
        '{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}}}',
    ),
    "inventory.target_final.networks": (
        "docker",
        "network",
        "ls",
        "--filter",
        "label=com.docker.compose.project=<target_project>",
        "--format",
        "{{json .}}",
    ),
    "volume.cargo.initial": ("docker", "volume", "inspect", "<cargo_volume>"),
    "volume.cargo.create": (
        "docker",
        "volume",
        "create",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "<cargo_volume>",
    ),
    "volume.cargo.qualified": ("docker", "volume", "inspect", "<cargo_volume>"),
    "volume.cargo.remove": ("docker", "volume", "rm", "<cargo_volume>"),
    "volume.cargo.absent": ("docker", "volume", "inspect", "<cargo_volume>"),
    "volume.target.initial": ("docker", "volume", "inspect", "<target_volume>"),
    "volume.target.create": (
        "docker",
        "volume",
        "create",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "<target_volume>",
    ),
    "volume.target.qualified": ("docker", "volume", "inspect", "<target_volume>"),
    "volume.target.remove": ("docker", "volume", "rm", "<target_volume>"),
    "volume.target.absent": ("docker", "volume", "inspect", "<target_volume>"),
    "fetch.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<fetch_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=fetch",
        "--network",
        "bridge",
        "--mount",
        "type=bind,src=<candidate_root>/core-rs,dst=/workspace/core-rs,readonly",
        "--mount",
        "type=volume,src=<cargo_volume>,dst=/cargo",
        "--mount",
        "type=volume,src=<target_volume>,dst=/target",
        "--env",
        "CARGO_HOME=/cargo",
        "--env",
        "CARGO_TARGET_DIR=/target",
        "--workdir",
        "/workspace/core-rs",
        "<qualified_toolchain_id>",
        "cargo",
        "fetch",
        "--locked",
    ),
    "fetch.qualified": ("docker", "inspect", "--type", "container", "<fetch_cid>"),
    "fetch.start": ("docker", "start", "--attach", "--interactive", "<fetch_cid>"),
    "fetch.terminal": ("docker", "inspect", "--type", "container", "<fetch_cid>"),
    "fetch.remove": ("docker", "rm", "<fetch_cid>"),
    "fetch.absent": ("docker", "inspect", "--type", "container", "<fetch_cid>"),
    "fetch.gap": ("docker", "inspect", "--type", "container", "<fetch_name>"),
    "fetch.prekill": ("docker", "inspect", "--type", "container", "<fetch_cid>"),
    "fetch.kill": ("docker", "kill", "<fetch_cid>"),
    "fetch.postkill": ("docker", "inspect", "--type", "container", "<fetch_cid>"),
    "fmt.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<fmt_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=fmt",
        "--network",
        "none",
        "--mount",
        "type=bind,src=<candidate_root>/core-rs,dst=/workspace/core-rs,readonly",
        "--mount",
        "type=volume,src=<cargo_volume>,dst=/cargo",
        "--mount",
        "type=volume,src=<target_volume>,dst=/target",
        "--env",
        "CARGO_HOME=/cargo",
        "--env",
        "CARGO_TARGET_DIR=/target",
        "--workdir",
        "/workspace/core-rs",
        "<qualified_toolchain_id>",
        "cargo",
        "fmt",
        "--all",
        "--check",
    ),
    "fmt.qualified": ("docker", "inspect", "--type", "container", "<fmt_cid>"),
    "fmt.start": ("docker", "start", "--attach", "--interactive", "<fmt_cid>"),
    "fmt.terminal": ("docker", "inspect", "--type", "container", "<fmt_cid>"),
    "fmt.remove": ("docker", "rm", "<fmt_cid>"),
    "fmt.absent": ("docker", "inspect", "--type", "container", "<fmt_cid>"),
    "fmt.gap": ("docker", "inspect", "--type", "container", "<fmt_name>"),
    "fmt.prekill": ("docker", "inspect", "--type", "container", "<fmt_cid>"),
    "fmt.kill": ("docker", "kill", "<fmt_cid>"),
    "fmt.postkill": ("docker", "inspect", "--type", "container", "<fmt_cid>"),
    "clippy.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<clippy_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=clippy",
        "--network",
        "none",
        "--mount",
        "type=bind,src=<candidate_root>/core-rs,dst=/workspace/core-rs,readonly",
        "--mount",
        "type=volume,src=<cargo_volume>,dst=/cargo",
        "--mount",
        "type=volume,src=<target_volume>,dst=/target",
        "--env",
        "CARGO_HOME=/cargo",
        "--env",
        "CARGO_TARGET_DIR=/target",
        "--workdir",
        "/workspace/core-rs",
        "<qualified_toolchain_id>",
        "cargo",
        "clippy",
        "--locked",
        "--offline",
        "--workspace",
        "--all-targets",
        "--all-features",
        "--",
        "-D",
        "warnings",
    ),
    "clippy.qualified": ("docker", "inspect", "--type", "container", "<clippy_cid>"),
    "clippy.start": ("docker", "start", "--attach", "--interactive", "<clippy_cid>"),
    "clippy.terminal": ("docker", "inspect", "--type", "container", "<clippy_cid>"),
    "clippy.remove": ("docker", "rm", "<clippy_cid>"),
    "clippy.absent": ("docker", "inspect", "--type", "container", "<clippy_cid>"),
    "clippy.gap": ("docker", "inspect", "--type", "container", "<clippy_name>"),
    "clippy.prekill": ("docker", "inspect", "--type", "container", "<clippy_cid>"),
    "clippy.kill": ("docker", "kill", "<clippy_cid>"),
    "clippy.postkill": ("docker", "inspect", "--type", "container", "<clippy_cid>"),
    "default_tests.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<default_tests_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=default_tests",
        "--network",
        "none",
        "--mount",
        "type=bind,src=<candidate_root>/core-rs,dst=/workspace/core-rs,readonly",
        "--mount",
        "type=volume,src=<cargo_volume>,dst=/cargo",
        "--mount",
        "type=volume,src=<target_volume>,dst=/target",
        "--env",
        "CARGO_HOME=/cargo",
        "--env",
        "CARGO_TARGET_DIR=/target",
        "--workdir",
        "/workspace/core-rs",
        "<qualified_toolchain_id>",
        "cargo",
        "test",
        "--locked",
        "--offline",
        "--workspace",
    ),
    "default_tests.qualified": ("docker", "inspect", "--type", "container", "<default_tests_cid>"),
    "default_tests.start": ("docker", "start", "--attach", "--interactive", "<default_tests_cid>"),
    "default_tests.terminal": ("docker", "inspect", "--type", "container", "<default_tests_cid>"),
    "default_tests.remove": ("docker", "rm", "<default_tests_cid>"),
    "default_tests.absent": ("docker", "inspect", "--type", "container", "<default_tests_cid>"),
    "default_tests.gap": ("docker", "inspect", "--type", "container", "<default_tests_name>"),
    "default_tests.prekill": ("docker", "inspect", "--type", "container", "<default_tests_cid>"),
    "default_tests.kill": ("docker", "kill", "<default_tests_cid>"),
    "default_tests.postkill": ("docker", "inspect", "--type", "container", "<default_tests_cid>"),
    "release.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<release_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=release",
        "--network",
        "none",
        "--mount",
        "type=bind,src=<candidate_root>/core-rs,dst=/workspace/core-rs,readonly",
        "--mount",
        "type=volume,src=<cargo_volume>,dst=/cargo",
        "--mount",
        "type=volume,src=<target_volume>,dst=/target",
        "--env",
        "CARGO_HOME=/cargo",
        "--env",
        "CARGO_TARGET_DIR=/target",
        "--workdir",
        "/workspace/core-rs",
        "<qualified_toolchain_id>",
        "cargo",
        "build",
        "--locked",
        "--offline",
        "--release",
        "-p",
        "bifrost-db",
        "--features",
        "workflow-sql-parity",
        "--example",
        "workflow_sql_vectors",
    ),
    "release.qualified": ("docker", "inspect", "--type", "container", "<release_cid>"),
    "release.start": ("docker", "start", "--attach", "--interactive", "<release_cid>"),
    "release.terminal": ("docker", "inspect", "--type", "container", "<release_cid>"),
    "release.remove": ("docker", "rm", "<release_cid>"),
    "release.absent": ("docker", "inspect", "--type", "container", "<release_cid>"),
    "release.gap": ("docker", "inspect", "--type", "container", "<release_name>"),
    "release.prekill": ("docker", "inspect", "--type", "container", "<release_cid>"),
    "release.kill": ("docker", "kill", "<release_cid>"),
    "release.postkill": ("docker", "inspect", "--type", "container", "<release_cid>"),
    "schema_before.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<schema_before_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=schema_before",
        "--network",
        "<owned_original_network_id>",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "--env-file",
        "<rust_env_file>",
        "--mount",
        "type=bind,src=<qualified_binary>,dst=/f4/bin/workflow_sql_vectors,readonly",
        "--entrypoint",
        "/f4/bin/workflow_sql_vectors",
        "<qualified_api_id>",
        "observe-schema",
    ),
    "schema_before.qualified": ("docker", "inspect", "--type", "container", "<schema_before_cid>"),
    "schema_before.start": ("docker", "start", "--attach", "--interactive", "<schema_before_cid>"),
    "schema_before.terminal": ("docker", "inspect", "--type", "container", "<schema_before_cid>"),
    "schema_before.remove": ("docker", "rm", "<schema_before_cid>"),
    "schema_before.absent": ("docker", "inspect", "--type", "container", "<schema_before_cid>"),
    "schema_before.gap": ("docker", "inspect", "--type", "container", "<schema_before_name>"),
    "schema_before.prekill": ("docker", "inspect", "--type", "container", "<schema_before_cid>"),
    "schema_before.kill": ("docker", "kill", "<schema_before_cid>"),
    "schema_before.postkill": ("docker", "inspect", "--type", "container", "<schema_before_cid>"),
    "schema_after.create": (
        "docker",
        "create",
        "--interactive",
        "--name",
        "<schema_after_name>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=schema_after",
        "--network",
        "<owned_original_network_id>",
        "--user",
        "<actual_host_uid>:<actual_host_gid>",
        "--env-file",
        "<rust_env_file>",
        "--mount",
        "type=bind,src=<qualified_binary>,dst=/f4/bin/workflow_sql_vectors,readonly",
        "--entrypoint",
        "/f4/bin/workflow_sql_vectors",
        "<qualified_api_id>",
        "observe-schema",
    ),
    "schema_after.qualified": ("docker", "inspect", "--type", "container", "<schema_after_cid>"),
    "schema_after.start": ("docker", "start", "--attach", "--interactive", "<schema_after_cid>"),
    "schema_after.terminal": ("docker", "inspect", "--type", "container", "<schema_after_cid>"),
    "schema_after.remove": ("docker", "rm", "<schema_after_cid>"),
    "schema_after.absent": ("docker", "inspect", "--type", "container", "<schema_after_cid>"),
    "schema_after.gap": ("docker", "inspect", "--type", "container", "<schema_after_name>"),
    "schema_after.prekill": ("docker", "inspect", "--type", "container", "<schema_after_cid>"),
    "schema_after.kill": ("docker", "kill", "<schema_after_cid>"),
    "schema_after.postkill": ("docker", "inspect", "--type", "container", "<schema_after_cid>"),
    "release.copy": (
        "docker",
        "cp",
        "<release_cid>:/target/release/examples/workflow_sql_vectors",
        "<owned_binary_path>",
    ),
    "image.api.before_use": ("docker", "image", "inspect", "<configured_api_tag>"),
    "image.api.after_use": ("docker", "image", "inspect", "<owned_api_id>", "<actual_pgbouncer_image_id>"),
    "image.api.remove": ("docker", "image", "rm", "<owned_api_id>"),
    "image.api.absent": ("docker", "image", "inspect", "<owned_api_id>"),
    "image.api.creation_gap": ("docker", "image", "inspect", "<configured_api_tag>"),
    "image.toolchain.before_use": ("docker", "image", "inspect", "<owned_toolchain_id>"),
    "image.toolchain.after_use": ("docker", "image", "inspect", "<owned_toolchain_id>"),
    "image.toolchain.remove": ("docker", "image", "rm", "<owned_toolchain_id>"),
    "image.toolchain.absent": ("docker", "image", "inspect", "<owned_toolchain_id>"),
    "image.toolchain.creation_gap": ("docker", "image", "inspect", "<configured_toolchain_tag>"),
    "log_restore.create": (
        "docker",
        "create",
        "--name",
        "<log_restore_name>",
        "--label",
        "com.docker.compose.project=<owned_project>",
        "--label",
        "bifrost.f4.invocation=<invocation>",
        "--label",
        "bifrost.f4.candidate=<actual_head>",
        "--label",
        "bifrost.f4.purpose=log-restore",
        "--pull",
        "never",
        "--network",
        "none",
        "--user",
        "0:0",
        "--read-only",
        "--no-healthcheck",
        "--restart",
        "no",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "DAC_READ_SEARCH",
        "--pids-limit",
        "8",
        "--memory",
        "64m",
        "--cpus",
        "0.25",
        "--mount",
        "type=bind,src=<qualified_owned_log_dir>,dst=/task-log",
        "--entrypoint",
        "/usr/bin/chown",
        "<owned_api_id>",
        "--recursive",
        "--no-dereference",
        "--preserve-root",
        "-P",
        "--",
        "+<actual_host_uid>:+<actual_host_gid>",
        "/task-log",
    ),
    "log_restore.qualified": ("docker", "inspect", "--type", "container", "<log_restore_cid>"),
    "log_restore.start": ("docker", "start", "--attach", "<log_restore_cid>"),
    "log_restore.terminal": ("docker", "inspect", "--type", "container", "<log_restore_cid>"),
    "log_restore.remove": ("docker", "rm", "<log_restore_cid>"),
    "log_restore.absent": ("docker", "inspect", "--type", "container", "<log_restore_cid>"),
    "log_restore.gap": ("docker", "inspect", "--type", "container", "<log_restore_name>"),
    "log_restore.prekill": ("docker", "inspect", "--type", "container", "<log_restore_cid>"),
    "log_restore.kill": ("docker", "kill", "<log_restore_cid>"),
    "log_restore.postkill": ("docker", "inspect", "--type", "container", "<log_restore_cid>"),
}

ROSTER_LIMIT = 217
CONTROL_LIMIT = 4096
ROW_LIMIT = 65536
SOURCE_LIMIT = 4 * 1024 * 1024
BINARY_LIMIT = 64 * 1024 * 1024
IPC_SCHEMA = "bifrost.test.workflow-commit-ipc/v1"
OBSERVER_SCHEMA = "bifrost.test.workflow-commit-observer/v1"
STACK_SERVICES = (
    "postgres",
    "pgbouncer",
    "rabbitmq",
    "redis",
    "seaweedfs",
    "init",
    "api",
    "api-replica",
    "worker",
    "scheduler",
    "scheduler-fixtures",
)


class Failure(Exception):
    """Private static source label, never raw driver/configuration text."""

    def __init__(self, label):
        super().__init__()
        self.label = label


def require(condition, label):
    if not condition:
        raise Failure(label)


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), "closed_keys")
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "integer")
    return value


def pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, "duplicate_key")
        result[key] = value
    return result


def finite(value, depth=0):
    require(depth <= 64, "json_depth")
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str and "\0" not in key, "json_key")
            finite(child, depth + 1)
    elif type(value) is list:
        for child in value:
            finite(child, depth + 1)
    elif type(value) is float:
        require(math.isfinite(value), "json_nonfinite")
    else:
        require(value is None or type(value) in (str, int, bool), "json_type")
        if type(value) is str:
            require("\0" not in value, "json_nul")


def decode(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "json_bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _text: (_ for _ in ()).throw(Failure("json_nonfinite")),
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Failure("json_encoding") from error
    finite(value)
    return value


def encode(value, limit):
    finite(value)
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    require(0 < len(raw) <= limit, "encoded_bound")
    return raw


def public_json(value, limit):
    integer(limit, 1, 65536)
    raw = encode(value, limit - 1) + b"\n"
    require(len(raw) <= limit and raw.endswith(b"\n"), "public_json_bound")
    return raw


def software_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_uuid(value):
    require(
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is not None,
        "uuid",
    )
    require(str(uuid.UUID(value)) == value, "uuid")
    return value


def first_error(original, secondary):
    return original if original is not None else secondary


def file_identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def read_software(path, limit, *, allow_empty=False):
    """Acquire/retain one FD; complete bytes and independent close are required."""
    before = os.lstat(path)
    require(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and (0 if allow_empty else 1) <= before.st_size <= limit,
        "software_file",
    )
    descriptor = None
    original = result = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        require(file_identity(os.fstat(descriptor)) == file_identity(before), "software_open")
        data = bytearray()
        while len(data) <= before.st_size:
            chunk = os.read(descriptor, min(65536, before.st_size + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) == before.st_size, "software_length")
        require(
            file_identity(os.fstat(descriptor)) == file_identity(before)
            and file_identity(os.lstat(path)) == file_identity(before),
            "software_stable",
        )
        result = bytes(data)
    except BaseException as error:
        original = error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                original = first_error(original, error)
    if original is not None:
        raise original
    return result


class Files:
    """Only returned task-owned identities; failed close never licenses FD retry."""

    def __init__(self):
        self.entries = []
        self.failed = False

    def directory(self, path, mode):
        entry = {"path": path, "kind": "directory", "identity": None, "removed": False}
        self.entries.append(entry)
        os.mkdir(path, mode)
        observed = os.lstat(path)
        require(
            stat.S_ISDIR(observed.st_mode)
            and observed.st_uid == os.getuid()
            and stat.S_IMODE(observed.st_mode) == mode,
            "private_directory",
        )
        entry["identity"] = (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid)
        return path

    def file(self, path, data, *, retain=False):
        descriptor = None
        original = None
        entry = {
            "path": path,
            "kind": "file",
            "identity": None,
            "descriptor": None,
            "close_attempted": False,
            "closed": False,
            "removed": False,
        }
        self.entries.append(entry)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            entry["descriptor"] = descriptor
            observed = os.fstat(descriptor)
            require(
                stat.S_ISREG(observed.st_mode)
                and observed.st_uid == os.getuid()
                and observed.st_nlink == 1
                and observed.st_size == 0
                and stat.S_IMODE(observed.st_mode) == 0o600,
                "private_file",
            )
            entry["identity"] = (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid)
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset : offset + 65536])
                require(type(written) is int and written > 0, "private_write")
                offset += written
            os.fsync(descriptor)
            current = os.fstat(descriptor)
            require(
                current.st_size == len(data)
                and current.st_nlink == 1
                and file_identity(current) == file_identity(os.lstat(path)),
                "private_file_stable",
            )
        except BaseException as error:
            original = error
        finally:
            if descriptor is not None and (not retain or original is not None):
                entry["close_attempted"] = True
                try:
                    os.close(descriptor)
                    entry["closed"] = True
                    entry["descriptor"] = None
                except BaseException as error:
                    self.failed = True
                    original = first_error(original, error)
        if original is not None:
            raise original
        return path

    def remove(self, entry):
        require(not entry["removed"] and entry["identity"] is not None, "file_removal_identity")
        if entry["kind"] == "file":
            require(entry["closed"] is True, "file_close_unknown")
        observed = os.lstat(entry["path"])
        require(
            (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid) == entry["identity"],
            "file_removal_replaced",
        )
        if entry["kind"] == "directory":
            os.rmdir(entry["path"])
        else:
            os.unlink(entry["path"])
        try:
            os.lstat(entry["path"])
        except FileNotFoundError:
            entry["removed"] = True
        else:
            raise Failure("file_removal_present")


class Peer:
    """One qualified Unix peer; no reconnect, role inference or sequence renewal."""

    def __init__(self, connection, role, invocation):
        self.connection = connection
        self.role = role
        self.invocation = canonical_uuid(invocation)
        self.sent = self.received = 0
        self.input = bytearray()
        self.output = bytearray()
        self.length = None
        self.eof = self.closed = self.failed = False
        self.qualified = False
        self.credentials = None

    def qualify(self, pid, uid, gid):
        require(not self.qualified, "peer_requalification")
        raw = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        observed = struct.unpack("3i", raw)
        require(observed == (pid, uid, gid), "peer_credentials")
        self.credentials = observed
        self.qualified = True

    def send(self, kind, body, data=False):
        require(self.qualified and not self.closed and not self.eof and not self.output, "peer_send_state")
        integer(self.sent, 0, 2**64 - 2)
        raw = encode(
            {
                "schema": OBSERVER_SCHEMA if self.role == "observer" else IPC_SCHEMA,
                "lane": "supervisor" if self.role == "observer" else self.role,
                "invocation": self.invocation,
                "seq": self.sent + 1,
                "kind": kind,
                "body": body,
            },
            ROW_LIMIT if data else CONTROL_LIMIT,
        )
        self.output.extend(struct.pack("!I", len(raw)))
        self.output.extend(raw)
        self.sent += 1

    def write(self):
        require(not self.closed and bool(self.output), "peer_write_state")
        try:
            written = self.connection.send(self.output)
        except BlockingIOError:
            return
        require(type(written) is int and 0 < written <= len(self.output), "peer_write")
        del self.output[:written]

    def read(self):
        require(not self.closed and not self.eof, "peer_read_state")
        maximum = 4 - len(self.input) if self.length is None else self.length + 4 - len(self.input)
        require(maximum > 0, "peer_frame_pending")
        try:
            raw = self.connection.recv(min(16384, maximum))
        except BlockingIOError:
            return
        if not raw:
            self.eof = True
            require(not self.input and self.length is None, "peer_partial_eof")
            return
        self.input.extend(raw)
        if self.length is None and len(self.input) == 4:
            self.length = struct.unpack("!I", self.input)[0]
            integer(self.length, 1, ROW_LIMIT)

    def receive(self, kind, keys, data=False):
        require(
            self.qualified and not self.closed and self.length is not None and len(self.input) == self.length + 4,
            "peer_frame_incomplete",
        )
        require(self.length <= (ROW_LIMIT if data else CONTROL_LIMIT), "peer_frame_bound")
        value = closed(
            decode(bytes(self.input[4:]), self.length), ("schema", "lane", "invocation", "seq", "kind", "body")
        )
        require(
            value["schema"] == (OBSERVER_SCHEMA if self.role == "observer" else IPC_SCHEMA)
            and value["lane"] == self.role
            and value["invocation"] == self.invocation
            and value["kind"] == kind,
            "peer_frame_identity",
        )
        integer(self.received, 0, 2**64 - 2)
        integer(value["seq"], self.received + 1, self.received + 1)
        body = closed(value["body"], keys)
        self.received += 1
        self.input.clear()
        self.length = None
        return body

    def close(self):
        require(not self.closed, "peer_close_repeated")
        try:
            self.connection.close()
            require(self.connection.fileno() == -1, "peer_close_unknown")
            self.closed = True
        except BaseException:
            self.failed = True
            raise


class Native:
    """One roster launch; acquisition facts survive every later setup failure."""

    def __init__(self, label, end, cleanup_end, limit):
        self.label, self.end, self.cleanup_end = label, end, cleanup_end
        self.limit = (limit, limit) if type(limit) is int else limit
        self.process = None
        self.pgid = None
        self.error = None
        self.returncode = None
        self.stdout_eof = self.stderr_eof = None
        self.group_settled = self.fd_settled = None
        self.settled = False
        self.kill_attempted = False
        self.handles = []
        self.captures = []
        self.input = b""
        self.offset = 0
        self.bytes = [0, 0]
        self.stdout = self.stderr = None

    def record(self):
        code = self.returncode
        if code is not None:
            code = code if code >= 0 else 128 - code if self.settled else None
            require(code is None or (type(code) is int and 0 <= code <= 255), "native_exit_range")
        return {
            "label": self.label,
            "settled": self.settled,
            "native_exit": code,
            "stdout_eof": self.stdout_eof,
            "stderr_eof": self.stderr_eof,
            "group_settled": self.group_settled,
            "fd_settled": self.fd_settled,
        }


def group_present(group):
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


class Controller:
    """Finite commands and four peers; original ends also bound all cleanup."""

    def __init__(self):
        # This initializer performs no fallible acquisition. The caller retains
        # this object before private directories, selectors or handlers exist.
        self.started_at = time.monotonic()
        self.normal_end = self.started_at + 2460
        self.whole_end = self.started_at + 2700
        self.stages = {}
        self.used = set()
        self.native = []
        self.selector = None
        self.files = Files()
        self.capture_directory = None
        self.captured_bytes = 0
        self.first = None
        self.cleanup_failed = False
        self.emergency_end = None
        self.peers = {}
        self.listeners = {}
        self.listener_records = []
        self.accepted_records = []
        self.invocation = str(uuid.uuid4())
        self.values = {}
        self.source = {"head": None, "tree": None, "main": None, "workspace": None}
        self.stage = None
        self.case_start = self.work_end = self.case_end = None
        self.cycles = []
        self.project_inventories = {}
        self.peer_sources = {}
        self.frontend_seconds = 0.0

    def fail(self, error, *, cleanup=False):
        if self.first is None:
            self.emergency_end = min(time.monotonic() + 240, self.whole_end)
        self.first = first_error(self.first, error)
        if cleanup:
            self.cleanup_failed = True

    def stage_end(self, name, seconds, *, cleanup=False):
        require(name not in self.stages, "stage_reentry")
        end = min(time.monotonic() + seconds, self.whole_end if cleanup else self.normal_end)
        self.stages[name] = end
        require(time.monotonic() < end, "stage_expired")
        return end

    def argv(self, label):
        require(label in ROSTER, "command_label")
        result = []
        for template in ROSTER[label]:
            exact = re.fullmatch(r"<([^<>]+)>", template)
            if exact is not None and type(self.values.get(exact[1])) is tuple:
                result.extend(self.values[exact[1]])
                continue

            def substitute(match):
                value = self.values.get(match[1])
                require(type(value) is str and value and "\0" not in value, "command_argument_unavailable")
                return value

            value = re.sub(r"<([^<>]+)>", substitute, template)
            require("\0" not in value, "command_argument")
            result.append(value)
        require(0 < len(result) <= 256 and all(type(value) is str for value in result), "command_argv")
        return result

    def _capture(self, native, index):
        path = self.capture_directory / f"{len(self.native) - 1:03d}-{index}"
        entry = {
            "path": path,
            "kind": "file",
            "identity": None,
            "descriptor": None,
            "close_attempted": False,
            "closed": False,
            "removed": False,
        }
        self.files.entries.append(entry)
        native.captures.append(entry)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        entry["descriptor"] = descriptor
        observed = os.fstat(descriptor)
        require(
            stat.S_ISREG(observed.st_mode)
            and observed.st_uid == os.getuid()
            and observed.st_nlink == 1
            and observed.st_size == 0
            and stat.S_IMODE(observed.st_mode) == 0o600,
            "capture_acquisition",
        )
        entry["identity"] = (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid)
        return entry

    def launch(self, label, end, environment, *, stdin=b"", limit=16 * 1024 * 1024, cleanup=False, cleanup_end=None):
        require(label in ROSTER and label not in self.used and len(self.used) < ROSTER_LIMIT, "command_one_use")
        require(cleanup or self.first is None, "positive_admission_after_failure")
        require(type(stdin) is bytes and len(stdin) <= 16 * 1024 * 1024, "native_stdin")
        require(
            (type(limit) is int and 0 < limit <= 16 * 1024 * 1024)
            or (
                type(limit) is tuple
                and len(limit) == 2
                and all(type(value) is int and 0 < value <= 16 * 1024 * 1024 for value in limit)
            ),
            "native_capture_allocation",
        )
        require(time.monotonic() < min(end, self.whole_end), "native_deadline")
        arguments = self.argv(label)
        self.used.add(label)  # No failed Popen permits label/count renewal.
        native = Native(
            label,
            min(end, self.whole_end),
            min(cleanup_end if cleanup_end is not None else self.whole_end, self.whole_end),
            limit,
        )
        self.native.append(native)
        native.input = stdin
        self.stage = label
        try:
            for index in (0, 1):
                self._capture(native, index)
            process = subprocess.Popen(
                arguments,
                cwd=self.values["candidate_root"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            native.process = process
            native.pgid = process.pid
            # Every returned pipe is retained before the first configuration.
            for index, handle in enumerate((process.stdout, process.stderr, process.stdin)):
                native.handles.append(
                    {"handle": handle, "index": index, "registered": False, "close_attempted": False, "closed": False}
                )
            for entry in native.handles:
                handle, index = entry["handle"], entry["index"]
                require(handle is not None, "native_pipe_missing")
                os.set_blocking(handle.fileno(), False)
                if index != 2 or stdin:
                    self.selector.register(
                        handle, selectors.EVENT_WRITE if index == 2 else selectors.EVENT_READ, ("native", native, entry)
                    )
                    entry["registered"] = True
                else:
                    self._close_handle(native, entry)
        except BaseException as error:
            native.error = first_error(native.error, error)
            self.fail(error, cleanup=cleanup)
        return native

    def _close_handle(self, native, entry):
        original = None
        if entry["registered"]:
            try:
                self.selector.unregister(entry["handle"])
                entry["registered"] = False
            except BaseException as error:
                original = error
        if not entry["close_attempted"]:
            entry["close_attempted"] = True
            try:
                entry["handle"].close()
                entry["closed"] = entry["handle"].closed is True
                require(entry["closed"], "native_pipe_close_unknown")
            except BaseException as error:
                original = first_error(original, error)
        if original is not None:
            native.error = first_error(native.error, original)
            self.fail(original, cleanup=True)

    def _close_capture(self, native, entry):
        if entry["descriptor"] is not None and not entry["close_attempted"]:
            entry["close_attempted"] = True
            try:
                os.close(entry["descriptor"])
                entry["closed"] = True
                entry["descriptor"] = None
            except BaseException as error:
                # A possibly closed numeric FD must never be retried after reuse.
                native.error = first_error(native.error, error)
                self.files.failed = True
                self.fail(error, cleanup=True)

    def _native_io(self, native, entry):
        handle, index = entry["handle"], entry["index"]
        try:
            if index == 2:
                remaining = native.input[native.offset : native.offset + 65536]
                sent = os.write(handle.fileno(), remaining)
                require(type(sent) is int and 0 < sent <= len(remaining), "native_stdin_write")
                native.offset += sent
                if native.offset == len(native.input):
                    self._close_handle(native, entry)
                return
            available = min(native.limit[index] - native.bytes[index], 64 * 1024 * 1024 - self.captured_bytes)
            raw = os.read(handle.fileno(), min(65536, available + 1))
            if not raw:
                if index == 0:
                    native.stdout_eof = True
                else:
                    native.stderr_eof = True
                self._close_handle(native, entry)
                self._close_capture(native, native.captures[index])
                return
            native.bytes[index] += len(raw)
            self.captured_bytes += len(raw)
            require(
                native.bytes[index] <= native.limit[index] and self.captured_bytes <= 64 * 1024 * 1024,
                "native_capture_overflow",
            )
            descriptor = native.captures[index]["descriptor"]
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                require(type(written) is int and written > 0, "capture_write")
                offset += written
        except BlockingIOError:
            return
        except BaseException as error:
            native.error = first_error(native.error, error)
            self.fail(error)
            self._close_handle(native, entry)
            if index < 2:
                self._close_capture(native, native.captures[index])

    def settle(self, native):
        # Deadline expiry forbids positive admission, never independent polling,
        # pipe/capture closure or another owned cleanup command's dispatch.
        process = native.process
        if process is None:
            for entry in native.captures:
                self._close_capture(native, entry)
            native.fd_settled = all(entry["closed"] for entry in native.captures)
            return
        try:
            status = process.poll()
            if status is not None:
                native.returncode = process.wait(timeout=0)
                native.group_settled = not group_present(native.pgid)
                if not native.group_settled and time.monotonic() >= native.end:
                    native.error = first_error(native.error, Failure("native_group_expired"))
                    self.fail(native.error)
                    if time.monotonic() < native.cleanup_end and not native.kill_attempted:
                        native.kill_attempted = True
                        os.killpg(native.pgid, signal.SIGKILL)
            elif time.monotonic() >= native.end or native.error is not None:
                error = Failure("native_expired") if native.error is None else native.error
                native.error = first_error(native.error, error)
                self.fail(error)
                if time.monotonic() < native.cleanup_end and not native.kill_attempted:
                    native.kill_attempted = True
                    os.killpg(native.pgid, signal.SIGKILL)
            if time.monotonic() >= native.cleanup_end:
                for entry in native.handles:
                    self._close_handle(native, entry)
                for entry in native.captures:
                    self._close_capture(native, entry)
            streams_closed = all(entry["closed"] for entry in native.handles if entry["index"] < 2)
            if native.returncode is not None and streams_closed:
                for entry in native.handles:
                    self._close_handle(native, entry)
                for entry in native.captures:
                    self._close_capture(native, entry)
                native.fd_settled = all(entry["closed"] for entry in native.handles + native.captures)
                native.settled = native.group_settled is True and native.fd_settled is True
        except BaseException as error:
            native.error = first_error(native.error, error)
            self.fail(error, cleanup=True)

    def pump(self, end):
        require(self.selector is not None, "selector_unavailable")
        for native in self.native:
            if not native.settled:
                self.settle(native)
        timeout = max(0.0, min(0.02, end - time.monotonic()))
        for key, mask in self.selector.select(timeout):
            tag, owner, entry = key.data
            if tag == "native":
                self._native_io(owner, entry)
            elif tag == "listener":
                self.accept(owner)
            elif tag == "peer":
                try:
                    if mask & selectors.EVENT_WRITE:
                        owner.write()
                    if mask & selectors.EVENT_READ:
                        owner.read()
                    self.peer_interest(owner)
                except BaseException as error:
                    owner.failed = True
                    self.fail(error)
                    try:
                        self.selector.unregister(owner.connection)
                    except BaseException as secondary:
                        self.fail(secondary, cleanup=True)
            else:
                raise Failure("selector_role")
        for native in self.native:
            if not native.settled:
                self.settle(native)

    def wait(self, native):
        while not native.settled and time.monotonic() < native.cleanup_end:
            self.pump(native.cleanup_end)
            if native.process is None:
                break
            if (
                native.returncode is not None
                and native.group_settled is True
                and all(entry["close_attempted"] for entry in native.handles + native.captures)
            ):
                break
        if native.error is not None:
            raise native.error
        require(
            native.settled
            and native.stdout_eof is True
            and native.stderr_eof is True
            and native.offset == len(native.input),
            "native_unsettled",
        )
        return native

    def run(
        self,
        label,
        end,
        environment,
        *,
        stdin=b"",
        allowed=(0,),
        cleanup=False,
        limit=16 * 1024 * 1024,
        cleanup_end=None,
    ):
        native = self.launch(
            label, end, environment, stdin=stdin, cleanup=cleanup, limit=limit, cleanup_end=cleanup_end
        )
        self.wait(native)
        require(native.returncode in allowed, "native_status")
        return native

    def capture(self, native, index=0):
        require(
            native.settled and (native.stdout_eof if index == 0 else native.stderr_eof) is True, "capture_incomplete"
        )
        entry = native.captures[index]
        require(entry["closed"] is True and entry["identity"] is not None, "capture_close_unknown")
        before = os.lstat(entry["path"])
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_size == native.bytes[index]
            and (before.st_dev, before.st_ino, before.st_uid, before.st_gid) == entry["identity"],
            "capture_identity",
        )
        descriptor = None
        original = result = None
        try:
            descriptor = os.open(entry["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            require(file_identity(os.fstat(descriptor)) == file_identity(before), "capture_read_identity")
            data = bytearray()
            while len(data) <= before.st_size:
                raw = os.read(descriptor, min(65536, before.st_size + 1 - len(data)))
                if not raw:
                    break
                data.extend(raw)
            require(
                len(data) == before.st_size
                and file_identity(os.fstat(descriptor)) == file_identity(before)
                and file_identity(os.lstat(entry["path"])) == file_identity(before),
                "capture_read_stable",
            )
            result = bytes(data)
        except BaseException as error:
            original = error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    self.fail(error, cleanup=True)
                    original = first_error(original, error)
        if original is not None:
            raise original
        return result

    def listener(self, role, path):
        require(role in ("observer", "relay", "python", "rust") and role not in self.listeners, "listener_role")
        require(not os.path.lexists(path), "listener_preexisting")
        record = {
            "path": path,
            "kind": "socket",
            "identity": None,
            "removed": False,
            "closed": False,
            "close_attempted": False,
            "socket": None,
            "registered": False,
        }
        self.listener_records.append(record)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        record["socket"] = listener
        self.listeners[role] = record
        try:
            listener.setblocking(False)
            listener.bind(str(path))
            observed = os.lstat(path)
            require(stat.S_ISSOCK(observed.st_mode) and observed.st_uid == os.getuid(), "listener_identity")
            record["identity"] = (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid)
            os.chmod(path, 0o666 if role == "observer" else 0o600, follow_symlinks=False)
            current = os.lstat(path)
            require(
                (current.st_dev, current.st_ino, current.st_uid, current.st_gid) == record["identity"]
                and stat.S_ISSOCK(current.st_mode)
                and stat.S_IMODE(current.st_mode) == (0o666 if role == "observer" else 0o600),
                "listener_mode",
            )
            listener.listen(1)
            self.selector.register(listener, selectors.EVENT_READ, ("listener", role, record))
            record["registered"] = True
        except BaseException as error:
            self.fail(error)
            raise

    def accept(self, role):
        record = self.listeners[role]
        require(role not in self.peers, "extra_peer")
        connection, _address = record["socket"].accept()
        acquired = {"connection": connection, "role": role, "peer": None, "close_attempted": False, "closed": False}
        self.accepted_records.append(acquired)
        peer = Peer(connection, role, self.invocation)
        acquired["peer"] = peer
        self.peers[role] = peer  # Retain the returned socket before configuration.
        try:
            connection.setblocking(False)
            self.selector.unregister(record["socket"])
            record["registered"] = False
        except BaseException as error:
            peer.failed = True
            self.fail(error)
            raise

    def peer_interest(self, peer):
        if peer.closed or peer.failed:
            return
        complete = peer.length is not None and len(peer.input) == peer.length + 4
        mask = selectors.EVENT_READ if not peer.eof and not complete else 0
        if peer.output:
            mask |= selectors.EVENT_WRITE
        try:
            key = self.selector.get_key(peer.connection)
        except KeyError:
            key = None
        if mask:
            if key is None:
                self.selector.register(peer.connection, mask, ("peer", peer, None))
            else:
                self.selector.modify(peer.connection, mask, ("peer", peer, None))
        elif key is not None:
            self.selector.unregister(peer.connection)

    def await_peer(self, role, end):
        while role not in self.peers:
            require(time.monotonic() < end and self.first is None, "peer_start_deadline")
            self.pump(end)
        return self.peers[role]

    def send(self, peer, kind, body, end, *, data=False):
        require(time.monotonic() < end and self.first is None, "peer_send_deadline")
        peer.send(kind, body, data)
        self.peer_interest(peer)
        while peer.output:
            require(time.monotonic() < end and self.first is None, "peer_send_deadline")
            self.pump(end)

    def receive(self, peer, kind, keys, end, *, data=False):
        while peer.length is None or len(peer.input) != peer.length + 4:
            require(time.monotonic() < end and self.first is None and not peer.eof, "peer_receive_deadline")
            self.peer_interest(peer)
            self.pump(end)
        body = peer.receive(kind, keys, data)
        self.peer_interest(peer)
        return body


# Narrow static readbacks retained from the independently reviewed ordinary
# controller source. No ordinary controller command, schema or runtime proof is used.
FRONTEND_PROCESS = '\nset -eu\nrecord_file() {\n  tag=$1; path=$2; bound=$3\n  length=$(dd if="$path" bs="$((bound+1))" count=1 2>/dev/null | wc -c)\n  case "$length" in \'\'|*[!0-9]*) exit 1;; esac\n  [ "$length" -le "$bound" ] || exit 1\n  printf \'bifrost-proc/v1 %s %s\\n\' "$tag" "$length"\n  dd if="$path" bs="$((bound+1))" count=1 2>/dev/null\n  printf \'\\n\'\n}\nrecord_value() {\n  tag=$1; value=$2\n  [ "${#value}" -le 4096 ] || exit 1\n  printf \'bifrost-proc/v1 %s %s\\n%s\\n\' "$tag" "${#value}" "$value"\n}\nrecord_file stat /proc/1/stat 4096\nrecord_file cmdline /proc/1/cmdline 4096\nexe=$(readlink /proc/1/exe)\ncwd=$(readlink /proc/1/cwd)\nnetns=$(readlink /proc/1/ns/net)\n[ "$exe" = /usr/bin/pgbouncer ] || exit 1\nrecord_value exe "$exe"\nrecord_value cwd "$cwd"\nrecord_value netns "$netns"\nsize=$(stat -Lc \'%s\' /proc/1/exe)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 67108864 ] || exit 1\nrecord_value exe_bytes "$size"\nhash=$(sha256sum /proc/1/exe)\nhash=${hash%% *}\nrecord_value exe_sha256 "$hash"\nset -- /proc/1/fd/*\n[ "$#" -le 2048 ] || exit 1\nsockets=\'\'\ncount=0\nfor path do\n  target=$(readlink "$path")\n  case "$target" in\n    socket:\\[*\\])\n      count=$((count+1)); [ "$count" -le 128 ] || exit 1\n      sockets="${sockets}${target}\n";;\n  esac\ndone\nrecord_value sockets "$sockets"\nprintf \'bifrost-proc-end/v1\\n\'\n'

FRONTEND_TCP = '\nset -eu\nfor table in tcp tcp6; do\n  file=/proc/1/net/$table\n  length=$(dd if="$file" bs=65537 count=1 2>/dev/null | wc -c)\n  case "$length" in \'\'|*[!0-9]*) exit 1;; esac\n  [ "$length" -le 65536 ] || exit 1\n  printf \'bifrost-tcp/v1 %s %s\\n\' "$table" "$length"\n  dd if="$file" bs=65537 count=1 2>/dev/null\n  printf \'\\n\'\ndone\nprintf \'bifrost-tcp-end/v1\\n\'\n'

FRONTEND_CONFIG = '\nset -eu\nopened=0\ntrap \'first=$?; if [ "$opened" -eq 1 ]; then if exec 3<&-; then closed=0; else closed=$?; fi; if [ "$first" -eq 0 ] && [ "$closed" -ne 0 ]; then first=$closed; fi; fi; exit "$first"\' EXIT\npath=$1\n[ "$path" = /etc/pgbouncer/pgbouncer.ini ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\n[ -f "$path" ] && [ ! -L "$path" ] || exit 1\nformat=$(printf \'%%d\\t%%i\\t%%f\\t%%h\\t%%s\\t%%y\\t%%z\')\nexec 3< "$path"\nopened=1\nbefore=$(stat -Lc "$format" /proc/self/fd/3)\nactual=$(stat -Lc "$format" "$path")\n[ "$before" = "$actual" ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\nsize=$(stat -Lc \'%s\' /proc/self/fd/3)\nlinks=$(stat -Lc \'%h\' /proc/self/fd/3)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 65536 ] && [ "$links" = 1 ] || exit 1\nprintf \'bifrost-config-read/v1\\nbefore %s\\n%s\\npayload %s\\n\' "${#before}" "$before" "$size"\ndd bs=65537 count=1 <&3 2>/dev/null\nprintf \'\\n\'\nafter=$(stat -Lc "$format" /proc/self/fd/3)\nactual=$(stat -Lc "$format" "$path")\n[ "$before" = "$after" ] && [ "$before" = "$actual" ] && [ ! -L "$path" ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\nexec 3<&-\nopened=0\nprintf \'after %s\\n%s\\nend\\n\' "${#after}" "$after"\n'

FRONTEND_ENTRYPOINT = '\nset -eu\n[ -f /entrypoint.sh ] && [ ! -L /entrypoint.sh ] || exit 1\nsize=$(stat -c \'%s\' /entrypoint.sh)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 16384 ] || exit 1\nhash=$(sha256sum /entrypoint.sh)\nhash=${hash%% *}\nprintf \'%s %s\\n\' "$size" "$hash"\n'

DNS_READER = "\nimport json\nimport socket\nimport sys\nhost = sys.argv[1]\nif not host.isascii() or not 1 <= len(host) <= 253:\n    raise SystemExit(1)\naddresses = sorted({row[4][0] for row in socket.getaddrinfo(host,None,socket.AF_INET,socket.SOCK_STREAM)})\nif not 1 <= len(addresses) <= 8:\n    raise SystemExit(1)\nsys.stdout.write(json.dumps(addresses,ensure_ascii=True,separators=(',',':'))+'\\n')\n"

FRONTEND_DNS = "\nimport json\nimport socket\nimport sys\nhost = sys.argv[1]\nif not host.isascii() or not 1 <= len(host) <= 253:\n    raise SystemExit(1)\naddresses = sorted({row[4][0] for row in socket.getaddrinfo(host,None,socket.AF_INET,socket.SOCK_STREAM)})\nif not 1 <= len(addresses) <= 8:\n    raise SystemExit(1)\nsys.stdout.write(json.dumps(addresses,ensure_ascii=True,separators=(',',':'))+'\\n')\n"

SOURCE_READER = "# Proposed static read-only Docker-exec text; NOT executed or installed.\nimport hashlib\nimport json\nimport os\nimport stat\nimport sys\n\nrole = sys.argv[1] if len(sys.argv) == 2 else None\nif role not in ('observer', 'relay', 'python', 'rust'):\n    raise SystemExit(2)\npaths = (\n    '/app/src/config.py',\n    '/app/src/core/database.py',\n    '/app/src/jobs/consumers/workflow_execution.py',\n    '/app/bifrost/__init__.py',\n    '/app/bifrost/_sync.py',\n    '/app/shared/workspace_effects.py',\n    '/app/tests/parity/workflow_commit_fault.py',\n    '/app/tests/parity/workflow_commit_fault_child.py',\n    '/app/tests/diagnostics/workflow_commit_fault.py',\n)\nif role == 'rust':\n    paths += ('/f4/bin/workflow_sql_vectors',)\nrecords = []\nfor path in paths:\n    limit = 67108864 if path == '/f4/bin/workflow_sql_vectors' else 4194304\n    key = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)\n    before = os.lstat(path)\n    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:\n        raise SystemExit(2)\n    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n    original = None\n    try:\n        acquired = os.fstat(fd)\n        if not stat.S_ISREG(acquired.st_mode) or acquired.st_nlink != 1 or not 0 < acquired.st_size <= limit or key(before) != key(acquired):\n            raise SystemExit(2)\n        digest = hashlib.sha256()\n        size = 0\n        while True:\n            chunk = os.read(fd, 65536)\n            if not chunk:\n                break\n            size += len(chunk)\n            if size > limit:\n                raise SystemExit(2)\n            digest.update(chunk)\n        after_fd = os.fstat(fd)\n        after_path = os.lstat(path)\n        if key(acquired) != key(after_fd) or key(acquired) != key(after_path) or size != acquired.st_size:\n            raise SystemExit(2)\n        records.append({'path': path, 'sha256': digest.hexdigest(), 'bytes': size})\n    except BaseException as error:\n        original = error\n        raise\n    finally:\n        try:\n            os.close(fd)\n        except BaseException:\n            if original is None:\n                raise\nns = {}\nfor name in ('user', 'net', 'pid'):\n    value = os.stat('/proc/1/ns/' + name)\n    ns[name] = {'dev': value.st_dev, 'ino': value.st_ino}\nresult = {'schema': 'bifrost.private.f4-source-namespace/v1', 'role': role, 'uid': os.getuid(), 'gid': os.getgid(), 'reader_pid': os.getpid(), 'pid1_namespaces': ns, 'sources': records}\nraw = json.dumps(result, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\\n'\nif len(raw) > 8192:\n    raise SystemExit(2)\nsys.stdout.buffer.write(raw)\nsys.stdout.buffer.flush()\n"

ROW_COLUMNS = {
    "executions": [
        ("id", "uuid", False),
        ("workflow_name", "text", False),
        ("workflow_version", "text", True),
        ("status", "text", False),
        ("parameters", "jsonb", False),
        ("result", "jsonb", True),
        ("result_type", "text", True),
        ("variables", "jsonb", True),
        ("execution_context", "jsonb", True),
        ("error_message", "text", True),
        ("started_at", "utc_us", True),
        ("completed_at", "utc_us", True),
        ("duration_ms", "integer", True),
        ("peak_memory_bytes", "integer", True),
        ("process_rss_bytes", "integer", True),
        ("cpu_user_seconds", "float64_bits", True),
        ("cpu_system_seconds", "float64_bits", True),
        ("cpu_total_seconds", "float64_bits", True),
        ("time_saved", "integer", False),
        ("value", "decimal", False),
        ("executed_by", "uuid", True),
        ("executed_by_name", "text", False),
        ("organization_id", "uuid", True),
        ("form_id", "uuid", True),
        ("workflow_id", "uuid", True),
        ("solution_deployment_id", "uuid", True),
        ("runtime_mode", "text", False),
        ("runtime_evidence", "jsonb", True),
        ("runtime_evidence_hash", "text", True),
        ("dispatch_evidence", "jsonb", True),
        ("dispatch_evidence_hash", "text", True),
        ("retry_policy", "jsonb", False),
        ("attempt_tracking_version", "text", True),
        ("api_key_id", "uuid", True),
        ("is_local_execution", "bool", False),
        ("execution_model", "text", True),
        ("session_id", "uuid", True),
        ("created_at", "utc_us", False),
        ("scheduled_at", "utc_us", True),
    ],
    "attempts": [
        ("id", "uuid", False),
        ("execution_id", "uuid", False),
        ("attempt_number", "integer", False),
        ("claim_token", "uuid", True),
        ("status", "text", False),
        ("phase", "text", False),
        ("failure_phase", "text", True),
        ("failure_code", "text", True),
        ("worker_id", "text", True),
        ("worker_incarnation_id", "uuid", True),
        ("process_id", "text", True),
        ("runtime_mode", "text", True),
        ("runtime_evidence_hash", "text", True),
        ("dispatch_evidence_hash", "text", True),
        ("policy_digest", "text", True),
        ("policy_version", "text", False),
        ("published_at", "utc_us", True),
        ("claimed_at", "utc_us", True),
        ("started_at", "utc_us", True),
        ("heartbeat_at", "utc_us", True),
        ("completed_at", "utc_us", True),
        ("duration_ms", "integer", True),
        ("peak_memory_bytes", "integer", True),
        ("cpu_total_seconds", "float64_bits", True),
        ("created_at", "utc_us", False),
    ],
    "logs": [
        ("id", "integer", False),
        ("execution_id", "uuid", False),
        ("level", "text", False),
        ("message", "text", False),
        ("log_metadata", "jsonb", True),
        ("timestamp", "utc_us", False),
        ("sequence", "integer", False),
    ],
}


def validate_rows(value):
    closed(value, ROW_COLUMNS)
    for table, columns in ROW_COLUMNS.items():
        rows = value[table]
        require(type(rows) is list and len(rows) <= (1024 if table == "logs" else 2), "row_count")
        identities = []
        for row in rows:
            closed(row, (name for name, _kind, _nullable in columns))
            for name, kind, nullable in columns:
                cell = closed(row[name], ("sql_null", "value"))
                require(type(cell["sql_null"]) is bool, "row_null_type")
                actual = cell["value"]
                if cell["sql_null"]:
                    require(nullable and actual is None, "row_null")
                    continue
                require(actual is not None, "row_value")
                if kind == "uuid":
                    canonical_uuid(actual)
                elif kind in ("text", "jsonb", "decimal"):
                    require(type(actual) is str and "\0" not in actual, "row_text")
                elif kind == "bool":
                    require(type(actual) is bool, "row_bool")
                elif kind in ("integer", "utc_us"):
                    width = 64 if kind == "utc_us" or name in ("peak_memory_bytes", "process_rss_bytes") else 32
                    integer(actual, -(2 ** (width - 1)), 2 ** (width - 1) - 1)
                elif kind == "float64_bits":
                    require(type(actual) is str and re.fullmatch(r"[0-9a-f]{16}", actual) is not None, "row_float_bits")
                    require(math.isfinite(struct.unpack("!d", bytes.fromhex(actual))[0]), "row_nonfinite")
                else:
                    raise Failure("row_unselected_kind")
            identities.append(row["id"]["value"])
        require(identities == sorted(identities) and len(set(identities)) == len(identities), "row_order")
    encode(value, ROW_LIMIT)
    return value


def query_witness(value):
    closed(value, ("xid", "backend_pid", "database_name", "server_address", "server_port", "server_version_num"))
    require(type(value["xid"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value["xid"]) is not None, "witness_xid")
    integer(int(value["xid"]), 1, 2**64 - 1)
    integer(value["backend_pid"], 1, 2**31 - 1)
    require(
        type(value["database_name"]) is str and 0 < len(value["database_name"].encode("utf-8")) <= 63,
        "witness_database",
    )
    require(
        type(value["server_address"]) is str
        and str(ipaddress.IPv4Address(value["server_address"])) == value["server_address"],
        "witness_address",
    )
    integer(value["server_port"], 1, 65535)
    require(
        type(value["server_version_num"]) is str
        and re.fullmatch(r"[1-9][0-9]{0,9}", value["server_version_num"]) is not None,
        "witness_version",
    )
    integer(int(value["server_version_num"]), 160000, 169999)
    return value


def binding(value, number, actor):
    integer(value["cycle"], number, number)
    require(value["actor"] == actor, "cycle_actor")
    if "connection" in value:
        integer(value["connection"], number, number)


def constructor(value, role, hostname, port):
    closed(
        value,
        (
            "schema",
            "role",
            "env_count",
            "env_source_equal",
            "source_calls",
            "endpoint",
            "engine",
            "factory",
            "connection",
            "restoration",
        ),
    )
    require(
        value["schema"] == "bifrost.test.f4-constructor-observation/v1"
        and value["role"] == role
        and role in ("observer", "python"),
        "constructor_identity",
    )
    integer(value["env_count"], 28, 28)
    require(value["env_source_equal"] is True, "constructor_environment")
    calls = closed(value["source_calls"], ("settings", "prepare", "engine", "factory", "guard"))
    for name, count in calls.items():
        integer(
            count,
            1 if role == "python" or name in ("engine", "factory") else 0,
            1 if role == "python" or name in ("engine", "factory") else 0,
        )
    endpoint = closed(value["endpoint"], ("hostname", "port", "drivername"))
    require(
        endpoint == {"hostname": hostname, "port": port, "drivername": "postgresql+asyncpg"}
        and type(endpoint["port"]) is int,
        "constructor_endpoint",
    )
    engine = closed(value["engine"], ("origin_matches", "input_matches", "kwargs_matches", "returned_matches", "pool"))
    require(
        all(engine[name] is True for name in engine if name != "pool")
        and engine["pool"] == ("null_pool" if role == "observer" else "production_pool"),
        "constructor_engine",
    )
    factory = closed(
        value["factory"], ("origin_matches", "engine_matches", "returned_matches", "expire_on_commit", "autoflush")
    )
    require(
        all(factory[name] is True for name in ("origin_matches", "engine_matches", "returned_matches"))
        and factory["expire_on_commit"] is False
        and factory["autoflush"] is (role == "observer"),
        "constructor_factory",
    )
    physical = closed(
        value["connection"],
        (
            "record_join",
            "selected_connection_join",
            "frontend_join",
            "driver_args_observed",
            "negotiated_transport_observed",
        ),
    )
    for name, observed in physical.items():
        require(type(observed) is bool, "constructor_connection_type")
        expected = role == "python" and name in ("record_join", "frontend_join", "driver_args_observed")
        require(observed is expected, "constructor_connection_stage")
    require(
        closed(value["restoration"], ("pending", "failed")) == {"pending": True, "failed": False}
        and value["restoration"]["pending"] is True
        and value["restoration"]["failed"] is False,
        "constructor_restoration",
    )
    return value


def connection_ready(value, role):
    closed(
        value,
        (
            "schema",
            "role",
            "engine_join",
            "preconnect_match",
            "record_join",
            "checkout_join",
            "selected_connection_join",
            "frontend_join",
            "negotiated_tls",
        ),
    )
    require(
        value["schema"] == "bifrost.test.f4-connection-observation/v1" and value["role"] == role, "connection_identity"
    )
    require(
        all(value[name] is True for name in ("engine_join", "preconnect_match", "record_join", "checkout_join"))
        and value["selected_connection_join"] is False
        and value["frontend_join"] is False
        and value["negotiated_tls"] is None,
        "connection_stage",
    )
    return value


def sdk_witness(value):
    closed(
        value,
        (
            "schema",
            "sdk_function_source",
            "sdk_call_count",
            "sdk_arguments_match",
            "sdk_result_zero",
            "sdk_call_error",
            "session_connection_join",
            "shared_export_source",
        ),
    )
    require(value["schema"] == "bifrost.test.f4-sdk-source/v1", "sdk_schema")
    integer(value["sdk_call_count"], 1, 1)
    for name in (
        "sdk_function_source",
        "sdk_arguments_match",
        "sdk_result_zero",
        "sdk_call_error",
        "session_connection_join",
        "shared_export_source",
    ):
        require(value[name] is (name != "sdk_call_error"), "sdk_source_observation")
    return value


LANE_FIELDS = (
    "frontend_matches",
    "transaction_witness_matches",
    "selected_writes_observed",
    "listener_returned",
    "armed",
    "upstream_forwarded",
    "downstream_dropped",
    "client_outcome",
    "server_status",
    "row_relation",
    "exposure",
    "cleanup_complete",
)
LANE_BOOLS = (
    "frontend_matches",
    "transaction_witness_matches",
    "selected_writes_observed",
    "listener_returned",
    "armed",
    "exposure",
    "cleanup_complete",
)
SAFE_LIMITS = {"fault.receipt.json": 16384, "producer.json": 65536, "disposal.json": 16384}


def nullable_bool(value):
    require(value is None or type(value) is bool, "safe_boolean")


def nullable_integer(value, high):
    if value is not None:
        integer(value, 0, high)


def digest(value, width):
    require(
        value is None or (type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(width) + "}", value) is not None),
        "safe_software_digest",
    )


def outcome(value):
    if value is None:
        return
    closed(value, ("commit", "error"))
    require(value["commit"] in ("not_dispatched", "acknowledged", "error"), "safe_commit")
    error = value["error"]
    if error is not None:
        closed(error, ("family", "code"))
        require(
            (error["family"], error["code"])
            in (
                ("python_connection_lost", "08003"),
                ("rust_io", "unexpected_eof"),
                ("rust_io", "connection_reset"),
                ("rust_io", "broken_pipe"),
                ("other", None),
            ),
            "safe_error_pair",
        )


def lane_exposure(value, actor):
    """Field coherence only; the real caller must also retain private causal joins."""
    result = value["client_outcome"]
    if result is None or result["commit"] != "error" or result["error"] is None:
        return False
    error = result["error"]
    error_matches = (
        error == {"family": "python_connection_lost", "code": "08003"}
        if actor == "python"
        else error["family"] == "rust_io" and error["code"] in ("unexpected_eof", "connection_reset", "broken_pipe")
    )
    return (
        error_matches
        and all(
            value[name] is True
            for name in (
                "frontend_matches",
                "transaction_witness_matches",
                "selected_writes_observed",
                "listener_returned",
                "armed",
            )
        )
        and type(value["upstream_forwarded"]) is int
        and value["upstream_forwarded"] > 0
        and type(value["downstream_dropped"]) is int
        and value["downstream_dropped"] > 0
        and value["server_status"] == "committed"
        and value["row_relation"] == "all_after"
    )


def validate_fault(value, qualified):
    closed(value, ("schema", "case_id", "source_sha256", "lanes", "passed"))
    require(
        value["schema"] == "bifrost.test.workflow-commit-fault/v1" and value["case_id"] == "r-tx-lost-commit-response",
        "safe_fault_identity",
    )
    source = closed(value["source_sha256"], ("source_manifest", "rust_binary"))
    for observed in source.values():
        digest(observed, 64)
    lanes = closed(value["lanes"], ("python", "rust"))
    for actor, lane in lanes.items():
        closed(lane, LANE_FIELDS)
        for name in LANE_BOOLS:
            nullable_bool(lane[name])
        for name in ("upstream_forwarded", "downstream_dropped"):
            nullable_integer(lane[name], 2**64 - 1)
        outcome(lane["client_outcome"])
        require(
            lane["server_status"] in (None, "committed", "aborted", "in_progress", "unavailable"), "safe_server_status"
        )
        require(lane["row_relation"] in (None, "all_before", "all_after", "mixed", "unavailable"), "safe_row_relation")
        require(lane["exposure"] is not True or lane_exposure(lane, actor), "safe_exposure_coherence")
    require(type(value["passed"]) is bool and type(qualified) is bool, "safe_pass_type")
    require(
        not value["passed"]
        or (
            qualified
            and all(source.values())
            and all(lane["exposure"] is True and lane["cleanup_complete"] is True for lane in lanes.values())
        ),
        "safe_premature_pass",
    )
    return value


def validate_producer(value, qualified):
    closed(value, ("schema", "source", "stage", "commands", "primary_exit", "complete"))
    require(value["schema"] == "bifrost.test.workflow-commit-producer/v1", "safe_producer_schema")
    source = closed(value["source"], ("head", "tree", "main", "workspace"))
    for observed in source.values():
        digest(observed, 40)
    require(value["stage"] is None or value["stage"] in ROSTER, "safe_stage")
    records = value["commands"]
    require(type(records) is list and len(records) <= ROSTER_LIMIT, "safe_commands_count")
    labels = set()
    for record in records:
        closed(record, ("label", "settled", "native_exit", "stdout_eof", "stderr_eof", "group_settled", "fd_settled"))
        require(
            type(record["label"]) is str and record["label"] in ROSTER and record["label"] not in labels,
            "safe_command_label",
        )
        labels.add(record["label"])
        for name in ("settled", "stdout_eof", "stderr_eof", "group_settled", "fd_settled"):
            nullable_bool(record[name])
        nullable_integer(record["native_exit"], 255)
        encode(record, 256)
    nullable_integer(value["primary_exit"], 255)
    require(type(value["complete"]) is bool and type(qualified) is bool, "safe_complete_type")
    require(
        not value["complete"]
        or (
            qualified
            and value["primary_exit"] == 0
            and all(source.values())
            and bool(records)
            and all(
                record["native_exit"] is not None
                and all(
                    record[name] is True
                    for name in ("settled", "stdout_eof", "stderr_eof", "group_settled", "fd_settled")
                )
                for record in records
            )
        ),
        "safe_premature_completion",
    )
    return value


def validate_disposal(value, qualified):
    closed(
        value,
        (
            "schema",
            "primary_exit",
            "cleanup_exit",
            "complete",
            "processes",
            "resources",
            "private_captures",
            "credentials_disposed",
        ),
    )
    require(value["schema"] == "bifrost.test.workflow-commit-disposal/v1", "safe_disposal_schema")
    for name in ("primary_exit", "cleanup_exit"):
        nullable_integer(value[name], 255)
    processes = closed(value["processes"], ("started", "settled"))
    integer(processes["started"], 0, ROSTER_LIMIT)
    integer(processes["settled"], 0, processes["started"])
    resources = closed(value["resources"], ("containers", "networks", "volumes", "images", "cargo", "files"))
    for resource in resources.values():
        closed(resource, ("admitted", "removed", "remaining", "unknown"))
        for observed in resource.values():
            nullable_integer(observed, 2**64 - 1)
    captures = closed(value["private_captures"], ("closed", "removed"))
    for observed in captures.values():
        nullable_bool(observed)
    nullable_bool(value["credentials_disposed"])
    require(type(value["complete"]) is bool and type(qualified) is bool, "safe_disposal_complete_type")
    require(
        not value["complete"]
        or (
            qualified
            and value["cleanup_exit"] == 0
            and value["primary_exit"] is not None
            and processes["started"] == processes["settled"]
            and all(
                all(observed is not None for observed in resource.values())
                and resource["admitted"] == resource["removed"]
                and resource["remaining"] == 0
                and resource["unknown"] == 0
                for resource in resources.values()
            )
            and all(observed is True for observed in captures.values())
            and value["credentials_disposed"] is True
        ),
        "safe_disposal_premature_complete",
    )
    return value


COMPOSE_ENVIRONMENT = (
    "BIFROST_DATABASE_URL",
    "BIFROST_DATABASE_URL_SYNC",
    "BIFROST_RABBITMQ_URL",
    "BIFROST_WORK_DELIVERY_BACKEND",
    "BIFROST_REDIS_URL",
    "BIFROST_SECRET_KEY",
    "BIFROST_ENVIRONMENT",
    "BIFROST_ALLOW_REGISTRATION",
    "BIFROST_S3_BUCKET",
    "BIFROST_S3_ENDPOINT_URL",
    "BIFROST_S3_ACCESS_KEY",
    "BIFROST_S3_SECRET_KEY",
    "BIFROST_S3_REGION",
    "TEST_API_URL",
    "TEST_API_REPLICA_URL",
    "PYTHONPATH",
    "BIFROST_RUNTIME_VECTORS",
    "COVERAGE_FILE",
    "GITHUB_TEST_PAT",
    "GITHUB_TEST_REPO",
    "ANTHROPIC_API_TEST_KEY",
    "OPENAPI_API_TEST_KEY",
    "GENERIC_AI_TEST_KEY",
    "GENERIC_AI_BASE_URL",
    "EMBEDDINGS_AI_TEST_KEY",
    "BIFROST_POSTURE_HARDENED",
)
VENDOR_ENVIRONMENT = (
    "GITHUB_TEST_PAT",
    "ANTHROPIC_API_TEST_KEY",
    "OPENAPI_API_TEST_KEY",
    "GENERIC_AI_TEST_KEY",
    "GENERIC_AI_BASE_URL",
    "EMBEDDINGS_AI_TEST_KEY",
)


def environment_map(raw):
    require(type(raw) is list and len(raw) <= 256, "environment_array")
    result = {}
    for entry in raw:
        require(type(entry) is str and "=" in entry and "\0" not in entry, "environment_entry")
        key, value = entry.split("=", 1)
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is not None and key not in result, "environment_duplicate")
        result[key] = value
    return result


def image_environment(raw):
    observed = environment_map(raw)
    require(
        observed.get("BIFROST_VERSION") == "unknown" and observed.get("PYTHONPATH") == "/app",
        "image_environment_profile",
    )
    require(
        not any(
            (key.startswith("BIFROST_") and key != "BIFROST_VERSION")
            or key.startswith("PG")
            or key in ("SSL_CERT_FILE", "SSL_CERT_DIR")
            for key in observed
        ),
        "image_environment_extra",
    )
    return observed


def selected_environment(model):
    require(type(model) is dict and type(model.get("services")) is dict, "compose_services")
    runner = model["services"].get("test-runner")
    require(type(runner) is dict, "compose_runner")
    selected = closed(runner.get("environment"), COMPOSE_ENVIRONMENT)
    require(
        all(
            type(value) is str and not any(character in value for character in "\0\r\n") for value in selected.values()
        ),
        "environment_encoding",
    )
    require(all(selected[key] == "" for key in VENDOR_ENVIRONMENT), "vendor_environment")
    require(
        selected["BIFROST_ENVIRONMENT"] == "testing"
        and selected["PYTHONPATH"] == "/app"
        and selected["BIFROST_WORK_DELIVERY_BACKEND"] in ("rabbitmq", "postgres"),
        "environment_profile",
    )
    # These are the actual source conftest assignments; mismatch is rejected,
    # rather than rewriting the admitted resolved Compose environment.
    require(
        selected["BIFROST_DATABASE_URL"] == "postgresql+asyncpg://bifrost:bifrost_test@pgbouncer:5432/bifrost_test"
        and selected["BIFROST_DATABASE_URL_SYNC"] == "postgresql://bifrost:bifrost_test@pgbouncer:5432/bifrost_test"
        and selected["BIFROST_RABBITMQ_URL"] == "amqp://bifrost:bifrost_test@rabbitmq:5672/"
        and selected["BIFROST_REDIS_URL"] == "redis://redis:6379/0"
        and selected["BIFROST_SECRET_KEY"] == "test-secret-key-for-e2e-testing-must-be-32-chars",
        "environment_conftest_association",
    )
    supplied = dict(selected)
    supplied["BIFROST_TEMP_LOCATION"] = "/tmp/bifrost/temp"
    require(len(supplied) == 27 and "BIFROST_VERSION" not in supplied, "environment_supplied_membership")
    return supplied


def actor_environment_match(raw, supplied, inherited):
    observed = environment_map(raw)
    expected = dict(inherited)
    expected.update(supplied)
    require(observed == expected, "actor_image_environment_join")
    keys = (*COMPOSE_ENVIRONMENT, "BIFROST_TEMP_LOCATION", "BIFROST_VERSION")
    require(
        len(keys) == 28 and all(key in observed for key in keys) and observed["BIFROST_VERSION"] == "unknown",
        "actor_version_join",
    )
    require(
        not any(
            (key.startswith(("BIFROST_", "PG")) and key not in keys) or key in ("SSL_CERT_FILE", "SSL_CERT_DIR")
            for key in observed
        ),
        "actor_environment_extra",
    )
    return {key: observed[key] for key in keys}


def write_actor_environment(controller, supplied, directory):
    # Docker consumes this private host file. Version is inherited from the
    # separately admitted image, and is deliberately not an env-file override.
    require(
        type(supplied) is dict
        and len(supplied) == 27
        and set(supplied) == {*COMPOSE_ENVIRONMENT, "BIFROST_TEMP_LOCATION"},
        "environment_file_membership",
    )
    raw = b"".join((key + "=" + supplied[key] + "\n").encode("utf-8") for key in sorted(supplied))
    require(len(raw) <= 65536, "environment_file_bound")
    python_path = controller.files.file(directory / "python.env", raw, retain=True)
    original = supplied["BIFROST_DATABASE_URL"]
    require(original.startswith("postgresql+asyncpg://") and "?" not in original, "native_original_url_profile")
    native_url = "postgresql://" + original[len("postgresql+asyncpg://") :]
    native_raw = ("BIFROST_RUST_TEST_DATABASE_URL=" + native_url + "\n").encode("utf-8")
    rust_path = controller.files.file(directory / "rust.env", native_raw, retain=True)
    controller.values["python_env_file"] = str(python_path)
    controller.values["rust_env_file"] = str(rust_path)
    return python_path, rust_path


def native_text(controller, native, bound, *, ascii_only=False):
    raw = controller.capture(native)
    require(len(raw) <= bound and b"\0" not in raw, "native_text_bound")
    return raw.decode("ascii" if ascii_only else "utf-8", "strict")


def git_identity(controller, label, end, environment):
    native = controller.run(label, end, environment)
    raw = native_text(controller, native, 41, ascii_only=True)
    require(re.fullmatch(r"[0-9a-f]{40}\n", raw) is not None, "git_identity")
    return raw[:-1]


def tree_roster(raw):
    require(type(raw) is bytes and len(raw) <= 16 * 1024 * 1024 and raw.endswith(b"\0"), "git_tree_framing")
    result = {}
    for record in raw[:-1].split(b"\0"):
        require(b"\t" in record, "git_tree_record")
        header, path_bytes = record.split(b"\t", 1)
        match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})", header)
        require(match is not None, "git_tree_kind")
        path = path_bytes.decode("utf-8", "strict")
        require(
            path
            and not path.startswith("/")
            and "\\" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and not any(ord(character) < 32 or ord(character) == 127 for character in path)
            and path not in result,
            "git_tree_path",
        )
        result[path] = {"mode": match[1].decode("ascii"), "oid": match[2].decode("ascii")}
    require(result, "git_tree_empty")
    return result


def git_batch_request(roster, paths):
    require(
        type(paths) is tuple
        and paths
        and len(paths) <= 4096
        and tuple(sorted(set(paths))) == paths
        and all(path in roster for path in paths),
        "git_batch_paths",
    )
    # Object IDs may repeat across files. Each requested record is retained in
    # path order; no object deduplication may drop a required software member.
    return b"".join((roster[path]["oid"] + "\n").encode("ascii") for path in paths)


def git_batch_response(raw, roster, paths):
    require(type(raw) is bytes and len(raw) <= 16 * 1024 * 1024, "git_batch_bound")
    offset = 0
    result = {}
    for path in paths:
        newline = raw.find(b"\n", offset)
        require(newline >= offset and newline - offset <= 80, "git_batch_header")
        header = raw[offset:newline]
        match = re.fullmatch(rb"([0-9a-f]{40}) blob (0|[1-9][0-9]*)", header)
        require(match is not None and match[1].decode("ascii") == roster[path]["oid"], "git_batch_object")
        length = int(match[2])
        require(length <= 4 * 1024 * 1024, "git_source_size")
        offset = newline + 1
        require(offset + length < len(raw) and raw[offset + length] == 10, "git_batch_body")
        data = raw[offset : offset + length]
        oid = hashlib.sha1(b"blob " + str(length).encode("ascii") + b"\0" + data).hexdigest()
        require(oid == roster[path]["oid"], "git_batch_blob_digest")
        result[path] = {
            "mode": roster[path]["mode"],
            "oid": oid,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": data,
        }
        offset += length + 1
    require(offset == len(raw) and len(result) == len(paths), "git_batch_extra")
    return result


def source_disk_match(root, sources):
    for path, record in sources.items():
        physical = root / path
        # Every ancestor is checked in the actual candidate namespace. A
        # symlink to a matching mirror is not admitted as the selected source.
        current = root
        for part in path.split("/")[:-1]:
            current /= part
            observed = os.lstat(current)
            require(stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(observed.st_mode), "source_directory")
        observed = os.lstat(physical)
        require(
            stat.S_ISREG(observed.st_mode)
            and observed.st_nlink == 1
            and bool(observed.st_mode & 0o111) == (record["mode"] == "100755"),
            "source_file_mode",
        )
        data = read_software(physical, 4 * 1024 * 1024, allow_empty=True)
        require(data == record["bytes"] and hashlib.sha256(data).hexdigest() == record["sha256"], "source_disk_bytes")


def source_heads(sources):
    graph = {}
    for path in sorted(sources):
        if not path.startswith("api/alembic/versions/") or not path.endswith(".py"):
            continue
        raw = sources[path]["bytes"]
        require(not raw.startswith(b"\xef\xbb\xbf"), "migration_bom")
        parsed = ast.parse(raw.decode("utf-8", "strict"), filename=path)
        values = {}
        targets = set()
        for node in parsed.body:
            name = value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                require(node.simple == 1, "migration_annotation")
                name, value = node.target.id, node.value
            if name in ("revision", "down_revision", "depends_on", "branch_labels"):
                require(name not in values and value is not None, "migration_duplicate_literal")
                if isinstance(value, ast.Tuple):
                    require(
                        name == "down_revision"
                        and len(value.elts) == 2
                        and all(isinstance(item, ast.Constant) and type(item.value) is str for item in value.elts),
                        "migration_tuple",
                    )
                    values[name] = tuple(item.value for item in value.elts)
                else:
                    require(isinstance(value, ast.Constant), "migration_dynamic")
                    values[name] = value.value
                target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
                targets.add(id(target))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                require(
                    node.id not in ("revision", "down_revision", "depends_on", "branch_labels") or id(node) in targets,
                    "migration_metadata_store",
                )
            if isinstance(node, ast.alias):
                require(
                    (node.asname or node.name.split(".")[0])
                    not in ("revision", "down_revision", "depends_on", "branch_labels"),
                    "migration_metadata_import",
                )
        require(
            set(values) == {"revision", "down_revision", "depends_on", "branch_labels"}
            and values["depends_on"] is None
            and values["branch_labels"] is None,
            "migration_required",
        )
        revision = values["revision"]
        require(
            type(revision) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", revision) and revision not in graph,
            "migration_revision",
        )

        def references(value):
            if value is None:
                return ()
            if type(value) is str:
                value = (value,)
            require(
                type(value) is tuple
                and len(value) in (1, 2)
                and len(set(value)) == len(value)
                and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", item) for item in value),
                "migration_references",
            )
            return value

        graph[revision] = references(values["down_revision"])
    require(
        graph and all(parent in graph for revision in graph for parent in graph[revision]), "migration_missing_parent"
    )
    visited = set()
    active = set()

    def visit(revision):
        require(revision not in active, "migration_cycle")
        if revision in visited:
            return
        active.add(revision)
        for parent in graph[revision]:
            visit(parent)
        active.remove(revision)
        visited.add(revision)

    for revision in graph:
        visit(revision)
    heads = sorted(set(graph) - {parent for parents in graph.values() for parent in parents})
    require(0 < len(heads) <= 8, "migration_heads_empty")
    return heads


def container_object(value, cid):
    require(type(value) is list and len(value) == 1 and type(value[0]) is dict, "container_inspect_shape")
    observed = value[0]
    require(
        type(cid) is str
        and re.fullmatch(r"[0-9a-f]{64}", cid)
        and observed.get("Id") == cid
        and type(observed.get("Config")) is dict
        and type(observed.get("HostConfig")) is dict
        and type(observed.get("State")) is dict
        and type(observed.get("NetworkSettings")) is dict
        and type(observed.get("Mounts")) is list,
        "container_inspect_members",
    )
    require(
        type(observed.get("Image")) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", observed["Image"]),
        "container_image_id",
    )
    return observed


def native_container_config(controller, observed, purpose, image_id, network, user, *, command=None, entrypoint=None):
    config = observed["Config"]
    host = observed["HostConfig"]
    labels = config.get("Labels")
    require(
        type(labels) is dict
        and labels.get("bifrost.f4.invocation") == controller.invocation
        and labels.get("bifrost.f4.candidate") == controller.source["head"]
        and (labels.get("bifrost.f4.purpose") == purpose or labels.get("bifrost.f4.actor") == purpose),
        "container_source_labels",
    )
    require(
        observed["Image"] == image_id
        and config.get("User") == user
        and host.get("NetworkMode") == network
        and host.get("Privileged") is False
        and host.get("PidMode") == ""
        and host.get("UsernsMode") == ""
        and host.get("AutoRemove") is False,
        "container_native_profile",
    )
    if command is not None:
        require(config.get("Cmd") == command, "container_command")
    if entrypoint is not None:
        require(config.get("Entrypoint") == entrypoint, "container_entrypoint")
    return observed


def created_container(observed):
    state = observed["State"]
    require(
        state.get("Status") == "created"
        and state.get("Running") is False
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and state.get("OOMKilled") is False
        and state.get("Pid") == 0
        and type(state.get("Pid")) is int
        and state.get("Error") == "",
        "container_created_state",
    )
    return observed


def live_container(observed):
    state = observed["State"]
    require(
        state.get("Status") == "running"
        and state.get("Running") is True
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and state.get("OOMKilled") is False
        and state.get("Error") == "",
        "container_live_state",
    )
    integer(state.get("Pid"), 1, 2**31 - 1)
    return observed


def terminal_container(observed, expected):
    state = observed["State"]
    require(
        type(expected) is int
        and 0 <= expected <= 255
        and state.get("Status") == "exited"
        and state.get("Running") is False
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and state.get("OOMKilled") is False
        and type(state.get("ExitCode")) is int
        and state["ExitCode"] == expected
        and type(state.get("Pid")) is int
        and state["Pid"] == 0
        and state.get("Error") == ""
        and type(state.get("FinishedAt")) is str
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z", state["FinishedAt"])
        and not state["FinishedAt"].startswith("0001-"),
        "container_terminal_state",
    )
    return observed


def container_stable(before, after):
    # Retain complete actual values. No OOM policy or default normalization is
    # borrowed from a different source-check campaign.
    require(
        before["Id"] == after["Id"]
        and before["Image"] == after["Image"]
        and before.get("Created") == after.get("Created")
        and before["Config"] == after["Config"]
        and before["HostConfig"] == after["HostConfig"]
        and before["Mounts"] == after["Mounts"],
        "container_configuration_drift",
    )
    return after


def mount_association(observed, source, destination, readonly):
    require(type(source) is str and type(destination) is str and type(readonly) is bool, "mount_arguments")
    matches = [mount for mount in observed["Mounts"] if type(mount) is dict and mount.get("Destination") == destination]
    require(len(matches) == 1, "mount_multiplicity")
    mount = matches[0]
    require(
        mount.get("Type") == "bind"
        and mount.get("Source") == source
        and type(mount.get("RW")) is bool
        and mount["RW"] is (not readonly),
        "mount_source_association",
    )
    return mount


def source_mounts(controller, observed):
    root = controller.values["candidate_root"]
    for directory in ("src", "tests", "shared", "bifrost"):
        mount_association(observed, root + "/api/" + directory, "/app/" + directory, True)


def inspect_native(controller, label, end, environment, cid, *, cleanup=False):
    native = controller.run(label, end, environment, cleanup=cleanup)
    value = decode(controller.capture(native), 16 * 1024 * 1024)
    return container_object(value, cid)


def created_cid(controller, label, end, environment, *, stdin=b"", cleanup=False):
    attempts = getattr(controller, "container_attempts", None)
    if attempts is None:
        attempts = {}
        controller.container_attempts = attempts
    require(label not in attempts, "container_attempt_repeated")
    attempts[label] = None
    native = controller.run(label, end, environment, stdin=stdin, cleanup=cleanup)
    output = native_text(controller, native, 65, ascii_only=True)
    require(re.fullmatch(r"[0-9a-f]{64}\n", output), "container_create_id")
    attempts[label] = output[:-1]
    return output[:-1]


API_COPY_PATHS = (
    "api/_bifrost_workspace_effects.py",
    "api/bifrost/__init__.py",
    "api/bifrost/__main__.py",
    "api/bifrost/_context.py",
    "api/bifrost/_execution_context.py",
    "api/bifrost/_local_resources.py",
    "api/bifrost/_logging.py",
    "api/bifrost/_service_runtime.py",
    "api/bifrost/_solution_workspace.py",
    "api/bifrost/_sync.py",
    "api/bifrost/_version_notice.py",
    "api/bifrost/_workspace_lock.py",
    "api/bifrost/_write_buffer.py",
    "api/bifrost/admin_models.py",
    "api/bifrost/agents.py",
    "api/bifrost/ai.py",
    "api/bifrost/api.py",
    "api/bifrost/app_binding.py",
    "api/bifrost/app_migration.py",
    "api/bifrost/artifacts.py",
    "api/bifrost/cli.py",
    "api/bifrost/client.py",
    "api/bifrost/commands/__init__.py",
    "api/bifrost/commands/agents.py",
    "api/bifrost/commands/app.py",
    "api/bifrost/commands/apps.py",
    "api/bifrost/commands/base.py",
    "api/bifrost/commands/claims.py",
    "api/bifrost/commands/configs.py",
    "api/bifrost/commands/events.py",
    "api/bifrost/commands/files.py",
    "api/bifrost/commands/forms.py",
    "api/bifrost/commands/integrations.py",
    "api/bifrost/commands/orgs.py",
    "api/bifrost/commands/policy_rules.py",
    "api/bifrost/commands/promote.py",
    "api/bifrost/commands/requirements.py",
    "api/bifrost/commands/roles.py",
    "api/bifrost/commands/services.py",
    "api/bifrost/commands/solution.py",
    "api/bifrost/commands/tables.py",
    "api/bifrost/commands/workflows.py",
    "api/bifrost/config.py",
    "api/bifrost/contract_version.py",
    "api/bifrost/contracts/__init__.py",
    "api/bifrost/contracts/agents.py",
    "api/bifrost/contracts/applications.py",
    "api/bifrost/contracts/claims.py",
    "api/bifrost/contracts/config.py",
    "api/bifrost/contracts/enums.py",
    "api/bifrost/contracts/events.py",
    "api/bifrost/contracts/files.py",
    "api/bifrost/contracts/forms.py",
    "api/bifrost/contracts/integrations.py",
    "api/bifrost/contracts/organizations.py",
    "api/bifrost/contracts/policy_rules.py",
    "api/bifrost/contracts/services.py",
    "api/bifrost/contracts/solutions.py",
    "api/bifrost/contracts/tables.py",
    "api/bifrost/contracts/users.py",
    "api/bifrost/contracts/workflows.py",
    "api/bifrost/credentials.py",
    "api/bifrost/decorators.py",
    "api/bifrost/dto_flags.py",
    "api/bifrost/events.py",
    "api/bifrost/executions.py",
    "api/bifrost/field_classes.py",
    "api/bifrost/files.py",
    "api/bifrost/forms.py",
    "api/bifrost/git_commands.py",
    "api/bifrost/ignore_patterns.py",
    "api/bifrost/integrations.py",
    "api/bifrost/knowledge.py",
    "api/bifrost/lucide_icon_names.json",
    "api/bifrost/manifest.py",
    "api/bifrost/manifest_codec.py",
    "api/bifrost/migrate_imports.py",
    "api/bifrost/migrate_v2.py",
    "api/bifrost/models.py",
    "api/bifrost/oauth_admin.py",
    "api/bifrost/org_target.py",
    "api/bifrost/organizations.py",
    "api/bifrost/platform_jobs.py",
    "api/bifrost/platform_names.py",
    "api/bifrost/promotion.py",
    "api/bifrost/pyproject.toml",
    "api/bifrost/refs.py",
    "api/bifrost/resources.py",
    "api/bifrost/roles.py",
    "api/bifrost/root_file_bindings.py",
    "api/bifrost/skill.py",
    "api/bifrost/solution_binding.py",
    "api/bifrost/solution_delivery_review.py",
    "api/bifrost/solution_descriptor.py",
    "api/bifrost/solution_dev/__init__.py",
    "api/bifrost/solution_dev/app_select.py",
    "api/bifrost/solution_dev/function_host.py",
    "api/bifrost/solution_dev/proxy.py",
    "api/bifrost/solution_dev/reload.py",
    "api/bifrost/solution_dev/scaffold_check.py",
    "api/bifrost/solution_jobs.py",
    "api/bifrost/solution_source_closure.py",
    "api/bifrost/solution_vendoring.py",
    "api/bifrost/tables.py",
    "api/bifrost/tui/__init__.py",
    "api/bifrost/tui/connection_select.py",
    "api/bifrost/tui/file_select.py",
    "api/bifrost/tui/progress.py",
    "api/bifrost/tui/sync_app.py",
    "api/bifrost/tui/theme.py",
    "api/bifrost/tui/watch.py",
    "api/bifrost/users.py",
    "api/bifrost/webhooks.py",
    "api/bifrost/workflow_parameters.py",
    "api/bifrost/workflows.py",
    "api/bifrost/workspace_effects.py",
    "api/bifrost/workspace_impact.py",
    "api/bifrost/workspace_release.py",
    "api/bifrost/workspace_release_authorization.py",
    "api/entrypoint.sh",
    "api/scripts/build_cli_artifact.py",
    "api/shared/cli_artifact.py",
    "api/src/services/app_bundler/package-lock.json",
    "api/src/services/app_bundler/package.json",
    "api/src/services/app_compiler/compile.js",
    "api/src/services/app_compiler/package-lock.json",
    "api/src/services/app_compiler/package.json",
    "api/src/services/app_compiler/tailwind.js",
    "api/src/services/sdk_package/build_sdk.js",
    "api/src/services/sdk_package/package-lock.json",
    "api/src/services/sdk_package/package.json",
    "assets/icon.png",
    "assets/logo.png",
    "client/src/lib/app-sdk/bifrost-header.tsx",
    "client/src/lib/app-sdk/execution-stream.ts",
    "client/src/lib/app-sdk/files.ts",
    "client/src/lib/app-sdk/index.v2.ts",
    "client/src/lib/app-sdk/provider.tsx",
    "client/src/lib/app-sdk/sdk-contract.json",
    "client/src/lib/app-sdk/tables.ts",
    "client/src/lib/app-sdk/transport.ts",
    "client/src/lib/app-sdk/use-files.ts",
    "client/src/lib/app-sdk/use-infinite-table.ts",
    "client/src/lib/app-sdk/use-table.ts",
    "client/src/lib/app-sdk/use-workflow-hooks.ts",
    "client/src/lib/app-sdk/use-workflow.ts",
    "client/src/lib/app-sdk/webmcp.ts",
    "client/src/lib/app-sdk/ws-client.ts",
    "pyproject.toml",
    "requirements-pyright.lock",
    "requirements.lock",
)

WORKSPACE_REFERENCE = {
    "agents/387064f6-af8d-4b88-97b4-adbf0207a2fa.agent.yaml": {
        "oid": "e0c998da3dc84868bdfc5d03ff52d368db3bcb9d",
        "sha256": "b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914",
        "bytes": 1718,
    },
    "features/cove/workflows/recovery_steward.py": {
        "oid": "a5c9f3f5bd6184647e5f50468fe5dabe609ae7ca",
        "sha256": "71bc320261ab6f6da2abb3b4f1bc3335fc9925ae28ac9e922a1f260e378529eb",
        "bytes": 17642,
    },
    "features/cove/workflows/recovery_testing.py": {
        "oid": "dc35d4f513d801e1245598db83f3939635bdfb3d",
        "sha256": "cf0876ecf4fd793f709e6070771e5c0f84dce34128581cf68049a1cce844879a",
        "bytes": 32957,
    },
    "modules/cove.py": {
        "oid": "970bbb0f5d76f3c1c39be95a6bf8e7f7c594bb4f",
        "sha256": "778ec86607025eabf60efb768cf7f05b641a13532ea1686c19709211f5f52133",
        "bytes": 60566,
    },
    "features/cove/__init__.py": {
        "oid": "40fef58c9df9cdc98b51ab3f7e909701525c8eb6",
        "sha256": "0498ef9c35aad1478ab89f334894b7bd3bb418b6901356530ad3e9384fb595a0",
        "bytes": 23,
    },
    "features/utilities/workflows/check_integration_readiness.py": {
        "oid": "6dbd6bcc18716f8bc807e8fc2411c180f44f425c",
        "sha256": "f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de",
        "bytes": 5043,
    },
    "solutions/server-capacity-restore-readiness/functions/readiness.py": {
        "oid": "5889873b3bcdbc51709f3f994c67239534dedc75",
        "sha256": "1382d46429af953e937ee1ff8573d5e9e92c8b8b93640890aa6afccb76d80ad4",
        "bytes": 13788,
    },
    "solutions/server-capacity-restore-readiness/server_readiness/model.py": {
        "oid": "59c0cff1a91e7f1c12936510f36bab4beb523fc6",
        "sha256": "4729828368f9441080b5c0f446eb381efbe0d0584c3fe58a153d22cd14c732cd",
        "bytes": 11363,
    },
    "solutions/server-capacity-restore-readiness/server_readiness/__init__.py": {
        "oid": "19579518caf153a5de101ad1794c2a63d7843db1",
        "sha256": "6ee77548a63c712c93bfd5f91bd6982761b44068fa72d1b368755716226a925a",
        "bytes": 60,
    },
    "workflows/infra/noop_health_check.py": {
        "oid": "1d6d256bed18257b0086256b8a05b2ce3692ca37",
        "sha256": "158bf4e566d3eb04147d7629e71900f2c87ee5532442fbfc890d382dc3e38889",
        "bytes": 527,
    },
}
APPROVED_WORKSPACE_HEAD = "848134becf9e8fdb930efc86872daa62fdeb93cc"


RECIPE_HASHES = {
    "core-rs/Dockerfile": "a42c446bb8c67462ad6afefdc1aa6bdde4a7d241ad4d1122c26af9e5cfe9270b",
    "core-rs/rust-toolchain.toml": "c910997cb152c6dc8ed13b1fdf9fa28a4ed30d6776123e27dd6170e5c66067a0",
    "api/Dockerfile.dev": "54c271f4dd2f95c2b1644c7a7cce11aa5f21ca47126b8afe8ee2dff1c27bca2c",
    ".dockerignore": "992cfb524cc7b8e762fd0e57f3317cdf0980cd99cde1c5d2cdfeaa5cf530cf5d",
    "docker-compose.test.yml": "ea09ba47a32e6de364d66df34991984be6138bc85e7a68f8b79c94c72dd06052",
    "api/entrypoint.sh": "d638f919a0b11e43cdac1c0c82678258f08811a659557bf78e90955b490167f7",
    "test.sh": "19d75479975fcdfed46ce840081336d74db366f8ad008ca6aed9f1ce0f927cfb",
}
FIRST_PARTY_PATHS = (
    "api/tests/conftest.py",
    "api/tests/parity/workflow_commit_fault.py",
    "api/tests/parity/workflow_commit_fault_child.py",
    "api/tests/diagnostics/workflow_commit_fault.py",
    "api/tests/parity/workflow_sql_harness.py",
    "api/tests/parity/fixtures/workflow-result-v1.json",
    "api/src/config.py",
    "api/src/core/database.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/shared/workspace_effects.py",
    "api/alembic.ini",
    "api/alembic/env.py",
    "scripts/lib/test_helpers.sh",
    "scripts/ci/workflow-commit-fault.sh",
    ".github/workflows/workflow-commit-fault.yml",
)


def controlled_environment():
    result = dict(os.environ)
    require(
        not any(key.startswith("PG") or key in ("SSL_CERT_FILE", "SSL_CERT_DIR") for key in result),
        "parent_connection_environment",
    )
    require(
        not any(
            key in result
            for key in (
                "BIFROST_PROJECT_PREFIX",
                "BIFROST_SKIP_BUILD",
                "BIFROST_TEST_USE_CLEAN_BOOT",
                "COMPOSE_FILE",
                "COMPOSE_PROFILES",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ),
        "parent_profile_override",
    )
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "BIFROST_ACTION_PIN_TOKEN_FILE"):
        result.pop(key, None)
    result["GIT_TERMINAL_PROMPT"] = "0"
    home = result.get("HOME")
    require(
        type(home) is str and home.startswith("/") and not os.path.lexists(Path(home) / ".netrc"), "anonymous_netrc"
    )
    return result


def source_copy_membership(root, roster):
    required = set(API_COPY_PATHS)
    for prefix in ("api/bifrost/", "assets/"):
        require(
            {path for path in roster if path.startswith(prefix)}
            == {path for path in required if path.startswith(prefix)},
            "copy_committed_members",
        )
        base = root / prefix.rstrip("/")
        actual = set()
        for directory, directories, files in os.walk(base, followlinks=False):
            observed = os.lstat(directory)
            require(stat.S_ISDIR(observed.st_mode) and not stat.S_ISLNK(observed.st_mode), "copy_directory")
            for name in directories:
                require(not stat.S_ISLNK(os.lstat(Path(directory) / name).st_mode), "copy_directory_symlink")
            for name in files:
                physical = Path(directory) / name
                observed = os.lstat(physical)
                require(stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1, "copy_member_kind")
                actual.add(physical.relative_to(root).as_posix())
        require(actual == {path for path in required if path.startswith(prefix)}, "copy_physical_members")
    for directory in ("app_compiler", "app_bundler"):
        base = "api/src/services/" + directory + "/"
        actual = {physical.relative_to(root).as_posix() for physical in (root / base).glob("package-lock.json*")}
        require(actual == {base + "package-lock.json"}, "copy_wildcard_members")
    require(not os.path.lexists(root / "api/Dockerfile.dev.dockerignore"), "copy_recipe_specific_ignore")
    require(required <= set(roster), "copy_required_members")


def source_preflight(controller, environment, end):
    root = Path(controller.values["candidate_root"])
    version = native_text(controller, controller.run("git.version", end, environment), 256, ascii_only=True)
    require(re.fullmatch(r"git version [0-9]+\.[0-9]+[^\r\n]*\n", version), "git_version")
    origin = native_text(controller, controller.run("git.origin", end, environment), 512, ascii_only=True)
    require(
        origin
        in (
            "https://github.com/Midtown-Technology-Group/bifrost.git\n",
            "git@github.com:Midtown-Technology-Group/bifrost.git\n",
        ),
        "git_origin",
    )
    actual_root = native_text(controller, controller.run("git.root", end, environment), 4096)
    require(actual_root == str(root) + "\n" and root.resolve(strict=True) == root, "git_root")
    controller.source["head"] = git_identity(controller, "git.head", end, environment)
    controller.source["tree"] = git_identity(controller, "git.tree", end, environment)
    controller.values["actual_head"] = controller.source["head"]
    require(controller.capture(controller.run("git.status", end, environment)) == b"", "git_clean")
    controller.run("git.fetch", end, environment)
    controller.source["main"] = git_identity(controller, "git.main", end, environment)
    controller.run("git.ancestor", end, environment)
    roster = tree_roster(controller.capture(controller.run("git.tree_roster", end, environment)))
    core = tuple(path for path in roster if path.startswith("core-rs/"))
    migrations = tuple(path for path in roster if path.startswith("api/alembic/versions/") and path.endswith(".py"))
    paths = tuple(sorted(set((*core, *migrations, *API_COPY_PATHS, *FIRST_PARTY_PATHS, *RECIPE_HASHES))))
    batch = controller.run("git.object_read", end, environment, stdin=git_batch_request(roster, paths))
    sources = git_batch_response(controller.capture(batch), roster, paths)
    source_disk_match(root, sources)
    for path, expected in RECIPE_HASHES.items():
        require(sources[path]["sha256"] == expected, "recipe_source_change")
    source_copy_membership(root, roster)
    # The source parser never supplies a requested installed head to D2.
    controller.heads = source_heads(sources)
    lock = tomllib.loads(sources["core-rs/Cargo.lock"]["bytes"].decode("utf-8", "strict"))
    require(type(lock.get("package")) is list and len(lock["package"]) == 232, "cargo_locked_package_membership")
    packages = set()
    for package in lock["package"]:
        require(
            type(package) is dict and type(package.get("name")) is str and type(package.get("version")) is str,
            "cargo_locked_package",
        )
        identity = (package["name"], package["version"], package.get("source"))
        require(identity not in packages, "cargo_locked_package_duplicate")
        packages.add(identity)
    controller.sources = sources
    controller.git_roster = roster
    controller.source_paths = paths
    return sources


def source_final_bracket(controller, environment, end):
    require(
        git_identity(controller, "git.final_head", end, environment) == controller.source["head"]
        and git_identity(controller, "git.final_tree", end, environment) == controller.source["tree"],
        "git_final_identity",
    )
    require(controller.capture(controller.run("git.final_status", end, environment)) == b"", "git_final_clean")
    source_disk_match(Path(controller.values["candidate_root"]), controller.sources)
    source_copy_membership(Path(controller.values["candidate_root"]), controller.git_roster)


SOURCE_CONTROL_FAMILIES = 8


def private_junit(raw):
    require(
        type(raw) is bytes
        and 0 < len(raw) <= 1024 * 1024
        and b"<!DOCTYPE" not in raw.upper()
        and b"<!ENTITY" not in raw.upper(),
        "junit_bound",
    )
    root = ET.fromstring(raw)
    require(root.tag == "testsuites" and len(root) == 1 and root[0].tag == "testsuite", "junit_suite")
    suite = root[0]
    for key, expected in (("tests", "1"), ("failures", "0"), ("errors", "0"), ("skipped", "0")):
        require(suite.get(key) == expected, "junit_count")
    cases = list(suite.iter("testcase"))
    require(
        len(cases) == 1
        and cases[0].get("name") == "test_workflow_commit_fault"
        and cases[0].get("classname") == "tests.diagnostics.workflow_commit_fault",
        "junit_identity",
    )
    case = cases[0]
    require(not any(node.tag in ("failure", "error", "skipped") for node in case.iter()), "junit_failure")
    properties = [node for node in case if node.tag == "properties"]
    require(len(properties) == 1, "junit_properties")
    observed = {}
    for node in properties[0]:
        require(
            node.tag == "property" and set(node.attrib) == {"name", "value"} and node.get("name") not in observed,
            "junit_property_shape",
        )
        observed[node.get("name")] = node.get("value")
    require(
        observed == {"f4_source_controls": str(SOURCE_CONTROL_FAMILIES), "f4_lanes": "2"}, "junit_property_identity"
    )
    elapsed = float(case.get("time", "nan"))
    require(math.isfinite(elapsed) and 0 <= elapsed <= 120, "junit_elapsed")
    # This returns measured collection/completion identities only. It does
    # not certify transaction fate, source closure or parent EMPTY.
    return {
        "item": "tests/diagnostics/workflow_commit_fault.py::test_workflow_commit_fault",
        "source_controls": SOURCE_CONTROL_FAMILIES,
        "lanes": 2,
        "elapsed": elapsed,
    }


def schema_observation(raw, phase, heads):
    value = closed(
        decode(raw, 4096),
        (
            "schema",
            "case_id",
            "phase",
            "server_major",
            "heads",
            "device_floor",
            "catalog",
            "complete",
        ),
    )
    case_id = "schemaBefore" if phase == "before_target" else "schemaAfter"
    require(
        phase in ("before_target", "after_native")
        and value["schema"] == "bifrost.test.workflow-schema-observed/v1"
        and value["case_id"] == case_id
        and value["phase"] == phase
        and type(value["server_major"]) is int
        and value["server_major"] == 16
        and value["complete"] is True
        and value["device_floor"] is True,
        "schema_native_profile",
    )
    observed_heads = value["heads"]
    require(
        type(observed_heads) is list
        and 0 < len(observed_heads) <= 8
        and all(type(head) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", head) for head in observed_heads)
        and observed_heads == sorted(set(observed_heads))
        and observed_heads == heads,
        "schema_source_heads",
    )
    catalog = closed(value["catalog"], ("relations", "columns", "constraints", "indexes", "combined_sha256"))
    for key in ("relations", "columns", "constraints", "indexes"):
        component = closed(catalog[key], ("count", "sha256"))
        integer(component["count"], 1, 6 if key == "relations" else 1024)
        require(key != "relations" or component["count"] == 6, "schema_relations")
        digest(component["sha256"], 64)
        require(component["sha256"] is not None, "schema_component_missing")
    digest(catalog["combined_sha256"], 64)
    require(catalog["combined_sha256"] is not None, "schema_combined_missing")
    return value


def schema_pair(before, after):
    require(
        before["phase"] == "before_target"
        and after["phase"] == "after_native"
        and before["server_major"] == after["server_major"]
        and before["heads"] == after["heads"]
        and before["catalog"] == after["catalog"]
        and before["device_floor"] is True
        and after["device_floor"] is True,
        "schema_catalog_drift",
    )


def container_absent(controller, label, end, environment, cid, *, cleanup=False):
    native = controller.run(label, end, environment, allowed=(1,), cleanup=cleanup)
    output = controller.capture(native)
    error = controller.capture(native, 1)
    require(
        output in (b"", b"[]\n") and error == ("Error: No such container: " + cid + "\n").encode("ascii"),
        "container_absence_unavailable",
    )


class ContainerCustody:
    """Finite original identities; no name or failed build creates ownership."""

    def __init__(self, controller):
        self.controller = controller
        self.records = {}

    def acquire(self, purpose, cid):
        require(purpose not in self.records and re.fullmatch(r"[0-9a-f]{64}", cid), "container_acquisition_duplicate")
        record = {
            "purpose": purpose,
            "cid": cid,
            "created": None,
            "current": None,
            "admitted": False,
            "removed": False,
            "absent": False,
            "native": None,
            "expected_exit": None,
            "unknown": False,
        }
        self.records[purpose] = record
        self.controller.values[purpose + "_cid"] = cid
        return record

    def admit_created(self, record, observed):
        require(record["created"] is None and observed["Id"] == record["cid"], "container_created_duplicate")
        created_container(observed)
        record["created"] = observed
        record["current"] = observed
        record["admitted"] = True

    def terminal(self, record, observed, expected):
        require(record["admitted"] is True and record["created"] is not None, "container_terminal_unowned")
        container_stable(record["created"], observed)
        terminal_container(observed, expected)
        record["current"] = observed
        record["expected_exit"] = expected

    def remove(self, record, end, environment, *, cleanup=False):
        controller = self.controller
        require(
            record["admitted"] is True
            and record["unknown"] is False
            and record["removed"] is False
            and record["current"] is not None
            and record["expected_exit"] is not None,
            "container_removal_unqualified",
        )
        terminal_container(record["current"], record["expected_exit"])
        container_stable(record["created"], record["current"])
        controller.run(record["purpose"] + ".remove", end, environment, cleanup=cleanup)
        record["removed"] = True
        container_absent(controller, record["purpose"] + ".absent", end, environment, record["cid"], cleanup=cleanup)
        record["absent"] = True


# Narrow source-pattern provenance only: donor SHA256 7a9ea430beded6bf379d9e0a5079d564a8db3b800af1da9f63d7413b26b3a73e.
# No stopped workflow/controller is invoked and no historical result is adopted.
HEX64 = re.compile(r"[0-9a-f]{64}")


def private_frames(raw, prefix, tags, end_marker, limit):
    require(len(raw) <= limit and raw.endswith(end_marker), "private-frame-bound")
    index = 0
    result = {}
    for tag in tags:
        stop = raw.find(b"\n", index)
        require(stop >= index, "private-frame-header")
        match = re.fullmatch(re.escape(prefix + b" " + tag.encode()) + rb" ([0-9]+)", raw[index:stop])
        require(match is not None, "private-frame-tag")
        length = int(match[1])
        begin = stop + 1
        finish = begin + length
        require(length <= limit and raw[finish : finish + 1] == b"\n", "private-frame-length")
        result[tag] = raw[begin:finish]
        index = finish + 1
    require(raw[index:] == end_marker, "private-frame-extra")
    return result


def parse_process(raw, docker_pid):
    rows = private_frames(
        raw,
        b"bifrost-proc/v1",
        ("stat", "cmdline", "exe", "cwd", "netns", "exe_bytes", "exe_sha256", "sockets"),
        b"bifrost-proc-end/v1\n",
        256 * 1024,
    )
    require(len(rows["stat"]) <= 4096 and len(rows["cmdline"]) <= 4096, "proc-bounds")
    fields = re.fullmatch(rb"1 \(([^()]*)\) ([A-Za-z]) (.*)\n", rows["stat"])
    require(fields is not None, "proc-stat")
    tail = fields[3].decode("ascii").split()
    require(len(tail) >= 19 and re.fullmatch(r"[0-9]+", tail[18]) is not None, "proc-start")
    start = int(tail[18])
    argv = rows["cmdline"].split(b"\0")
    require(
        argv == [b"/usr/bin/pgbouncer", b"/etc/pgbouncer/pgbouncer.ini", b""] and rows["exe"] == b"/usr/bin/pgbouncer",
        "proc-exec-profile",
    )
    require(rows["cwd"].startswith(b"/") and len(rows["cwd"]) <= 256, "proc-cwd")
    namespace = re.fullmatch(rb"net:\[([0-9]+)\]", rows["netns"])
    require(namespace is not None, "proc-netns")
    inode = int(namespace[1])
    require(re.fullmatch(rb"[0-9]+", rows["exe_bytes"]) is not None, "proc-exe-size")
    size = int(rows["exe_bytes"])
    digest = rows["exe_sha256"].decode("ascii")
    require(
        integer(start, 1, 2**63 - 1)
        and integer(inode, 1, 2**63 - 1)
        and integer(size, 1, 64 * 1024 * 1024)
        and HEX64.fullmatch(digest) is not None,
        "proc-types",
    )
    sockets = []
    for line in rows["sockets"].splitlines():
        match = re.fullmatch(rb"socket:\[([0-9]+)\]", line)
        require(match is not None, "proc-socket")
        sockets.append(int(match[1]))
    require(len(sockets) <= 128 and len(sockets) == len(set(sockets)), "proc-socket-count")
    return {
        "docker_state_pid": docker_pid,
        "container_pid": 1,
        "start_ticks": start,
        "netns_inode": inode,
        "exe_sha256": digest,
        "exe_bytes": size,
    }, sockets


def strict_frontend_config(raw, endpoint, port, username):
    require(0 < len(raw) <= 65536 and b"\0" not in raw, "frontend-config-bound")
    text = raw.decode("utf-8", "strict")
    require(
        "%" not in text
        and not any(ord(char) < 32 and char not in "\t\n\r" for char in text)
        and "\r" not in text.replace("\r\n", ""),
        "frontend-config-controls",
    )
    sections = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        require(not line[0].isspace(), "frontend-config-continuation")
        if line.startswith("["):
            require(line in {"[databases]", "[pgbouncer]"} and line not in sections, "frontend-config-section")
            sections.append(line)
        else:
            key, separator, _value = line.partition("=")
            require(
                separator == "="
                and key.strip(" ").isascii()
                and re.fullmatch(r"[A-Za-z0-9_*]+", key.strip(" ")) is not None
                and key.strip(" ").lower() not in {"include", "%include"},
                "frontend-config-assignment",
            )
    require(set(sections) == {"[databases]", "[pgbouncer]"}, "frontend-config-sections")
    parser = configparser.ConfigParser(
        interpolation=None, strict=True, empty_lines_in_values=False, inline_comment_prefixes=None
    )
    parser.read_string(text)
    require(not parser.defaults(), "frontend-config-defaults")
    settings = parser["pgbouncer"]
    require(
        settings.get("client_tls_sslmode", "disable") == "disable"
        and settings.get("auth_type") == "plain"
        and settings.get("listen_addr") in {"0.0.0.0", endpoint}
        and settings.get("listen_port") == str(port),
        "frontend-config-effective",
    )
    for name in ("admin_users", "stats_users"):
        value = settings.get(name)
        if value is None:
            continue
        names = [item.strip(" ") for item in value.split(",")]
        require(
            names
            and len(names) == len(set(names))
            and all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) is not None for item in names)
            and username not in names,
            "frontend-console-exclusion",
        )
    return parser


def parse_config_readback(raw):
    require(raw.startswith(b"bifrost-config-read/v1\n") and len(raw) <= 131072, "config-readback-bound")
    cursor = len(b"bifrost-config-read/v1\n")
    records = {}
    for tag in ("before", "payload", "after"):
        stop = raw.find(b"\n", cursor)
        require(stop >= cursor, "config-readback-header")
        match = re.fullmatch(tag.encode() + rb" ([0-9]+)", raw[cursor:stop])
        require(match is not None, "config-readback-tag")
        size = int(match[1])
        require(0 < size <= (65536 if tag == "payload" else 1024), "config-readback-size")
        cursor = stop + 1
        records[tag] = raw[cursor : cursor + size]
        cursor += size
        require(raw[cursor : cursor + 1] == b"\n", "config-readback-delimiter")
        cursor += 1
    require(raw[cursor:] == b"end\n" and records["before"] == records["after"], "config-readback-stability")
    fields = records["before"].decode("ascii").split("\t")
    require(
        len(fields) == 7
        and all(re.fullmatch(r"[0-9]+", fields[index]) is not None for index in (0, 1, 3, 4))
        and re.fullmatch(r"[0-9a-f]+", fields[2]) is not None,
        "config-stat-fields",
    )
    mode = int(fields[2], 16)
    require(
        stat.S_ISREG(mode)
        and int(fields[3]) == 1
        and int(fields[4]) == len(records["payload"])
        and all(
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9} [+-][0-9]{4}", fields[index])
            is not None
            for index in (5, 6)
        ),
        "config-stat-profile",
    )
    return records["payload"], tuple(fields)


def parse_listener(raw, sockets, endpoint, port):
    records = private_frames(raw, b"bifrost-tcp/v1", ("tcp", "tcp6"), b"bifrost-tcp-end/v1\n", 131072)
    matches = []
    for family, body in records.items():
        require(len(body) <= 65536, "tcp-table-bound")
        lines = body.decode("ascii").splitlines()
        require(lines and len(lines) <= 129, "tcp-table-count")
        header = lines[0].split()
        require(header[:4] == ["sl", "local_address", "rem_address", "st"] and "inode" in header, "tcp-header")
        for line in lines[1:]:
            fields = line.split()
            require(
                len(fields) >= 10
                and re.fullmatch(r"[0-9]+:", fields[0]) is not None
                and re.fullmatch(r"[0-9A-F]{2}", fields[3]) is not None
                and re.fullmatch(r"[0-9]+", fields[9]) is not None,
                "tcp-row",
            )
            width = 8 if family == "tcp" else 32
            local = re.fullmatch(r"([0-9A-F]{" + str(width) + r"}):([0-9A-F]{4})", fields[1])
            remote = re.fullmatch(r"[0-9A-F]{" + str(width) + r"}:[0-9A-F]{4}", fields[2])
            require(local is not None and remote is not None, "tcp-address")
            if fields[3] != "0A" or int(local[2], 16) != port:
                continue
            require(family == "tcp", "tcp-relevant-ipv6")
            address = ".".join(str(value) for value in bytes.fromhex(local[1])[::-1])
            require(address in {"0.0.0.0", endpoint}, "tcp-listen-address")
            inode = int(fields[9])
            require(integer(inode, 1, 2**63 - 1) and inode in sockets, "tcp-listen-owner")
            matches.append(
                {
                    "socket_inode": inode,
                    "owner_container_pid": 1,
                    "family": "ipv4",
                    "address_kind": "wildcard" if address == "0.0.0.0" else "owned_endpoint",
                    "port": port,
                }
            )
    require(len(matches) == 1, "tcp-listener-multiplicity")
    return matches[0]


def frontend_top(raw, pid):
    require(0 < len(raw) <= 16384, "frontend-top-bound")
    lines = raw.decode("ascii").splitlines()
    require(lines and lines[0].split() == ["PID", "PPID"] and 1 <= len(lines) - 1 <= 128, "frontend-top-header")
    rows = []
    for line in lines[1:]:
        fields = line.split()
        require(
            len(fields) == 2 and all(re.fullmatch(r"[0-9]+", item) is not None for item in fields), "frontend-top-row"
        )
        values = tuple(int(item) for item in fields)
        require(all(integer(item, 1, 2**63 - 1) for item in values), "frontend-top-types")
        rows.append(values)
    require(
        sum(row[0] == pid for row in rows) == 1 and len({row[0] for row in rows}) == len(rows),
        "frontend-top-association",
    )


class CycleCustody:
    """One actual selected transaction and its causal transport/readback joins."""

    def __init__(self, number, actor, seeded):
        require((number, actor) in ((1, "python"), (2, "rust")), "cycle_identity")
        closed(seeded, ("cycle", "actor", "scope", "request"))
        integer(seeded["cycle"], number, number)
        require(seeded["actor"] == actor, "cycle_seed_actor")
        scope = closed(seeded["scope"], ("foreign_execution_id", "attempt_id", "foreign_attempt_id"))
        for value in scope.values():
            canonical_uuid(value)
        request = closed(seeded["request"], ("schema", "case_id", "cohort", "operation", "projection"))
        require(
            request["schema"] == "bifrost.test.workflow-sql/v1" and request["case_id"] == "r-attempt-claimed-unstarted",
            "cycle_request_identity",
        )
        cohort = closed(request["cohort"], ("execution_id", "submitted_token"))
        owned = canonical_uuid(cohort["execution_id"])
        canonical_uuid(cohort["submitted_token"])
        operation = closed(request["operation"], ("kind", "lane", "raw_fields"))
        require(operation["kind"] == "result" and operation["lane"] == "success", "cycle_operation")
        fields = closed(
            operation["raw_fields"],
            (
                "status",
                "result",
                "error",
                "error_type",
                "duration_ms",
                "variables",
                "execution_context",
                "metrics",
                "roi",
            ),
        )
        for directive in fields.values():
            require(closed(directive, ("kind",))["kind"] == "absent", "cycle_raw_presence")
        require(
            owned != scope["foreign_execution_id"] and scope["attempt_id"] != scope["foreign_attempt_id"],
            "cycle_scope_identity",
        )
        self.number, self.actor = number, actor
        self.scope, self.request, self.owned = scope, request, owned
        self.frontend = self.snapshot = self.selected = self.arm_ack = None
        self.returned = self.transport = self.actor_end = self.readback = None
        self.native_complete = self.source_closed = None
        self.events = []

    def event(self, name):
        require(name not in {entry[0] for entry in self.events}, "cycle_event_duplicate")
        self.events.append((name, time.monotonic()))

    def frontend_ready(self, value, actor_address, relay_address, upstream_address, port):
        require(self.frontend is None, "cycle_frontend_duplicate")
        closed(
            value,
            (
                "cycle",
                "actor",
                "connection",
                "peer_address",
                "peer_port",
                "local_address",
                "local_port",
                "upstream_address",
                "upstream_port",
            ),
        )
        binding(value, self.number, self.actor)
        require(
            value["peer_address"] == actor_address
            and value["local_address"] == relay_address
            and value["upstream_address"] == upstream_address,
            "cycle_transport_addresses",
        )
        integer(value["peer_port"], 1, 65535)
        integer(value["local_port"], port, port)
        integer(value["upstream_port"], port, port)
        self.frontend = value
        self.event("frontend_ready")

    def post_flush(self, value):
        require(self.frontend is not None and self.snapshot is None, "cycle_snapshot_order")
        closed(value, ("cycle", "actor", "phase", "rows"))
        integer(value["cycle"], self.number, self.number)
        require(value["actor"] == self.actor and value["phase"] == "post_flush", "cycle_snapshot_identity")
        validate_rows(value["rows"])
        require(
            len(value["rows"]["executions"]) == 2 and len(value["rows"]["attempts"]) == 2, "cycle_snapshot_cardinality"
        )
        require(
            {row["id"]["value"] for row in value["rows"]["executions"]}
            == {self.owned, self.scope["foreign_execution_id"]}
            and {row["id"]["value"] for row in value["rows"]["attempts"]}
            == {self.scope["attempt_id"], self.scope["foreign_attempt_id"]},
            "cycle_snapshot_scope",
        )
        self.snapshot = value["rows"]
        self.event("post_flush")

    def selected_ready(self, value, database, postgres_address):
        require(self.snapshot is not None and self.selected is None, "cycle_selected_order")
        keys = ("cycle", "actor", "connection", "xid", "backend_pid", "query_witness")
        closed(value, (*keys, "source_witness") if self.actor == "python" else keys)
        binding(value, self.number, self.actor)
        witness = query_witness(value["query_witness"])
        require(
            value["xid"] == witness["xid"]
            and type(value["backend_pid"]) is int
            and value["backend_pid"] == witness["backend_pid"]
            and witness["database_name"] == database
            and witness["server_address"] == postgres_address
            and witness["server_port"] == 5432,
            "cycle_selected_transaction",
        )
        if self.actor == "python":
            sdk_witness(value["source_witness"])
        self.selected = value
        self.event("selected_ready")

    def armed(self, value):
        require(self.selected is not None and self.arm_ack is None, "cycle_arm_order")
        closed(value, ("cycle", "actor", "connection"))
        binding(value, self.number, self.actor)
        self.arm_ack = value
        self.event("armed")

    def listener_returned(self, value):
        require(self.arm_ack is not None and self.returned is None, "cycle_listener_order")
        closed(value, ("cycle", "actor", "connection"))
        binding(value, self.number, self.actor)
        self.returned = value
        self.event("listener_returned")

    def transport_closed(self, value):
        require(self.returned is not None and self.transport is None, "cycle_transport_order")
        closed(
            value, ("cycle", "actor", "connection", "upstream_bytes", "downstream_suppressed_bytes", "transport_closed")
        )
        binding(value, self.number, self.actor)
        integer(value["upstream_bytes"], 1, 2**64 - 1)
        integer(value["downstream_suppressed_bytes"], 1, 2**64 - 1)
        require(value["transport_closed"] is True, "cycle_transport_custody")
        self.transport = value
        self.event("transport_closed")

    def actor_finished(self, value):
        require(self.transport is not None and self.actor_end is None, "cycle_actor_finish_order")
        closed(value, ("cycle", "actor", "connection", "commit", "error", "pool_closed"))
        binding(value, self.number, self.actor)
        require(value["commit"] == "error" and value["pool_closed"] is True, "cycle_consumed_commit_unknown")
        outcome({"commit": value["commit"], "error": value["error"]})
        require(
            value["error"] is not None
            and value["error"]["family"] == ("python_connection_lost" if self.actor == "python" else "rust_io"),
            "cycle_commit_error_class",
        )
        self.actor_end = value
        self.event("actor_finished")

    def actor_settled(self, native):
        require(
            self.actor_end is not None
            and self.native_complete is None
            and native.settled
            and native.returncode == 1
            and native.stdout_eof is True
            and native.stderr_eof is True,
            "cycle_native_settlement",
        )
        self.native_complete = True
        self.event("actor_settled")
        return {
            "cycle": self.number,
            "actor": self.actor,
            "execution_id": self.owned,
            "scope": self.scope,
            "query_witness": self.selected["query_witness"],
        }

    def observed(self, value):
        require(self.native_complete is True and self.readback is None, "cycle_readback_order")
        closed(value, ("cycle", "actor", "query_witness", "fate", "rows"))
        integer(value["cycle"], self.number, self.number)
        require(
            value["actor"] == self.actor
            and query_witness(value["query_witness"]) == self.selected["query_witness"]
            and value["fate"] == "committed",
            "cycle_readback_transaction",
        )
        validate_rows(value["rows"])
        require(value["rows"] == self.snapshot, "cycle_all_after_rows")
        self.readback = value
        self.event("observed_committed")

    def exposed(self):
        return (
            self.readback is not None
            and self.native_complete is True
            and self.actor_end is not None
            and self.transport is not None
            and self.returned is not None
            and self.arm_ack is not None
            and self.selected is not None
            and self.snapshot is not None
            and self.frontend is not None
        )


class VolumeCustody:
    """The two selected new volumes; missing initial proof is irreversible."""

    def __init__(self, controller):
        self.controller = controller
        self.records = {}

    def acquire(self, purpose, end, environment):
        require(purpose in ("cargo", "target") and purpose not in self.records, "volume_acquisition_identity")
        controller = self.controller
        name = controller.values[purpose + "_volume"]
        record = {
            "purpose": purpose,
            "name": name,
            "initial_absent": False,
            "created": False,
            "qualified": None,
            "removed": False,
            "absent": False,
        }
        self.records[purpose] = record
        native = controller.run("volume." + purpose + ".initial", end, environment, allowed=(1,))
        require(
            controller.capture(native) in (b"", b"[]\n")
            and controller.capture(native, 1)
            == ("Error response from daemon: get " + name + ": no such volume\n").encode("ascii"),
            "volume_initial_unknown",
        )
        record["initial_absent"] = True
        native = controller.run("volume." + purpose + ".create", end, environment)
        require(controller.capture(native) == (name + "\n").encode("ascii"), "volume_created_name")
        record["created"] = True
        native = controller.run("volume." + purpose + ".qualified", end, environment)
        value = decode(controller.capture(native), 65536)
        require(type(value) is list and len(value) == 1 and type(value[0]) is dict, "volume_inspection_shape")
        observed = value[0]
        labels = observed.get("Labels")
        require(
            observed.get("Name") == name
            and observed.get("Driver") == "local"
            and observed.get("Scope") == "local"
            and observed.get("Options") in (None, {})
            and type(labels) is dict
            and labels.get("bifrost.f4.invocation") == controller.invocation
            and labels.get("bifrost.f4.candidate") == controller.source["head"],
            "volume_source_association",
        )
        record["qualified"] = observed
        return record

    def remove(self, record, end, environment):
        controller = self.controller
        require(
            record["initial_absent"] is True
            and record["created"] is True
            and record["qualified"] is not None
            and record["removed"] is False,
            "volume_removal_unowned",
        )
        native = controller.run("volume." + record["purpose"] + ".remove", end, environment, cleanup=True)
        require(controller.capture(native) == (record["name"] + "\n").encode("ascii"), "volume_removal_output")
        record["removed"] = True
        native = controller.run("volume." + record["purpose"] + ".absent", end, environment, allowed=(1,), cleanup=True)
        require(
            controller.capture(native) in (b"", b"[]\n")
            and controller.capture(native, 1)
            == ("Error response from daemon: get " + record["name"] + ": no such volume\n").encode("ascii"),
            "volume_absence_unknown",
        )
        record["absent"] = True


def cargo_container(controller, custody, purpose, end, environment):
    require(purpose in ("fetch", "fmt", "clippy", "default_tests", "release"), "cargo_purpose")
    cid = created_cid(controller, purpose + ".create", end, environment)
    record = custody.acquire(purpose, cid)
    observed = inspect_native(controller, purpose + ".qualified", end, environment, cid)
    commands = {
        "fetch": ["cargo", "fetch", "--locked"],
        "fmt": ["cargo", "fmt", "--all", "--check"],
        "clippy": [
            "cargo",
            "clippy",
            "--locked",
            "--offline",
            "--workspace",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ],
        "default_tests": ["cargo", "test", "--locked", "--offline", "--workspace"],
        "release": [
            "cargo",
            "build",
            "--locked",
            "--offline",
            "--release",
            "-p",
            "bifrost-db",
            "--features",
            "workflow-sql-parity",
            "--example",
            "workflow_sql_vectors",
        ],
    }
    native_container_config(
        controller,
        observed,
        purpose,
        controller.values["qualified_toolchain_id"],
        "bridge" if purpose == "fetch" else "none",
        "",
        command=commands[purpose],
    )
    require(observed["Config"].get("WorkingDir") == "/workspace/core-rs", "cargo_working_directory")
    mount_association(observed, controller.values["candidate_root"] + "/core-rs", "/workspace/core-rs", True)
    for key, destination in (("cargo", "/cargo"), ("target", "/target")):
        matches = [
            value for value in observed["Mounts"] if type(value) is dict and value.get("Destination") == destination
        ]
        require(
            len(matches) == 1
            and matches[0].get("Type") == "volume"
            and matches[0].get("Name") == controller.values[key + "_volume"]
            and matches[0].get("RW") is True,
            "cargo_volume_association",
        )
    environment_rows = observed["Config"].get("Env")
    require(
        type(environment_rows) is list
        and environment_rows.count("CARGO_HOME=/cargo") == 1
        and environment_rows.count("CARGO_TARGET_DIR=/target") == 1,
        "cargo_environment_association",
    )
    custody.admit_created(record, observed)
    native = controller.run(purpose + ".start", end, environment)
    record["native"] = native
    terminal = inspect_native(controller, purpose + ".terminal", end, environment, cid)
    custody.terminal(record, terminal, 0)
    return record


def close_private_files(controller):
    # Each returned descriptor receives one independent close attempt. No retry
    # of an integer whose prior close result is unknown can close a reused FD.
    for entry in controller.files.entries:
        if entry["kind"] != "file" or entry["close_attempted"]:
            continue
        descriptor = entry["descriptor"]
        if descriptor is None:
            continue
        entry["close_attempted"] = True
        try:
            os.close(descriptor)
            entry["descriptor"] = None
            entry["closed"] = True
        except BaseException as error:
            controller.files.failed = True
            controller.fail(error, cleanup=True)
    for entry in reversed(controller.files.entries):
        if entry["removed"]:
            continue
        try:
            controller.files.remove(entry)
        except BaseException as error:
            controller.files.failed = True
            controller.fail(error, cleanup=True)


def json_lines(raw, maximum, *, capture_limit=1024 * 1024):
    require(type(raw) is bytes and len(raw) <= capture_limit and (not raw or raw.endswith(b"\n")), "inventory_capture")
    lines = raw.splitlines()
    require(len(lines) <= maximum and all(lines), "inventory_cardinality")
    return [decode(line, 65536) for line in lines]


def stack_discovery(controller, end, environment):
    native = controller.run("stack.services.discover", end, environment)
    rows = json_lines(controller.capture(native), 11)
    require(len(rows) == len(STACK_SERVICES), "stack_service_cardinality")
    discovered = {}
    for row in rows:
        require(
            type(row) is dict and type(row.get("ID")) is str and re.fullmatch(r"[0-9a-f]{64}", row["ID"]),
            "stack_discovery_id",
        )
        labels = row.get("Labels")
        require(type(labels) is str, "stack_discovery_labels")
        label_map = {}
        for part in labels.split(","):
            key, separator, value = part.partition("=")
            require(separator == "=" and key and key not in label_map, "stack_discovery_label_encoding")
            label_map[key] = value
        service = label_map.get("com.docker.compose.service")
        require(
            service in STACK_SERVICES
            and service not in discovered
            and label_map.get("com.docker.compose.project") == controller.values["owned_project"]
            and label_map.get("com.docker.compose.oneoff") == "False"
            and label_map.get("com.docker.compose.container-number") == "1",
            "stack_discovery_source",
        )
        discovered[service] = row["ID"]
    require(len(set(discovered.values())) == 11, "stack_discovery_duplicate_id")
    controller.values["actual_CIDs_sorted_by_frozen_service_order"] = tuple(
        discovered[service] for service in STACK_SERVICES
    )
    controller.stack_discovered = discovered
    native = controller.run("stack.services.inspect", end, environment)
    values = decode(controller.capture(native), 16 * 1024 * 1024)
    require(type(values) is list and len(values) == 11, "stack_inspection_cardinality")
    observed = {}
    for service, value in zip(STACK_SERVICES, values, strict=True):
        value = container_object([value], discovered[service])
        labels = value["Config"].get("Labels")
        require(
            type(labels) is dict
            and labels.get("com.docker.compose.project") == controller.values["owned_project"]
            and labels.get("com.docker.compose.service") == service
            and labels.get("com.docker.compose.oneoff") == "False"
            and labels.get("com.docker.compose.container-number") == "1",
            "stack_container_source",
        )
        if service == "init":
            terminal_container(value, 0)
        else:
            live_container(value)
            health = value["State"].get("Health")
            if health is not None:
                require(type(health) is dict and health.get("Status") == "healthy", "stack_container_health")
        require(
            value["HostConfig"].get("Privileged") is False
            and value["HostConfig"].get("PidMode") == ""
            and value["HostConfig"].get("UsernsMode") == "",
            "stack_container_security",
        )
        observed[service] = value
    controller.stack_current = observed
    return observed


def stack_source_join(controller, observed, model):
    require(type(model) is dict and type(model.get("services")) is dict, "stack_source_model")
    api_services = {"init", "api", "api-replica", "worker", "scheduler", "scheduler-fixtures"}
    for service in STACK_SERVICES:
        source = model["services"].get(service)
        require(type(source) is dict, "stack_source_service_missing")
        value = observed[service]
        config, host = value["Config"], value["HostConfig"]
        if service in api_services:
            require(value["Image"] == controller.values["qualified_api_id"], "stack_api_image_join")
        if "command" in source:
            require(type(source["command"]) is list and config.get("Cmd") == source["command"], "stack_source_command")
        if "entrypoint" in source:
            require(
                type(source["entrypoint"]) is list and config.get("Entrypoint") == source["entrypoint"],
                "stack_source_entrypoint",
            )
        if "user" in source:
            require(type(source["user"]) is str and config.get("User") == source["user"], "stack_source_user")
        environment = source.get("environment", {})
        require(
            type(environment) is dict
            and all(type(key) is str and type(item) is str for key, item in environment.items()),
            "stack_source_environment",
        )
        actual_environment = environment_map(config.get("Env"))
        require(
            all(actual_environment.get(key) == item for key, item in environment.items()),
            "stack_source_environment_join",
        )
        mounts = source.get("volumes", [])
        require(type(mounts) is list, "stack_source_mounts")
        for mount in mounts:
            require(type(mount) is dict and type(mount.get("target")) is str, "stack_source_mount")
            matches = [
                item for item in value["Mounts"] if type(item) is dict and item.get("Destination") == mount["target"]
            ]
            require(
                len(matches) == 1
                and matches[0].get("Type") == mount.get("type")
                and type(matches[0].get("RW")) is bool
                and matches[0]["RW"] is (not mount.get("read_only", False)),
                "stack_source_mount_profile",
            )
            if mount.get("type") == "bind":
                require(
                    type(mount.get("source")) is str and matches[0].get("Source") == mount["source"],
                    "stack_source_bind",
                )
            elif mount.get("type") == "volume":
                source_name = mount.get("source")
                volume_model = model.get("volumes", {}).get(source_name)
                if source_name is not None:
                    require(
                        type(volume_model) is dict
                        and type(volume_model.get("name")) is str
                        and matches[0].get("Name") == volume_model["name"],
                        "stack_source_volume",
                    )
            else:
                raise Failure("stack_source_mount_unsupported")
        require(host.get("NetworkMode") == controller.values["owned_project"] + "_default", "stack_source_network_mode")
    network_name = controller.values["owned_project"] + "_default"
    endpoints = {}
    network_id = None
    for service, value in observed.items():
        networks = value["NetworkSettings"].get("Networks")
        require(type(networks) is dict and set(networks) == {network_name}, "stack_network_membership")
        endpoint = networks[network_name]
        require(type(endpoint) is dict, "stack_network_endpoint")
        if service == "init":
            # A successfully exited init is not a live endpoint witness.
            continue
        current_id = endpoint.get("NetworkID")
        require(type(current_id) is str and re.fullmatch(r"[0-9a-f]{64}", current_id), "stack_live_network_id")
        if network_id is None:
            network_id = current_id
        require(current_id == network_id, "stack_shared_network")
        address = endpoint.get("IPAddress")
        require(type(address) is str and str(ipaddress.IPv4Address(address)) == address, "stack_live_address")
        aliases = endpoint.get("Aliases")
        require(
            type(aliases) is list and service in aliases and all(type(item) is str for item in aliases),
            "stack_source_alias",
        )
        endpoints[service] = address
    require(
        network_id is not None and len(endpoints) == 10 and len(set(endpoints.values())) == 10,
        "stack_endpoint_cardinality",
    )
    controller.values["owned_network_id"] = network_id
    controller.values["owned_original_network_id"] = network_id
    controller.values["pgbouncer_cid"] = observed["pgbouncer"]["Id"]
    controller.values["actual_pgbouncer_image_id"] = observed["pgbouncer"]["Image"]
    controller.stack_endpoints = endpoints
    controller.stack_model = model
    return network_id


def frontend_run(controller, label, end, environment, bound):
    entered = time.monotonic()
    original = result = None
    try:
        remaining = 40 - controller.frontend_seconds
        require(remaining > 0, "frontend_total_expired")
        deadline = min(end, entered + 2, entered + remaining, controller.normal_end)
        result = controller.run(label, deadline, environment, limit=(bound, 65536), cleanup_end=deadline)
    except BaseException as error:
        original = error
    finally:
        controller.frontend_seconds += time.monotonic() - entered
        if controller.frontend_seconds > 40:
            exceeded = Failure("frontend_total_expired")
            controller.fail(exceeded)
            original = first_error(original, exceeded)
    if original is not None:
        raise original
    return result


def frontend_source_profile(controller, end, environment):
    require(not hasattr(controller, "frontend_version"), "frontend_profile_reentry")
    native = frontend_run(controller, "frontend.version", end, environment, 4096)
    require(controller.capture(native) == b"PgBouncer 1.26.0\n", "frontend_version_profile")
    controller.frontend_version = "1.26.0"
    native = frontend_run(controller, "frontend.entrypoint", end, environment, 16384)
    text = controller.capture(native)
    require(
        text == b"8449 9d9d23849f0180d7fb25263dca3870955c39e0fcf0211529b10238f280143333\n", "frontend_entrypoint_source"
    )
    controller.frontend_entrypoint_admitted = True


def frontend_bracket(controller, when, end, environment):
    require(
        when in ("before", "after")
        and controller.frontend_version == "1.26.0"
        and controller.frontend_entrypoint_admitted is True,
        "frontend_bracket_profile",
    )
    prefix = "frontend." + when + "."
    cid = controller.values["pgbouncer_cid"]
    require(getattr(controller, "frontend_" + when, None) is None, "frontend_bracket_reentry")
    native = frontend_run(controller, prefix + "1", end, environment, 256 * 1024)
    before = container_object(decode(controller.capture(native), 1024 * 1024), cid)
    live_container(before)
    container_stable(controller.stack_current["pgbouncer"], before)
    pid = before["State"]["Pid"]
    native = frontend_run(controller, prefix + "2", end, environment, 16384)
    frontend_top(controller.capture(native), pid)
    native = frontend_run(controller, prefix + "3", end, environment, 256 * 1024)
    process, sockets = parse_process(controller.capture(native), pid)
    native = frontend_run(controller, prefix + "4", end, environment, 131072)
    endpoint = controller.stack_endpoints["pgbouncer"]
    listener = parse_listener(controller.capture(native), sockets, endpoint, 5432)
    native = frontend_run(controller, prefix + "5", end, environment, 131072)
    config, config_identity = parse_config_readback(controller.capture(native))
    strict_frontend_config(config, endpoint, 5432, "bifrost")
    native = frontend_run(controller, prefix + "6", end, environment, 256 * 1024)
    network = decode(controller.capture(native), 1024 * 1024)
    network_admission(controller, network)
    native = frontend_run(controller, prefix + "7", end, environment, 4096)
    addresses = decode(controller.capture(native), 4096)
    require(addresses == [endpoint], "frontend_runner_endpoint")
    native = frontend_run(controller, prefix + "8", end, environment, 256 * 1024)
    after = container_object(decode(controller.capture(native), 1024 * 1024), cid)
    live_container(after)
    container_stable(before, after)
    require(
        after["State"]["Pid"] == pid and after["State"].get("StartedAt") == before["State"].get("StartedAt"),
        "frontend_process_lifetime",
    )
    observed = {
        "cid": cid,
        "image_id": before["Image"],
        "version": "1.26.0",
        "network_id": controller.values["owned_network_id"],
        "evidence": "Docker_API_same_CID",
        "process": process,
        "listener": listener,
    }
    private = {
        "observed": observed,
        "configuration": config,
        "configuration_identity": config_identity,
        "started_at": before["State"].get("StartedAt"),
    }
    if when == "after":
        initial = controller.frontend_before
        require(private == initial, "frontend_bracket_drift")
    setattr(controller, "frontend_" + when, private)
    return private


def network_admission(controller, value):
    require(type(value) is list and len(value) == 1 and type(value[0]) is dict, "network_inspection_shape")
    observed = value[0]
    labels = observed.get("Labels")
    require(
        observed.get("Id") == controller.values["owned_network_id"]
        and observed.get("Name") == controller.values["owned_project"] + "_default"
        and observed.get("Driver") == "bridge"
        and observed.get("Scope") == "local"
        and observed.get("Internal") is False
        and type(labels) is dict
        and labels.get("com.docker.compose.project") == controller.values["owned_project"]
        and labels.get("com.docker.compose.network") == "default",
        "network_source_join",
    )
    containers = observed.get("Containers")
    require(type(containers) is dict and 10 <= len(containers) <= 14, "network_endpoint_count")
    for service, address in controller.stack_endpoints.items():
        cid = controller.stack_current[service]["Id"]
        endpoint = containers.get(cid)
        require(
            type(endpoint) is dict
            and type(endpoint.get("IPv4Address")) is str
            and str(ipaddress.IPv4Interface(endpoint["IPv4Address"]).ip) == address,
            "network_endpoint_join",
        )
    allowed = {value["Id"] for value in controller.stack_current.values()}
    allowed.update(
        record["cid"]
        for record in controller.custody.records.values()
        if record["admitted"] is True and not record["removed"]
    )
    runner = controller.values.get("runner_cid")
    if runner is not None:
        allowed.add(runner)
    require(set(containers) <= allowed, "network_unknown_endpoint")
    return observed


READER_SOURCE_PATHS = (
    "api/src/config.py",
    "api/src/core/database.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/bifrost/__init__.py",
    "api/bifrost/_sync.py",
    "api/shared/workspace_effects.py",
    "api/tests/parity/workflow_commit_fault.py",
    "api/tests/parity/workflow_commit_fault_child.py",
    "api/tests/diagnostics/workflow_commit_fault.py",
)


def source_readback(controller, role, native, uid, gid):
    raw = controller.capture(native)
    require(0 < len(raw) <= 8193 and raw.endswith(b"\n") and raw.count(b"\n") == 1, "source_reader_framing")
    value = closed(decode(raw, 8193), ("schema", "role", "uid", "gid", "reader_pid", "pid1_namespaces", "sources"))
    require(
        value["schema"] == "bifrost.private.f4-source-namespace/v1"
        and value["role"] == role
        and type(value["uid"]) is int
        and value["uid"] == uid
        and type(value["gid"]) is int
        and value["gid"] == gid,
        "source_reader_identity",
    )
    integer(value["reader_pid"], 1, 2**31 - 1)
    namespaces = closed(value["pid1_namespaces"], ("user", "net", "pid"))
    for item in namespaces.values():
        closed(item, ("dev", "ino"))
        integer(item["dev"], 0, 2**64 - 1)
        integer(item["ino"], 1, 2**64 - 1)
    paths = list(READER_SOURCE_PATHS)
    if role == "rust":
        paths.append("/f4/bin/workflow_sql_vectors")
    records = value["sources"]
    require(type(records) is list and len(records) == len(paths), "source_reader_membership")
    for path, record in zip(paths, records, strict=True):
        closed(record, ("path", "sha256", "bytes"))
        container_path = "/app/" + path.removeprefix("api/") if path.startswith("api/") else path
        require(record["path"] == container_path, "source_reader_path")
        digest(record["sha256"], 64)
        limit = BINARY_LIMIT if path == "/f4/bin/workflow_sql_vectors" else SOURCE_LIMIT
        integer(record["bytes"], 1, limit)
        expected = controller.binary_source if path == "/f4/bin/workflow_sql_vectors" else controller.sources[path]
        size = expected["bytes"] if path == "/f4/bin/workflow_sql_vectors" else len(expected["bytes"])
        require(record["sha256"] == expected["sha256"] and record["bytes"] == size, "source_reader_bytes")
    return value


def direct_pid1_peer(state_pid, host_row, credentials):
    integer(state_pid, 1, 2**31 - 1)
    require(type(host_row) is tuple and len(host_row) == 4, "direct_pid1_top_shape")
    require(type(credentials) is tuple and len(credentials) == 3, "direct_pid1_credentials_shape")
    integer(host_row[0], 1, 2**31 - 1)
    integer(credentials[0], 1, 2**31 - 1)
    require(state_pid == host_row[0] == credentials[0], "direct_pid1_peer_join")


def process_peer_top(raw, peer, uid, gid):
    require(type(raw) is bytes and 0 < len(raw) <= 16384, "peer_top_bound")
    rows = raw.decode("ascii", "strict").splitlines()
    require(rows and rows[0].split() == ["PID", "PPID", "UID", "GID"] and 1 <= len(rows) - 1 <= 128, "peer_top_header")
    observed = []
    for row in rows[1:]:
        fields = row.split()
        require(len(fields) == 4 and all(re.fullmatch(r"[0-9]+", item) for item in fields), "peer_top_row")
        values = tuple(int(item) for item in fields)
        integer(values[0], 1, 2**31 - 1)
        integer(values[1], 0, 2**31 - 1)
        integer(values[2], 0, 2**32 - 1)
        integer(values[3], 0, 2**32 - 1)
        observed.append(values)
    require(len({row[0] for row in observed}) == len(observed), "peer_top_duplicate")
    credentials = struct.unpack(
        "3i", peer.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    matches = [row for row in observed if row[0] == credentials[0]]
    require(
        len(matches) == 1 and matches[0][2:] == (uid, gid) and credentials[1:] == (uid, gid), "peer_top_source_join"
    )
    peer.qualify(credentials[0], uid, gid)
    return matches[0]


def peer_source_admission(controller, role, cid, end, environment, uid, gid):
    peer = controller.await_peer(role, end)
    label = "runner" if role == "observer" else role
    current = inspect_native(controller, label + (".inspect" if role == "observer" else ".live"), end, environment, cid)
    live_container(current)
    if role != "observer":
        record = controller.custody.records[role]
        require(record["admitted"] is True, "peer_container_unowned")
        container_stable(record["created"], current)
        source_mounts(controller, current)
        record["current"] = current
    native = controller.run(label + ".top", end, environment, limit=(16384, 65536))
    host_row = process_peer_top(controller.capture(native), peer, uid, gid)
    native = controller.run(label + ".source", end, environment, limit=(8193, 65536))
    reader = source_readback(controller, role, native, uid, gid)
    direct_pid1_peer(current["State"]["Pid"], host_row, peer.credentials)
    controller.peer_sources[role] = {"container": current, "reader": reader, "host_row": host_row}
    return peer


def actor_created(controller, purpose, end, environment):
    require(purpose in ("relay", "python", "rust"), "actor_purpose")
    cid = created_cid(controller, purpose + ".create", end, environment)
    record = controller.custody.acquire(purpose, cid)
    # The actor roster has a live inspection after attach, not a Created
    # inspection. Store the returned ID immediately; admission awaits genuine
    # live Config/State before the socket peer is allowed to receive secrets.
    record["actor_acquired_at"] = time.monotonic()
    return record


def actor_live_admission(controller, role, record, end, environment):
    cid = record["cid"]
    value = inspect_native(controller, role + ".live", end, environment, cid)
    live_container(value)
    native_container_config(
        controller,
        value,
        role,
        controller.values["qualified_api_id"],
        controller.values["owned_network_id"],
        controller.values["actual_host_uid"] + ":" + controller.values["actual_host_gid"],
        command=["/app/tests/parity/workflow_commit_fault_child.py", role]
        if role != "rust"
        else ["apply-result-fault"],
        entrypoint=["python"] if role != "rust" else ["/f4/bin/workflow_sql_vectors"],
    )
    source_mounts(controller, value)
    mount_association(value, controller.values[role + "_socket"], "/run/f4-control.sock", True)
    if role == "rust":
        mount_association(value, controller.values["qualified_binary"], "/f4/bin/workflow_sql_vectors", True)
    if role == "python":
        actor_environment_match(
            value["Config"].get("Env"), controller.supplied_environment, controller.api_inherited_environment
        )
    elif role == "rust":
        actual = environment_map(value["Config"].get("Env"))
        expected = dict(controller.api_inherited_environment)
        expected["BIFROST_RUST_TEST_DATABASE_URL"] = controller.rust_dsn
        require(actual == expected, "rust_image_environment_join")
    else:
        require(
            environment_map(value["Config"].get("Env")) == controller.api_inherited_environment,
            "relay_image_environment_join",
        )
    extra = value["HostConfig"].get("ExtraHosts")
    expected_route = [controller.values["original_hostname"] + ":" + controller.values["actual_relay_ip"]]
    require(
        extra == expected_route if role in ("python", "rust") else extra in (None, []), "actor_selected_frontend_route"
    )
    network = value["NetworkSettings"].get("Networks")
    require(type(network) is dict and len(network) == 1, "actor_network_count")
    endpoint = next(iter(network.values()))
    require(
        type(endpoint) is dict and endpoint.get("NetworkID") == controller.values["owned_network_id"],
        "actor_network_association",
    )
    address = endpoint.get("IPAddress")
    require(type(address) is str and str(ipaddress.IPv4Address(address)) == address, "actor_network_address")
    record["created"] = value
    record["current"] = value
    record["admitted"] = True
    record["address"] = address
    if role == "relay":
        controller.values["actual_relay_ip"] = address
    return value


def actor_peer_admission(controller, role, record, end, environment):
    peer = controller.await_peer(role, end)
    native = controller.run(role + ".top", end, environment, limit=(16384, 65536))
    uid, gid = int(controller.values["actual_host_uid"]), int(controller.values["actual_host_gid"])
    host_row = process_peer_top(controller.capture(native), peer, uid, gid)
    direct_pid1_peer(record["current"]["State"]["Pid"], host_row, peer.credentials)
    native = controller.run(role + ".source", end, environment, limit=(8193, 65536))
    reader = source_readback(controller, role, native, uid, gid)
    controller.peer_sources[role] = {"container": record["current"], "reader": reader, "host_row": host_row}
    return peer


def mutation_cycle(controller, number, actor, seeded, observer, relay, environment):
    entered = time.monotonic()
    end = min(entered + 30, controller.case_end, controller.target_end, controller.normal_end, controller.whole_end)
    work = min(end, controller.work_end)
    require(entered < work, "actor_work_expired")
    cycle = CycleCustody(number, actor, seeded)
    controller.cycles.append(cycle)
    input_record = {
        "schema": "bifrost.test.workflow-result-fault-input/v1",
        "invocation": controller.invocation,
        "cycle": number,
        "actor": actor,
        "scope": cycle.scope,
        "request": cycle.request,
    }
    record = actor_created(controller, actor, work, environment)
    native = controller.launch(
        actor + ".start", end, environment, stdin=encode(input_record, 65536), limit=65536, cleanup_end=end
    )
    record["native"] = native
    actor_live_admission(controller, actor, record, work, environment)
    peer = actor_peer_admission(controller, actor, record, work, environment)
    controller.receive(peer, "hello", (), work)
    controller.send(peer, "hello_accept", {}, work)
    transport = controller.receive(
        relay,
        "frontend_ready",
        (
            "cycle",
            "actor",
            "connection",
            "peer_address",
            "peer_port",
            "local_address",
            "local_port",
            "upstream_address",
            "upstream_port",
        ),
        work,
    )
    cycle.frontend_ready(
        transport,
        record["address"],
        controller.values["actual_relay_ip"],
        controller.stack_endpoints["pgbouncer"],
        5432,
    )
    if actor == "python":
        ready = controller.receive(
            peer,
            "connection_ready",
            (
                "schema",
                "role",
                "engine_join",
                "preconnect_match",
                "record_join",
                "checkout_join",
                "selected_connection_join",
                "frontend_join",
                "negotiated_tls",
            ),
            work,
        )
        connection_ready(ready, "python")
        controller.send(peer, "connection_accept", {"role": "python"}, work)
        constructed = controller.receive(
            peer,
            "constructor_ready",
            (
                "schema",
                "role",
                "env_count",
                "env_source_equal",
                "source_calls",
                "endpoint",
                "engine",
                "factory",
                "connection",
                "restoration",
            ),
            work,
        )
        constructor(constructed, "python", controller.values["original_hostname"], 5432)
        controller.send(peer, "constructor_accept", {"role": "python"}, work)
    snapshot = controller.receive(peer, "snapshot", ("cycle", "actor", "phase", "rows"), work, data=True)
    cycle.post_flush(snapshot)
    ready_keys = ("cycle", "actor", "connection", "xid", "backend_pid", "query_witness")
    selected = controller.receive(
        peer, "selected_ready", (*ready_keys, "source_witness") if actor == "python" else ready_keys, work
    )
    cycle.selected_ready(selected, "bifrost_test", controller.stack_endpoints["postgres"])
    binding_record = {"cycle": number, "actor": actor, "connection": number}
    controller.send(relay, "arm", binding_record, work)
    cycle.armed(controller.receive(relay, "armed", tuple(binding_record), work))
    controller.send(peer, "release_commit", binding_record, work)
    cycle.listener_returned(controller.receive(peer, "listener_returned", tuple(binding_record), work))
    cycle.transport_closed(
        controller.receive(
            relay,
            "settled",
            ("cycle", "actor", "connection", "upstream_bytes", "downstream_suppressed_bytes", "transport_closed"),
            work,
        )
    )
    cycle.actor_finished(
        controller.receive(
            peer, "actor_finished", ("cycle", "actor", "connection", "commit", "error", "pool_closed"), end
        )
    )
    controller.wait(native)
    require(native.returncode == 1, "actor_unknown_commit_native_status")
    if actor == "python":
        require(
            controller.capture(native) == b"" and controller.capture(native, 1) == b"",
            "python_actor_static_error_boundary",
        )
    else:
        rust_unknown_response(controller.capture(native))
        require(controller.capture(native, 1) == b"", "rust_actor_static_error_boundary")
    terminal = inspect_native(controller, actor + ".terminal", end, environment, record["cid"])
    controller.custody.terminal(record, terminal, 1)
    settled = cycle.actor_settled(native)
    controller.send(observer, "actor_settled", settled, min(controller.work_end, end))
    observed = controller.receive(
        observer,
        "observed",
        ("cycle", "actor", "query_witness", "fate", "rows"),
        min(controller.work_end, end),
        data=True,
    )
    cycle.observed(observed)
    controller.custody.remove(record, end, environment)
    return cycle


def rust_unknown_response(raw):
    value = closed(decode(raw, 4096), ("schema", "case_id", "decision", "transaction", "clock_witness"))
    require(
        value["schema"] == "bifrost.test.workflow-sql-result/v2" and value["case_id"] == "r-attempt-claimed-unstarted",
        "rust_response_identity",
    )
    decision = closed(value["decision"], ("kind", "stage", "class", "sqlstate"))
    require(
        decision == {"kind": "infrastructure_failure", "stage": "Commit", "class": "Resource", "sqlstate": None},
        "rust_commit_response",
    )
    transaction = closed(value["transaction"], ("status", "affected_execution_rows", "affected_attempt_rows"))
    require(transaction["status"] == "unknown", "rust_unknown_settlement")
    integer(transaction["affected_execution_rows"], 1, 1)
    integer(transaction["affected_attempt_rows"], 1, 1)
    clock = closed(value["clock_witness"], ("complete", "attempt", "logical", "commit"))
    require(clock["complete"] is False, "rust_unknown_clock_complete")
    events = []
    for name, keys in (
        ("attempt", ("read_ack", "sample", "write_dispatch", "write_ack")),
        ("logical", ("status_read_ack", "sample", "upper", "upper_kind", "write_ack")),
        ("commit", ("dispatch", "ack")),
    ):
        if name == "logical":
            # The admitted fixed request has absent duration, so the actual
            # adapter creates no logical clock component. This is source-bound
            # presence, not a synthetic clock or a waived incomplete object.
            require(clock[name] is None, "rust_absent_duration_clock")
            continue
        component = closed(clock[name], keys)
        for key, event in component.items():
            if key == "upper_kind":
                require(event in ("context_read_dispatch", "write_execution_dispatch"), "rust_clock_upper_kind")
                continue
            if name == "commit" and key == "ack":
                require(event is None, "rust_unknown_commit_ack")
                continue
            closed(event, ("ordinal", "utc_us", "elapsed_ns"))
            integer(event["ordinal"], 1, 2**64 - 1)
            integer(event["utc_us"], -(2**63), 2**63 - 1)
            integer(event["elapsed_ns"], 0, 2**64 - 1)
            events.append(event)
    require(len({event["ordinal"] for event in events}) == len(events), "rust_clock_duplicate_event")
    ordered = sorted(events, key=lambda event: event["ordinal"])
    require(
        all(left["elapsed_ns"] <= right["elapsed_ns"] for left, right in itertools.pairwise(ordered)),
        "rust_clock_order",
    )
    return value


def volume_inventory(raw):
    records = json_lines(raw, 4096, capture_limit=16 * 1024 * 1024)
    result = {}
    for record in records:
        closed(record, {"name", "driver", "scope"})
        name, driver, scope = record["name"], record["driver"], record["scope"]
        require(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", name) is not None
            and type(driver) is str
            and 0 < len(driver.encode("utf-8")) <= 128
            and type(scope) is str
            and scope in ("local", "global")
            and name not in result,
            "volume_census_identity",
        )
        result[name] = record
    return result


def project_inventory(controller, when, end, environment, *, cleanup=False, kinds=None):
    require(when in ("initial", "prepr_final", "target_final"), "project_inventory_phase")
    if kinds is None:
        kinds = ("containers", "volumes", "networks")
    require(
        type(kinds) is tuple
        and len(set(kinds)) == len(kinds)
        and all(kind in ("containers", "volumes", "networks") for kind in kinds),
        "project_inventory_kinds",
    )
    result = controller.project_inventories.setdefault(when, {})
    original = None
    for kind in kinds:
        try:
            require(kind not in result, "project_inventory_repeated")
            native = controller.run("inventory." + when + "." + kind, end, environment, cleanup=cleanup)
            raw = controller.capture(native)
            if kind == "volumes":
                measured = volume_inventory(raw)
                result[kind] = measured
                if when == "initial":
                    controller.initial_volume_names = frozenset(measured)
                    planned = {controller.values["cargo_volume"], controller.values["target_volume"]}
                    require(not planned.intersection(measured), "initial_planned_volume_present")
                else:
                    initial = getattr(controller, "initial_volume_names", None)
                    require(initial is not None and frozenset(measured) == initial, "volume_census_set_changed")
                    if when == "target_final":
                        require(
                            not set(getattr(controller, "task_volume_mounts", {})).intersection(measured),
                            "task_volume_remaining",
                        )
                        controller.target_volume_absence = True
            else:
                result[kind] = json_lines(raw, 128)
                require(result[kind] == [], "project_inventory_not_empty")
        except BaseException as error:
            original = first_error(original, error)
            controller.fail(error, cleanup=cleanup)
            result[kind] = None
    if original is not None:
        raise original
    return result


def retain_volume_mounts(controller, observed, source, model):
    initial = getattr(controller, "initial_volume_names", None)
    require(initial is not None, "volume_initial_missing")
    mounts = source.get("volumes", [])
    require(type(mounts) is list, "volume_source_mounts")
    declared = {}
    for mount in mounts:
        require(type(mount) is dict and type(mount.get("target")) is str, "volume_source_mount")
        require(mount["target"] not in declared, "volume_source_duplicate")
        declared[mount["target"]] = mount
    image_volumes = None
    for image_record in controller.image_custody.records.values():
        if image_record["qualified"] is True and image_record["id"] == observed["Image"]:
            image_volumes = image_record["first"]["Config"].get("Volumes")
            require(image_volumes is None or type(image_volumes) is dict, "volume_image_declarations")
            break
    # Only same immutable qualified image metadata can explain image volumes.
    obligations = getattr(controller, "task_volume_obligations", None)
    if obligations is None:
        obligations = {}
        controller.task_volume_obligations = obligations
    for destination, mount in declared.items():
        if mount.get("type") == "volume":
            key = (observed["Id"], destination)
            require(key not in obligations, "volume_obligation_duplicate")
            obligations[key] = None
    if image_volumes is not None:
        for destination in image_volumes:
            require(type(destination) is str, "volume_image_destination")
            if destination not in declared:
                key = (observed["Id"], destination)
                require(key not in obligations, "volume_obligation_duplicate")
                obligations[key] = None
    expected = getattr(controller, "task_volume_expected", None)
    if expected is None:
        expected = set()
        controller.task_volume_expected = expected
    expected.update(key for key in obligations if key[0] == observed["Id"])
    seen = set()
    records = getattr(controller, "task_volume_mounts", None)
    if records is None:
        records = {}
        controller.task_volume_mounts = records
    for actual in observed["Mounts"]:
        require(type(actual) is dict, "volume_mount_shape")
        if actual.get("Type") != "volume":
            continue
        destination = actual.get("Destination")
        require(type(destination) is str and destination not in seen, "volume_mount_destination")
        seen.add(destination)
        mount = declared.get(destination)
        require(
            (mount is not None and mount.get("type") == "volume")
            or (mount is None and image_volumes is not None and destination in image_volumes),
            "volume_mount_unexplained",
        )
        name, driver, rw = actual.get("Name"), actual.get("Driver"), actual.get("RW")
        require(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", name) is not None
            and name not in initial
            and type(driver) is str
            and 0 < len(driver.encode("utf-8")) <= 128
            and type(rw) is bool,
            "volume_mount_identity",
        )
        if mount is not None:
            require(rw is (not mount.get("read_only", False)), "volume_mount_rw")
            source_name = mount.get("source")
            if source_name is not None:
                configured = model.get("volumes", {}).get(source_name)
                require(
                    type(configured) is dict and configured.get("name") == name,
                    "volume_mount_named_source",
                )
        record = {
            "cid": observed["Id"],
            "image": observed["Image"],
            "destination": destination,
            "name": name,
            "driver": driver,
            "rw": rw,
        }
        references = records.setdefault(name, [])
        require(record not in references, "volume_mount_reference_duplicate")
        require(all(value["driver"] == driver for value in references), "volume_mount_driver_changed")
        references.append(record)
        key = (observed["Id"], destination)
        require(key in obligations, "volume_reference_without_obligation")
        obligations[key] = record
    require(
        all(value is not None for key, value in obligations.items() if key[0] == observed["Id"]),
        "volume_source_destination_missing",
    )


def lane_projection(controller, actor):
    value = {key: None for key in LANE_FIELDS}
    cycle = next((cycle for cycle in controller.cycles if cycle.actor == actor), None)
    if cycle is None:
        return value
    if cycle.frontend is not None:
        value["frontend_matches"] = True
    if cycle.selected is not None:
        value["transaction_witness_matches"] = True
        value["selected_writes_observed"] = cycle.snapshot is not None
    if cycle.returned is not None:
        value["listener_returned"] = True
    if cycle.arm_ack is not None:
        value["armed"] = True
    if cycle.transport is not None:
        value["upstream_forwarded"] = cycle.transport["upstream_bytes"]
        value["downstream_dropped"] = cycle.transport["downstream_suppressed_bytes"]
    if cycle.actor_end is not None:
        value["client_outcome"] = {"commit": cycle.actor_end["commit"], "error": cycle.actor_end["error"]}
    if cycle.readback is not None:
        value["server_status"] = "committed"
        value["row_relation"] = "all_after"
    if cycle.native_complete is not None:
        value["exposure"] = cycle.exposed()
    if getattr(controller, "observer_source_closed", None) is True:
        record = controller.custody.records.get(actor)
        value["cleanup_complete"] = record is not None and record["absent"] is True
    return value


def fault_projection(controller, qualified):
    source = getattr(controller, "manifest_sha256", None)
    binary = getattr(controller, "binary_source", None)
    value = {
        "schema": "bifrost.test.workflow-commit-fault/v1",
        "case_id": "r-tx-lost-commit-response",
        "source_sha256": {"source_manifest": source, "rust_binary": None if binary is None else binary["sha256"]},
        "lanes": {actor: lane_projection(controller, actor) for actor in ("python", "rust")},
        "passed": qualified,
    }
    return validate_fault(value, qualified)


def producer_projection(controller, primary_exit, qualified):
    value = {
        "schema": "bifrost.test.workflow-commit-producer/v1",
        "source": dict(controller.source),
        "stage": controller.stage,
        "commands": [native.record() for native in controller.native if native.process is not None],
        "primary_exit": primary_exit,
        "complete": qualified,
    }
    return validate_producer(value, qualified)


def container_admission_gaps(attempts, records):
    require(type(attempts) is dict and type(records) is dict, "container_gap_ledger_missing")
    actual_cids = {record["cid"] for record in records.values()}
    return sum(cid is None or cid not in actual_cids for cid in attempts.values())


def volume_resource_counts(initial, prepr, final, expected, obligations, references):
    # This describes applicable target identities, never opaque historical acquisitions.
    require(type(initial) is dict and type(prepr) is dict and type(final) is dict, "volume_projection_census_missing")
    require(set(initial) == set(prepr) == set(final), "volume_projection_census_changed")
    require(type(obligations) is dict and type(references) is dict, "volume_projection_ledger_missing")
    require(type(expected) is frozenset and set(obligations) == expected, "volume_projection_expected_missing")
    joined = {}
    for key, value in obligations.items():
        require(
            type(key) is tuple
            and len(key) == 2
            and type(value) is dict
            and value.get("cid") == key[0]
            and value.get("destination") == key[1],
            "volume_projection_obligation_missing",
        )
        name, driver = value.get("name"), value.get("driver")
        require(
            type(name) is str
            and name not in initial
            and name not in final
            and type(driver) is str
            and type(value.get("rw")) is bool
            and type(value.get("image")) is str,
            "volume_projection_identity_unresolved",
        )
        joined.setdefault(name, []).append(value)
    require(set(joined) == set(references), "volume_projection_reference_missing")
    for name, values in references.items():
        require(
            type(values) is list
            and len(values) > 0
            and len(values) == len(joined[name])
            and all(values.count(value) == 1 and value in joined[name] for value in values)
            and all(value["driver"] == values[0]["driver"] for value in values),
            "volume_projection_reference_conflict",
        )
    return resource_count(len(joined), len(joined), 0, 0)


def resource_count(admitted=None, removed=None, remaining=None, unknown=None):
    return {"admitted": admitted, "removed": removed, "remaining": remaining, "unknown": unknown}


def disposal_projection(controller, primary_exit):
    # Null remains null when there was no actual complete census/readback. In
    # particular an unattempted acquisition is not an invented zero census.
    resources = {name: resource_count() for name in ("containers", "networks", "volumes", "images", "cargo", "files")}
    custody = getattr(controller, "custody", None)
    if custody is not None:
        records = list(custody.records.values())
        resources["containers"] = resource_count(
            sum(record["admitted"] is True for record in records),
            sum(record["absent"] is True for record in records),
            sum(record["admitted"] is True and record["absent"] is False for record in records),
            sum(record["admitted"] is False or record["unknown"] is True for record in records),
        )
    attempts = getattr(controller, "container_attempts", {})
    gaps = container_admission_gaps(attempts, {} if custody is None else custody.records)
    if gaps:
        resources["containers"]["unknown"] = (resources["containers"]["unknown"] or 0) + gaps
    volumes = getattr(controller, "volume_custody", None)
    if volumes is not None:
        records = list(volumes.records.values())
        resources["cargo"] = resource_count(
            sum(record["qualified"] is not None for record in records),
            sum(record["absent"] is True for record in records),
            sum(record["qualified"] is not None and record["absent"] is False for record in records),
            sum(record["qualified"] is None for record in records),
        )
    inventories = getattr(controller, "project_inventories", {})
    target = inventories.get("target_final")
    initial = inventories.get("initial")
    prepr = inventories.get("prepr_final")
    if (
        initial is not None
        and prepr is not None
        and target is not None
        and all(value.get("networks") == [] for value in (initial, prepr, target))
    ):
        admitted = getattr(controller, "project_resource_admitted", {}).get("networks")
        if admitted is not None:
            resources["networks"] = resource_count(admitted, admitted, 0, 0)
    mounts = getattr(controller, "task_volume_mounts", None)
    obligations = getattr(controller, "task_volume_obligations", None)
    if mounts is not None or obligations is not None:
        try:
            require(
                getattr(controller, "target_mount_observation_complete", False), "volume_projection_source_incomplete"
            )
            require(getattr(controller, "target_volume_absence", False), "volume_projection_absence_missing")
            resources["volumes"] = volume_resource_counts(
                None if initial is None else initial.get("volumes"),
                None if prepr is None else prepr.get("volumes"),
                None if target is None else target.get("volumes"),
                frozenset(controller.task_volume_expected),
                obligations,
                mounts,
            )
        except Failure:
            resources["volumes"] = resource_count(
                None if mounts is None else len(mounts),
                None,
                None,
                max(1, sum(value is None for value in (obligations or {}).values())),
            )
    images = getattr(controller, "image_custody", None)
    if images is not None:
        records = list(images.records.values())
        resources["images"] = resource_count(
            sum(record["qualified"] for record in records),
            sum(record["absent"] for record in records),
            sum(record["qualified"] and not record["absent"] for record in records),
            sum(not record["qualified"] or record["unknown"] for record in records),
        )
    for label, purpose in (("native.toolchain.build", "toolchain"), ("api.build", "api")):
        if label in controller.used and (images is None or purpose not in images.records):
            resources["images"]["unknown"] = (resources["images"]["unknown"] or 0) + 1
    files = controller.files.entries
    if files:
        resources["files"] = resource_count(
            sum(entry["identity"] is not None for entry in files),
            sum(entry["removed"] is True for entry in files),
            sum(entry["identity"] is not None and entry["removed"] is False for entry in files),
            sum(entry["identity"] is None or (entry["kind"] == "file" and entry["closed"] is False) for entry in files),
        )
    log = getattr(controller, "log_tree", None)
    if log is not None and log.identity is not None:
        actual = resources["files"]
        if all(value is not None for value in actual.values()):
            actual["admitted"] += 1
            actual["removed"] += int(log.removed)
            actual["remaining"] += int(not log.removed)
            actual["unknown"] += int(log.unknown)
    stack = getattr(controller, "stack_current", None)
    runner = getattr(controller, "runner_current", None)
    if stack is not None:
        additional = len(stack) + int(runner is not None)
        resources["containers"]["admitted"] += additional
        removed = additional if getattr(controller, "project_disposed", False) else 0
        resources["containers"]["removed"] += removed
        resources["containers"]["remaining"] += additional - removed
        resources["containers"]["unknown"] += int(not getattr(controller, "project_disposed", False))
    if "stack.up" in controller.used and (stack is None or len(stack) != len(STACK_SERVICES)):
        resources["containers"]["unknown"] = (resources["containers"]["unknown"] or 0) + len(STACK_SERVICES)
    if "runner.start" in controller.used and runner is None:
        resources["containers"]["unknown"] = (resources["containers"]["unknown"] or 0) + 1
    file_records = [entry for entry in files if entry["kind"] == "file"]
    closed_captures = all(entry["closed"] is True for entry in file_records) if file_records else None
    removed_captures = all(entry["removed"] is True for entry in file_records) if file_records else None
    started = [native for native in controller.native if native.process is not None]
    process_settled = sum(native.settled is True for native in started)
    credentials = getattr(controller, "credentials_disposed", None)
    complete = (
        not controller.cleanup_failed
        and process_settled == len(started)
        and closed_captures is True
        and removed_captures is True
        and credentials is True
        and all(
            all(item is not None for item in value.values())
            and value["admitted"] == value["removed"]
            and value["remaining"] == 0
            and value["unknown"] == 0
            for value in resources.values()
        )
    )
    value = {
        "schema": "bifrost.test.workflow-commit-disposal/v1",
        "primary_exit": primary_exit,
        "cleanup_exit": 0 if complete else 1,
        "complete": complete,
        "processes": {"started": len(started), "settled": process_settled},
        "resources": resources,
        "private_captures": {"closed": closed_captures, "removed": removed_captures},
        "credentials_disposed": credentials,
    }
    return validate_disposal(value, complete)


def projection_controls():
    """Private inert values use the same publication admission functions."""
    for number, actor, family, code in (
        (1, "python", "python_connection_lost", "08003"),
        (2, "rust", "rust_io", "unexpected_eof"),
    ):
        cycle = object.__new__(CycleCustody)
        cycle.number, cycle.actor = number, actor
        cycle.transport, cycle.actor_end, cycle.events = {}, None, []
        value = {
            "cycle": number,
            "actor": actor,
            "connection": number,
            "commit": "error",
            "error": {"family": family, "code": code},
            "pool_closed": True,
        }
        cycle.actor_finished(value)
        require(
            cycle.actor_end is value and cycle.events[0][0] == "actor_finished", "actor_finish_control_real_envelope"
        )
        for error_pair in ({"family": family, "code": "unknown"}, {"family": family}, None):
            cycle = object.__new__(CycleCustody)
            cycle.number, cycle.actor = number, actor
            cycle.transport, cycle.actor_end, cycle.events = {}, None, []
            try:
                cycle.actor_finished({**value, "error": error_pair})
            except Failure:
                require(cycle.actor_end is None and cycle.events == [], "actor_finish_control_no_partial_admission")
            else:
                raise Failure("actor_finish_control_not_rejected")
    direct_pid1_peer(12, (12, 1, 1000, 1000), (12, 1000, 1000))
    for arguments in (
        (1, (12, 1, 1000, 1000), (12, 1000, 1000)),
        (12, (12, 1, 1000, 1000), (13, 1000, 1000)),
        (True, (1, 0, 1000, 1000), (1, 1000, 1000)),
    ):
        try:
            direct_pid1_peer(*arguments)
        except Failure:
            pass
        else:
            raise Failure("direct_pid1_control_not_rejected")

    class CloseSocketControl:
        def __init__(self, error=None):
            self.error, self.calls, self.fd = error, 0, 12

        def close(self):
            self.calls += 1
            if self.error is not None:
                raise self.error
            self.fd = -1

        def fileno(self):
            return self.fd

    class SelectorControl:
        def __init__(self, error=None):
            self.error, self.calls = error, 0

        def unregister(self, connection):
            self.calls += 1
            if self.error is not None:
                raise self.error

    for listener in (False, True):
        for failed_unregister, failed_close in ((False, False), (True, False), (True, True)):
            first = Failure("inert-unregister") if failed_unregister else None
            secondary = Failure("inert-close") if failed_close else None
            connection = CloseSocketControl(secondary)
            selector = SelectorControl(first)
            peer = object.__new__(Peer)
            peer.connection, peer.closed, peer.failed = connection, False, False
            record = {
                "close_attempted": False,
                "closed": False,
                "registered": True,
                "socket": connection,
                "connection": connection,
                "peer": peer,
            }
            caught = None
            try:
                close_owned_ipc(record, selector, listener=listener)
            except BaseException as error:
                caught = error
            require(caught is first and connection.calls == 1, "ipc_control_first_and_independent_close")
            require(record["closed"] is (not failed_close), "ipc_control_actual_closure")
            close_owned_ipc(record, selector, listener=listener)
            require(connection.calls == 1 and selector.calls == 1, "ipc_control_no_uncertain_retry")
    require(public_json({}, 3) == b"{}\n", "public_json_control_exact_bound")
    require(public_json({"text": "line\nnext"}, 64).count(b"\n") == 1, "public_json_control_only_final_lf")
    require(encode({}, 2) == b"{}", "private_json_control_unchanged")
    try:
        public_json({}, 2)
    except Failure:
        pass
    else:
        raise Failure("public_json_control_bound_not_rejected")

    class MountControl:
        def __init__(self, initial_names, images):
            self.initial_volume_names = frozenset(initial_names)
            self.image_custody = ImageCustody(None)
            self.image_custody.records = images

    def mount_control(source, actual, images, initial_names=()):
        context = MountControl(initial_names, images)
        observed = {
            "Id": "inert-cid",
            "Image": "inert-image",
            "Mounts": actual,
            "Config": {"Volumes": {"/image-only": {}}},
        }
        retain_volume_mounts(context, observed, {"volumes": source}, {"volumes": {"named": {"name": "other-name"}}})
        return context

    actual_mount = {"Type": "volume", "Destination": "/anonymous", "Name": "owned", "Driver": "local", "RW": True}
    source_mount = {"type": "volume", "target": "/anonymous"}
    positive = mount_control([source_mount], [actual_mount], {})
    require(len(positive.task_volume_mounts["owned"]) == 1, "mount_control_source_anonymous")
    image_metadata = {
        "api": {"id": "inert-image", "qualified": True, "first": {"Config": {"Volumes": {"/image-only": {}}}}}
    }
    image_mount = {**actual_mount, "Destination": "/image-only"}
    positive = mount_control([], [image_mount], image_metadata)
    require(len(positive.task_volume_mounts["owned"]) == 1, "mount_control_same_image_declaration")
    mount_negatives = (
        ([source_mount], [], {}, ()),
        ([], [image_mount], {}, ()),
        ([], [image_mount], {"api": {**image_metadata["api"], "id": "wrong-image"}}, ()),
        ([source_mount], [actual_mount, dict(actual_mount)], {}, ()),
        ([source_mount], [{**actual_mount, "Type": "bind"}], {}, ()),
        ([source_mount], [{**actual_mount, "RW": "true"}], {}, ()),
        ([{**source_mount, "read_only": True}], [actual_mount], {}, ()),
        ([{**source_mount, "source": "named"}], [actual_mount], {}, ()),
        ([source_mount], [actual_mount], {}, ("owned",)),
        ([], [image_mount], {"borrowed": {**image_metadata["api"], "qualified": False}}, ()),
    )
    for arguments in mount_negatives:
        try:
            mount_control(*arguments)
        except Failure:
            pass
        else:
            raise Failure("mount_control_not_rejected")
    census_row = b'{"name":"foreign","driver":"local","scope":"local"}\n'
    require(set(volume_inventory(census_row)) == {"foreign"}, "volume_census_control_complete")
    for raw in (
        census_row[:-1],
        census_row + census_row,
        b'{"name":"foreign","driver":"local","scope":"unsupported"}\n',
        b'{"name":false,"driver":"local","scope":"local"}\n',
        b'{"name":"foreign","driver":"local","scope":"local","extra":false}\n',
    ):
        try:
            volume_inventory(raw)
        except Failure:
            pass
        else:
            raise Failure("volume_census_control_not_rejected")
    initial = {"foreign": {"name": "foreign", "driver": "local", "scope": "local"}}
    reference = {"cid": "c", "image": "i", "destination": "/source", "name": "owned", "driver": "local", "rw": True}
    expected = frozenset({("c", "/source")})
    obligation = {("c", "/source"): reference}
    references = {"owned": [reference]}
    require(
        volume_resource_counts(initial, initial, initial, expected, obligation, references)
        == resource_count(1, 1, 0, 0),
        "volume_projection_control_actual_join",
    )
    controls = (
        (None, initial, initial, expected, obligation, references),
        (initial, {}, initial, expected, obligation, references),
        (initial, initial, {**initial, "owned": {}}, expected, obligation, references),
        (initial, initial, initial, expected, {("c", "/source"): None}, references),
        (initial, initial, initial, expected, {}, references),
        (initial, initial, initial, expected, {}, {}),
        (initial, initial, initial, expected, obligation, {}),
        (initial, initial, initial, expected, obligation, {"owned": [reference, {**reference, "driver": "other"}]}),
        (initial, initial, initial, expected, {("c", "/source"): {**reference, "name": "foreign"}}, references),
        (initial, initial, None, expected, obligation, references),
    )
    for arguments in controls:
        try:
            volume_resource_counts(*arguments)
        except Failure:
            pass
        else:
            raise Failure("volume_projection_control_not_rejected")
    require(container_admission_gaps({"create": None}, {}) == 1, "container_gap_control_no_handle")
    require(container_admission_gaps({"create": "c"}, {}) == 1, "container_gap_control_missing_record")
    require(container_admission_gaps({"create": "c"}, {"actor": {"cid": "c"}}) == 0, "container_gap_control_join")
    require(
        set(volume_resource_counts(initial, initial, initial, expected, obligation, references))
        == {"admitted", "removed", "remaining", "unknown"},
        "volume_projection_no_historical_count",
    )
    fault = {
        "schema": "bifrost.test.workflow-commit-fault/v1",
        "case_id": "r-tx-lost-commit-response",
        "source_sha256": {"source_manifest": None, "rust_binary": None},
        "lanes": {actor: {key: None for key in LANE_FIELDS} for actor in ("python", "rust")},
        "passed": False,
    }
    producer = {
        "schema": "bifrost.test.workflow-commit-producer/v1",
        "source": {key: None for key in ("head", "tree", "main", "workspace")},
        "stage": None,
        "commands": [],
        "primary_exit": None,
        "complete": False,
    }
    disposal = {
        "schema": "bifrost.test.workflow-commit-disposal/v1",
        "primary_exit": None,
        "cleanup_exit": 1,
        "complete": False,
        "processes": {"started": 0, "settled": 0},
        "resources": {
            name: resource_count() for name in ("containers", "networks", "volumes", "images", "cargo", "files")
        },
        "private_captures": {"closed": None, "removed": None},
        "credentials_disposed": None,
    }
    for helper, original in ((validate_fault, fault), (validate_producer, producer), (validate_disposal, disposal)):
        require(helper(original, False) is original, "projection_control_actual_return")
        value = decode(encode(original, 16384), 16384)
        value["unexpected"] = "inert-private-control"
        rejected_projection(helper, value)
        value = decode(encode(original, 16384), 16384)
        value["passed" if helper is validate_fault else "complete"] = True
        rejected_projection(helper, value)
    value = decode(encode(fault, 16384), 16384)
    value["lanes"]["python"]["upstream_forwarded"] = False
    rejected_projection(validate_fault, value)
    value = decode(encode(fault, 16384), 16384)
    value["lanes"]["python"]["client_outcome"] = {
        "commit": "error",
        "error": {"family": "python_connection_lost", "code": "private-unreported"},
    }
    rejected_projection(validate_fault, value)
    value = decode(encode(fault, 16384), 16384)
    value["lanes"]["rust"]["server_status"] = "in progress"
    rejected_projection(validate_fault, value)
    value = decode(encode(disposal, 16384), 16384)
    value["resources"]["images"]["unknown"] = False
    rejected_projection(validate_disposal, value)
    command = {
        "label": "git.version",
        "settled": False,
        "native_exit": None,
        "stdout_eof": None,
        "stderr_eof": None,
        "group_settled": None,
        "fd_settled": None,
    }
    value = decode(encode(producer, 16384), 16384)
    value["commands"] = [command, dict(command)]
    rejected_projection(validate_producer, value)
    value = decode(encode(producer, 16384), 16384)
    value["commands"] = [dict(command) for _ in range(218)]
    rejected_projection(validate_producer, value)
    try:
        decode(b'{"complete":false,"complete":true}', 4096)
    except Failure:
        pass
    else:
        raise Failure("projection_duplicate_control_not_rejected")
    try:
        encode("X" * 16384, 16384)
    except Failure:
        pass
    else:
        raise Failure("projection_bound_control_not_rejected")


def rejected_projection(helper, value):
    try:
        helper(value, False)
    except Failure:
        return
    raise Failure("projection_control_not_rejected")


class ImageCustody:
    """Actual full initial IDs; a tag is only the first measurement selector."""

    def __init__(self, controller):
        self.controller = controller
        self.initial_ids = None
        self.initial_tags = None
        self.records = {}

    def initial(self, end, environment):
        require(self.initial_ids is None, "initial_images_repeated")
        native = self.controller.run("initial.images", end, environment)
        raw = self.controller.capture(native)
        require(len(raw) <= 16 * 1024 * 1024 and (not raw or raw.endswith(b"\n")), "initial_images_capture")
        lines = raw.splitlines()
        require(len(lines) <= 4096 and all(lines), "initial_images_cardinality")
        identities, tags = set(), {}
        seen = set()
        for line in lines:
            value = closed(decode(line, 8192), ("id", "repository", "tag"))
            require(type(value["id"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value["id"]), "initial_image_id")
            for name in ("repository", "tag"):
                require(
                    type(value[name]) is str
                    and value[name]
                    and len(value[name].encode("utf-8")) <= 1024
                    and not any(ord(character) < 32 for character in value[name]),
                    "initial_image_tag_encoding",
                )
            association = (value["id"], value["repository"], value["tag"])
            require(association not in seen, "initial_image_duplicate_record")
            seen.add(association)
            identities.add(value["id"])
            key = (value["repository"], value["tag"])
            if key != ("<none>", "<none>"):
                require(
                    "<none>" not in key and (key not in tags or tags[key] == value["id"]), "initial_image_tag_conflict"
                )
                tags[key] = value["id"]
        for name in ("configured_api_tag", "fixed_native_toolchain_tag"):
            repository, separator, tag = self.controller.values[name].rpartition(":")
            require(
                separator == ":" and repository and tag and (repository, tag) not in tags,
                "initial_selected_tag_present",
            )
        self.initial_ids, self.initial_tags = identities, tags

    def acquire(self, purpose, label, end, environment):
        require(
            purpose in ("api", "toolchain") and purpose not in self.records and self.initial_ids is not None,
            "image_acquisition_state",
        )
        controller = self.controller
        native = controller.run(label, end, environment)
        objects = decode(controller.capture(native), 16 * 1024 * 1024)
        require(type(objects) is list and len(objects) == 1, "image_first_measurement_count")
        image = image_object(objects[0])
        record = {
            "purpose": purpose,
            "id": image["Id"],
            "first": image,
            "qualified": False,
            "removed": False,
            "absent": False,
            "unknown": False,
        }
        self.records[purpose] = record
        controller.values["owned_" + purpose + "_id"] = image["Id"]
        require(image["Id"] not in self.initial_ids, "image_preexisting_id")
        tag = controller.values["configured_api_tag" if purpose == "api" else "owned_toolchain_tag"]
        require(type(image.get("RepoTags")) is list and tag in image["RepoTags"], "image_first_tag_join")
        require(
            getattr(controller, "disposable_daemon_admitted", False) is True, "image_exclusive_acquisition_unverified"
        )
        source_disk_match(Path(controller.values["candidate_root"]), controller.sources)
        source_copy_membership(Path(controller.values["candidate_root"]), controller.git_roster)
        if purpose == "api":
            require(
                getattr(controller, "api_build", None) is not None
                and controller.api_build.returncode == 0
                and controller.api_build.settled is True,
                "api_source_build_incomplete",
            )
            controller.api_inherited_environment = image_environment(image["Config"].get("Env"))
            require(
                image["Config"].get("WorkingDir") == "/app" and image["Config"].get("Entrypoint") == ["/entrypoint.sh"],
                "api_image_profile",
            )
            controller.values["qualified_api_id"] = image["Id"]
        else:
            require(
                getattr(controller, "toolchain_build", None) is not None
                and controller.toolchain_build.returncode == 0
                and controller.toolchain_build.settled is True,
                "toolchain_source_build_incomplete",
            )
            require(
                image["Config"].get("WorkingDir") == "/workspace/core-rs" and image["Config"].get("User") == "",
                "toolchain_image_profile",
            )
            controller.values["qualified_toolchain_id"] = image["Id"]
        record["qualified"] = True
        return record

    def joint_stack_metadata(self, end, environment):
        controller = self.controller
        require(
            self.records["api"]["qualified"] is True and getattr(controller, "stack_current", None) is not None,
            "joint_image_metadata_order",
        )
        native = controller.run("image.api.after_use", end, environment)
        values = decode(controller.capture(native), 16 * 1024 * 1024)
        require(type(values) is list and len(values) == 2, "joint_image_metadata_count")
        api, pgbouncer = (image_object(value) for value in values)
        require(
            api["Id"] == self.records["api"]["id"]
            and pgbouncer["Id"] == controller.values["actual_pgbouncer_image_id"]
            and api["Id"] != pgbouncer["Id"],
            "joint_image_role_mapping",
        )
        require(api == self.records["api"]["first"], "api_image_metadata_drift")
        repo_digests = pgbouncer.get("RepoDigests")
        require(
            type(repo_digests) is list
            and len(repo_digests) <= 16
            and len(repo_digests) == len(set(repo_digests))
            and all(type(value) is str for value in repo_digests),
            "frontend_repo_digest_types",
        )
        named = [value for value in repo_digests if re.fullmatch(r"edoburu/pgbouncer@sha256:[0-9a-f]{64}", value)]
        require(len(named) == 1, "frontend_publisher_repository")
        require(pgbouncer["Config"].get("Entrypoint") == ["/entrypoint.sh"], "frontend_image_entrypoint")
        controller.pgbouncer_image = pgbouncer
        controller.pgbouncer_publisher_digest = named[0]
        controller.frontend_image_admitted = True

    def remove(self, record, end, environment):
        controller = self.controller
        require(
            record["qualified"] is True
            and record["unknown"] is False
            and record["id"] not in self.initial_ids
            and record["removed"] is False,
            "image_removal_unowned",
        )
        controller.run("image." + record["purpose"] + ".remove", end, environment, cleanup=True)
        record["removed"] = True
        native = controller.run("image." + record["purpose"] + ".absent", end, environment, allowed=(1,), cleanup=True)
        require(
            controller.capture(native) in (b"", b"[]\n")
            and controller.capture(native, 1)
            == ("Error response from daemon: No such image: " + record["id"] + "\n").encode("ascii"),
            "image_absence_unknown",
        )
        record["absent"] = True


def image_object(value):
    require(
        type(value) is dict
        and type(value.get("Id")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["Id"])
        and value.get("Os") == "linux"
        and value.get("Architecture") == "amd64"
        and type(value.get("Config")) is dict,
        "image_full_profile",
    )
    return value


class LogTreeCustody:
    """Source-created tree is never silently excluded from overall disposal."""

    def __init__(self, path):
        self.path = path
        self.identity = None
        self.initial_owner = None
        self.removed = False
        self.unknown = True
        self.before_marker_absent = False
        self.marker_observed = False
        self.records = []
        self.end = None

    def create(self):
        require(not os.path.lexists(self.path), "log_tree_preexisting")
        os.mkdir(self.path, 0o700)
        before = os.lstat(self.path)
        self.identity = (before.st_dev, before.st_ino)
        self.initial_owner = (before.st_uid, before.st_gid)
        require(
            stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o700,
            "log_tree_acquisition",
        )
        self.unknown = False

    def directory(self):
        require(self.identity is not None and not self.removed, "log_tree_unowned")
        current = os.lstat(self.path)
        require(
            stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == self.identity
            and (current.st_uid, current.st_gid) in (self.initial_owner, (1000, 1000)),
            "log_tree_source_transition",
        )
        return current

    def before_target(self):
        self.directory()
        require(
            not self.before_marker_absent and not os.path.lexists(self.path / ".clean-boot-consumed"),
            "target_clean_boot_marker_present",
        )
        self.before_marker_absent = True

    def after_target(self, expected_mode):
        require(self.before_marker_absent and not self.marker_observed, "target_clean_boot_marker_order")
        directory = self.directory()
        require(stat.S_IMODE(directory.st_mode) == 0o777, "target_log_source_mode")
        marker = os.lstat(self.path / ".clean-boot-consumed")
        require(
            stat.S_ISREG(marker.st_mode)
            and marker.st_nlink == 1
            and marker.st_size == 0
            and stat.S_IMODE(marker.st_mode) == expected_mode
            and (marker.st_uid, marker.st_gid) == (1000, 1000),
            "target_clean_boot_marker_source",
        )
        self.marker_observed = True

    def snapshot(self):
        self.directory()
        records = []
        directories = [self.path]
        seen = set()
        while directories:
            require(type(self.end) is float and time.monotonic() < self.end, "tree_snapshot_deadline")
            parent = directories.pop()
            require(len(parent.parts) - len(self.path.parts) <= 64, "tree_snapshot_depth")
            parent_before = os.lstat(parent)
            require(stat.S_ISDIR(parent_before.st_mode), "log_tree_parent_type")
            descriptor = None
            original = children = None
            try:
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                require(file_identity(os.fstat(descriptor)) == file_identity(parent_before), "log_tree_parent_identity")
                with os.scandir(descriptor) as entries:
                    children = [(entry.name, entry.inode()) for entry in entries]
                require(
                    file_identity(os.fstat(descriptor)) == file_identity(parent_before)
                    and file_identity(os.lstat(parent)) == file_identity(parent_before),
                    "log_tree_parent_stable",
                )
            except BaseException as error:
                original = error
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except BaseException as error:
                        original = first_error(original, error)
            if original is not None:
                raise original
            require(len(records) + len(children) <= 4096, "log_tree_member_bound")
            for name, inode in children:
                require(
                    type(name) is str and name not in (".", "..") and "/" not in name and "\0" not in name,
                    "log_tree_child_name",
                )
                path = parent / name
                before = os.lstat(path)
                require(before.st_ino == inode, "log_tree_child_inode")
                require(
                    before.st_uid in (self.initial_owner[0], 1000)
                    and before.st_gid in (self.initial_owner[1], 1000)
                    and (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode))
                    and (not stat.S_ISREG(before.st_mode) or before.st_nlink == 1),
                    "log_tree_member_profile",
                )
                identity = (before.st_dev, before.st_ino)
                require(identity not in seen and before.st_dev == self.identity[0], "log_tree_member_identity")
                seen.add(identity)
                record = {
                    "path": path,
                    "identity": file_identity(before),
                    "directory": stat.S_ISDIR(before.st_mode),
                    "removed": False,
                }
                records.append(record)
                if record["directory"]:
                    directories.append(path)
        self.records = records
        return records

    def remove(self, controller):
        original = None
        try:
            self.snapshot()
        except BaseException as error:
            self.unknown = True
            controller.fail(error, cleanup=True)
            return
        for record in sorted(self.records, key=lambda item: len(item["path"].parts), reverse=True):
            try:
                require(type(self.end) is float and time.monotonic() < self.end, "tree_removal_deadline")
                observed = os.lstat(record["path"])
                actual = file_identity(observed)
                if record["directory"]:
                    # Removing our already censused children legitimately changes
                    # a directory's nlink/size/mtime/ctime. Its original inode,
                    # mode and owner remain mandatory, and rmdir must find empty.
                    require(
                        stat.S_ISDIR(observed.st_mode) and actual[:5] == record["identity"][:5],
                        "log_tree_directory_replaced",
                    )
                else:
                    require(actual == record["identity"], "log_tree_member_replaced")
                if record["directory"]:
                    os.rmdir(record["path"])
                else:
                    os.unlink(record["path"])
                require(not os.path.lexists(record["path"]), "log_tree_member_remains")
                record["removed"] = True
            except BaseException as error:
                original = first_error(original, error)
                controller.fail(error, cleanup=True)
        try:
            require(type(self.end) is float and time.monotonic() < self.end, "tree_root_deadline")
            self.directory()
            os.rmdir(self.path)
            require(not os.path.lexists(self.path), "log_tree_root_remains")
            self.removed = True
        except BaseException as error:
            original = first_error(original, error)
            controller.fail(error, cleanup=True)
        self.unknown = original is not None


def source_umask():
    # procfs reports zero st_size; it is not an ordinary immutable software file.
    descriptor = None
    original = result = None
    try:
        descriptor = os.open("/proc/self/status", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), "parent_status_type")
        chunks = bytearray()
        while len(chunks) <= 65536:
            raw = os.read(descriptor, min(4096, 65537 - len(chunks)))
            if not raw:
                break
            chunks.extend(raw)
        require(0 < len(chunks) <= 65536, "parent_status_bound")
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode),
            "parent_status_identity",
        )
        matches = [line for line in bytes(chunks).splitlines() if line.startswith(b"Umask:")]
        require(
            len(matches) == 1 and re.fullmatch(rb"Umask:\s+[0-7]{4}", matches[0]), "parent_source_umask_unavailable"
        )
        result = int(matches[0].split()[1], 8)
    except BaseException as error:
        original = error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                original = first_error(original, error)
    if original is not None:
        raise original
    return result


def daemon_profile(controller, end, environment):
    docker = controller.run("tool.docker.version", end, environment)
    versions = decode(controller.capture(docker), 65536)
    require(
        type(versions) is dict and type(versions.get("Client")) is dict and type(versions.get("Server")) is dict,
        "docker_version_profile",
    )
    for name in ("Client", "Server"):
        require(
            type(versions[name].get("Version")) is str
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.]+)?", versions[name]["Version"]),
            "docker_version_syntax",
        )
    compose = native_text(controller, controller.run("tool.compose.version", end, environment), 256, ascii_only=True)
    require(re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.]+)?\n", compose), "compose_version_syntax")
    context = decode(controller.capture(controller.run("profile.context", end, environment)), 65536)
    require(
        type(context) is list
        and len(context) == 1
        and type(context[0]) is dict
        and context[0].get("Name") == "default",
        "docker_context_profile",
    )
    endpoints = context[0].get("Endpoints")
    require(
        type(endpoints) is dict
        and type(endpoints.get("docker")) is dict
        and endpoints["docker"].get("Host") == "unix:///var/run/docker.sock"
        and endpoints["docker"].get("SkipTLSVerify") is False,
        "docker_local_endpoint",
    )
    daemon = decode(controller.capture(controller.run("profile.daemon", end, environment)), 1024 * 1024)
    require(
        type(daemon) is dict
        and daemon.get("OSType") == "linux"
        and daemon.get("Architecture") in ("x86_64", "amd64")
        and daemon.get("Driver") == "overlay2"
        and daemon.get("DefaultRuntime") == "runc",
        "docker_daemon_profile",
    )
    security = daemon.get("SecurityOptions")
    require(
        type(security) is list
        and all(type(value) is str for value in security)
        and not any("rootless" in value or "userns" in value for value in security)
        and all(value in ("name=seccomp,profile=builtin", "name=cgroupns") for value in security),
        "docker_same_user_namespace",
    )
    require(
        environment.get("GITHUB_ACTIONS") == "true"
        and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and environment.get("GITHUB_REPOSITORY") == "Midtown-Technology-Group/bifrost"
        and environment.get("GITHUB_SHA") == controller.source["head"],
        "trusted_disposable_job_profile",
    )
    # This is the explicitly selected conventional supported-job trust boundary,
    # not authentication of arbitrary stdout or proof against privileged peers.
    controller.disposable_daemon_admitted = True
    controller.docker_profile = {"versions": versions, "compose": compose, "context": context, "daemon": daemon}


def workspace_source(controller, end, environment):
    controller.run("workspace.init", end, environment)
    controller.run("workspace.fetch", end, environment)
    head = git_identity(controller, "workspace.main", end, environment)
    require(head == APPROVED_WORKSPACE_HEAD, "workspace_boundary_changed")
    controller.source["workspace"] = head
    roster = tree_roster(controller.capture(controller.run("workspace.tree_roster", end, environment)))
    paths = tuple(sorted(WORKSPACE_REFERENCE))
    result = controller.run("workspace.object_read", end, environment, stdin=git_batch_request(roster, paths))
    observed = git_batch_response(controller.capture(result), roster, paths)
    for path in paths:
        expected = WORKSPACE_REFERENCE[path]
        require(
            observed[path]["oid"] == expected["oid"]
            and observed[path]["sha256"] == expected["sha256"]
            and len(observed[path]["bytes"]) == expected["bytes"],
            "workspace_source_boundary",
        )
    controller.workspace_sources = observed


def toolchain_versions(controller, end, environment):
    purpose = "toolchain.version"
    cid = created_cid(controller, purpose + ".create", end, environment)
    record = controller.custody.acquire(purpose, cid)
    # The frozen placeholder spells this purpose with underscores.
    controller.values["tool_version_cid"] = cid
    observed = inspect_native(controller, purpose + ".qualified", end, environment, cid)
    native_container_config(
        controller,
        observed,
        "tool-version",
        controller.values["owned_toolchain_id"],
        "none",
        "",
        entrypoint=["/bin/sh"],
        command=["-c", "set -e; rustc -vV; cargo -V; rustfmt --version; cargo clippy --version"],
    )
    controller.custody.admit_created(record, observed)
    record["native"] = controller.run(purpose + ".start", end, environment)
    raw = controller.capture(record["native"])
    text = raw.decode("ascii", "strict")
    require(
        len(raw) <= 16384
        and text.endswith("\n")
        and "\r" not in text
        and "rustc 1.98.1 " in text
        and "release: 1.98.1\n" in text
        and re.search(r"(?m)^cargo 1\.98\.1 [^\n]+$", text)
        and re.search(r"(?m)^rustfmt [^\n]+$", text)
        and re.search(r"(?m)^clippy 0\.1\.98 [^\n]+$", text),
        "toolchain_measured_versions",
    )
    controller.toolchain_versions = text
    terminal = inspect_native(controller, purpose + ".terminal", end, environment, cid)
    controller.custody.terminal(record, terminal, 0)
    controller.custody.remove(record, end, environment)


def schema_native(controller, purpose, end, environment):
    require(purpose in ("schema_before", "schema_after"), "schema_venue")
    cid = created_cid(controller, purpose + ".create", end, environment)
    record = controller.custody.acquire(purpose, cid)
    observed = inspect_native(controller, purpose + ".qualified", end, environment, cid)
    native_container_config(
        controller,
        observed,
        purpose,
        controller.values["qualified_api_id"],
        controller.values["owned_original_network_id"],
        controller.values["actual_host_uid"] + ":" + controller.values["actual_host_gid"],
        command=["observe-schema"],
        entrypoint=["/f4/bin/workflow_sql_vectors"],
    )
    mount_association(observed, controller.values["qualified_binary"], "/f4/bin/workflow_sql_vectors", True)
    env = environment_map(observed["Config"].get("Env"))
    require(env.get("BIFROST_RUST_TEST_DATABASE_URL") == controller.rust_dsn, "schema_original_constructor_environment")
    controller.custody.admit_created(record, observed)
    before = purpose == "schema_before"
    request = {
        "schema": "bifrost.test.workflow-schema-observation/v1",
        "case_id": "schemaBefore" if before else "schemaAfter",
        "phase": "before_target" if before else "after_native",
    }
    native = controller.run(purpose + ".start", end, environment, stdin=encode(request, 4096), limit=(4096, 65536))
    record["native"] = native
    require(controller.capture(native, 1) == b"", "schema_private_error_boundary")
    result = schema_observation(controller.capture(native), request["phase"], controller.heads)
    terminal = inspect_native(controller, purpose + ".terminal", end, environment, cid)
    controller.custody.terminal(record, terminal, 0)
    controller.custody.remove(record, end, environment)
    return result


def cold_and_release(controller, environment):
    end = controller.stage_end("cold", 480)
    controller.toolchain_build = controller.run("native.toolchain.build", end, environment)
    controller.image_custody.acquire("toolchain", "prerequisite.images.inspect", end, environment)
    toolchain_versions(controller, end, environment)
    controller.volume_custody.acquire("cargo", end, environment)
    controller.volume_custody.acquire("target", end, environment)
    measured = controller.run("image.toolchain.before_use", end, environment)
    value = decode(controller.capture(measured), 16 * 1024 * 1024)
    require(
        type(value) is list
        and len(value) == 1
        and image_object(value[0]) == controller.image_custody.records["toolchain"]["first"],
        "toolchain_before_use_drift",
    )
    for purpose in ("fetch", "fmt", "clippy", "default_tests"):
        record = cargo_container(controller, controller.custody, purpose, end, environment)
        controller.custody.remove(record, end, environment)
    end = controller.stage_end("release", 120)
    record = cargo_container(controller, controller.custody, "release", end, environment)
    path = Path(controller.values["owned_binary_path"])
    require(not os.path.lexists(path), "release_binary_preexisting")
    controller.run("release.copy", end, environment)
    entry = {
        "path": path,
        "kind": "file",
        "identity": None,
        "descriptor": None,
        "close_attempted": True,
        "closed": True,
        "removed": False,
    }
    controller.files.entries.append(entry)
    actual = os.lstat(path)
    require(
        stat.S_ISREG(actual.st_mode)
        and actual.st_uid == os.getuid()
        and actual.st_nlink == 1
        and stat.S_IMODE(actual.st_mode) & 0o111
        and 0 < actual.st_size <= BINARY_LIMIT,
        "release_binary_acquisition",
    )
    entry["identity"] = (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid)
    raw = read_software(path, BINARY_LIMIT)
    controller.binary_source = {"bytes": len(raw), "sha256": software_sha(raw)}
    controller.values["qualified_binary"] = str(path)
    controller.custody.remove(record, end, environment)


def source_setup(controller, environment):
    end = controller.stage_end("source", 60)
    root = Path(controller.values["candidate_root"])
    source_preflight(controller, environment, end)
    workspace_source(controller, end, environment)
    daemon_profile(controller, end, environment)
    project_inventory(controller, "initial", end, environment)
    initial = controller.run("initial.inventory", end, environment)
    require(json_lines(controller.capture(initial), 128) == [], "initial_project_inventory")
    original = controller.run("compose.base", end, environment)
    model = decode(controller.capture(original), 16 * 1024 * 1024)
    require(
        type(model) is dict
        and type(model.get("services")) is dict
        and all(name in model["services"] for name in (*STACK_SERVICES, "test-runner")),
        "compose_service_membership",
    )
    initial_volumes = controller.initial_volume_names
    configured_volumes = model.get("volumes", {})
    require(type(configured_volumes) is dict, "initial_source_volume_model")
    for value in configured_volumes.values():
        require(type(value) is dict and type(value.get("name")) is str, "initial_source_volume_name")
        require(value["name"] not in initial_volumes, "initial_source_volume_present")
    api = model["services"]["api"]
    require(type(api) is dict and api.get("image") == "bifrost-test-api-dev:latest", "configured_api_source_tag")
    controller.values["configured_api_tag"] = api["image"]
    controller.original_model = model
    controller.supplied_environment = selected_environment(model)
    original_url = controller.supplied_environment["BIFROST_DATABASE_URL"]
    require(
        original_url == "postgresql+asyncpg://bifrost:bifrost_test@pgbouncer:5432/bifrost_test", "selected_original_url"
    )
    controller.values["original_hostname"] = "pgbouncer"
    controller.rust_dsn = "postgresql://" + original_url[len("postgresql+asyncpg://") :]
    controller.image_custody.initial(end, environment)
    for path in (root / ".env.test", root.parent / ".env.test"):
        require(not os.path.lexists(path), "unadmitted_test_environment_file")
    # The actual common-dir source fallback is separately rejected; it is not
    # read for secrets or silently replaced with the selected constructor URL.
    common = root / ".git"
    if common.is_file():
        raw = read_software(common, 4096).decode("utf-8", "strict")
        require(raw.startswith("gitdir: ") and raw.endswith("\n") and raw.count("\n") == 1, "git_worktree_pointer")
        actual = Path(raw[8:-1]).resolve(strict=True)
        commondir = read_software(actual / "commondir", 4096).decode("utf-8", "strict")
        require(commondir.endswith("\n") and commondir.count("\n") == 1, "git_common_pointer")
        primary = (actual / commondir[:-1]).resolve(strict=True).parent
        require(not os.path.lexists(primary / ".env.test"), "unadmitted_primary_environment_file")
    controller.marker_mode = 0o666 & ~source_umask()
    projection_controls()
    end = controller.stage_end("prepr", 480)
    controller.prepr_native = controller.run("prepr", end, environment)
    source_disk_match(root, controller.sources)
    source_copy_membership(root, controller.git_roster)
    project_inventory(controller, "prepr_final", end, environment)


def target_stack(controller, environment):
    end = controller.stage_end("stack", 180)
    controller.api_build = controller.run("api.build", end, environment)
    controller.image_custody.acquire("api", "image.api.before_use", end, environment)
    for role in ("observer", "relay", "python", "rust"):
        path = Path(controller.values[role + "_socket"])
        controller.listener(role, path)
    original = controller.original_model
    resolved = decode(encode(original, 16 * 1024 * 1024), 16 * 1024 * 1024)
    runner = resolved["services"]["test-runner"]
    mounts = runner.get("volumes")
    require(
        type(mounts) is list
        and not any(type(value) is dict and value.get("target") == "/run/f4-observer.sock" for value in mounts),
        "observer_bind_preexisting",
    )
    added = {
        "type": "bind",
        "source": controller.values["observer_socket"],
        "target": "/run/f4-observer.sock",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    mounts.append(added)
    controller.files.file(Path(controller.values["owned_resolved_json"]), encode(resolved, 16 * 1024 * 1024))
    native = controller.run("compose.resolved", end, environment)
    reread = decode(controller.capture(native), 16 * 1024 * 1024)
    require(reread == resolved, "resolved_compose_rewrite")
    inverse = decode(encode(reread, 16 * 1024 * 1024), 16 * 1024 * 1024)
    require(
        inverse["services"]["test-runner"]["volumes"].pop() == added and inverse == original, "observer_bind_inverse"
    )
    controller.target_environment = dict(environment)
    controller.target_environment["COMPOSE_FILE"] = controller.values["owned_resolved_json"]
    controller.target_environment["BIFROST_SKIP_BUILD"] = "1"
    controller.stack_up_native = controller.run("stack.up", end, controller.target_environment)
    discovered = stack_discovery(controller, end, controller.target_environment)
    stack_source_join(controller, discovered, reread)
    for service, observed in discovered.items():
        retain_volume_mounts(controller, observed, reread["services"][service], reread)
    controller.project_resource_admitted = {"networks": 1}
    native = controller.run("network.inspect", end, controller.target_environment)
    network_admission(controller, decode(controller.capture(native), 256 * 1024))
    controller.image_custody.joint_stack_metadata(end, controller.target_environment)
    frontend_source_profile(controller, end, controller.target_environment)
    write_actor_environment(controller, controller.supplied_environment, controller.private_directory)
    controller.schema_before = schema_native(controller, "schema_before", end, controller.target_environment)


def runner_and_fault(controller):
    environment = controller.target_environment
    controller.log_tree.before_target()
    # C0 is measured before the one genuine runner Popen acquisition. It is
    # never moved to a later socket handshake or container discovery.
    controller.case_start = time.monotonic()
    controller.work_end = min(controller.case_start + 75, controller.normal_end)
    controller.case_end = min(controller.case_start + 90, controller.normal_end)
    controller.target_end = min(controller.case_start + 120, controller.normal_end)
    runner_environment = dict(environment)
    runner_environment["BIFROST_TEST_USE_CLEAN_BOOT"] = "1"
    controller.runner_native = controller.launch(
        "runner.start", controller.target_end, runner_environment, cleanup_end=controller.whole_end
    )
    observer = controller.await_peer("observer", min(controller.case_start + 60, controller.work_end))
    native = controller.run("runner.discover", controller.work_end, environment)
    rows = json_lines(controller.capture(native), 16)
    require(
        len(rows) == 1 and type(rows[0].get("ID")) is str and re.fullmatch(r"[0-9a-f]{12,64}", rows[0]["ID"]),
        "runner_discovery_identity",
    )
    # Inspect resolves a short ps ID to the full current immutable ID; no
    # desired name is substituted for a missing actual runner.
    controller.values["runner_cid"] = rows[0]["ID"]
    observed = controller.run("runner.inspect", controller.work_end, environment)
    objects = decode(controller.capture(observed), 16 * 1024 * 1024)
    require(type(objects) is list and len(objects) == 1, "runner_full_inspection")
    current = objects[0]
    full_id = current.get("Id") if type(current) is dict else None
    require(
        type(full_id) is str
        and re.fullmatch(r"[0-9a-f]{64}", full_id)
        and full_id.startswith(controller.values["runner_cid"]),
        "runner_full_id",
    )
    current = container_object(objects, full_id)
    live_container(current)
    controller.values["runner_cid"] = full_id
    controller.runner_current = current
    controller.runner_discovered = True
    labels = current["Config"].get("Labels")
    require(
        type(labels) is dict
        and labels.get("com.docker.compose.project") == controller.values["owned_project"]
        and labels.get("com.docker.compose.service") == "test-runner"
        and labels.get("com.docker.compose.oneoff") == "True"
        and current["Image"] == controller.values["qualified_api_id"],
        "runner_source_identity",
    )
    for mount in controller.stack_model["services"]["test-runner"]["volumes"]:
        if mount.get("type") == "bind":
            mount_association(current, mount["source"], mount["target"], mount.get("read_only", False))
    retain_volume_mounts(controller, current, controller.stack_model["services"]["test-runner"], controller.stack_model)
    controller.target_mount_observation_complete = True
    runner_environment_observed = environment_map(current["Config"].get("Env"))
    runner_environment_expected = dict(controller.api_inherited_environment)
    runner_environment_expected.update({key: controller.supplied_environment[key] for key in COMPOSE_ENVIRONMENT})
    require(runner_environment_observed == runner_environment_expected, "runner_compose_image_environment")
    native = controller.run("runner.top", controller.work_end, environment, limit=(16384, 65536))
    host_row = process_peer_top(controller.capture(native), observer, 1000, 1000)
    direct_pid1_peer(current["State"]["Pid"], host_row, observer.credentials)
    native = controller.run("runner.source", controller.work_end, environment, limit=(8193, 65536))
    reader = source_readback(controller, "observer", native, 1000, 1000)
    controller.peer_sources["observer"] = {"container": current, "reader": reader, "host_row": host_row}
    remaining = int((controller.work_end - time.monotonic()) * 1000)
    integer(remaining, 1, 75000)
    controller.send(observer, "bootstrap", {"remaining_ms": remaining}, controller.work_end)
    controller.receive(observer, "hello", (), controller.work_end)
    remaining = int((controller.work_end - time.monotonic()) * 1000)
    integer(remaining, 1, 75000)
    controller.send(observer, "admit", {"remaining_ms": remaining}, controller.work_end)
    constructed = controller.receive(
        observer,
        "constructor_ready",
        (
            "schema",
            "role",
            "env_count",
            "env_source_equal",
            "source_calls",
            "endpoint",
            "engine",
            "factory",
            "connection",
            "restoration",
        ),
        controller.work_end,
    )
    hostname = controller.values["original_hostname"]
    constructor(constructed, "observer", hostname, 5432)
    controller.values["actual_observer_constructor_hostname"] = constructed["endpoint"]["hostname"]
    frontend_bracket(controller, "before", controller.work_end, environment)
    controller.send(observer, "constructor_accept", {"role": "observer"}, controller.work_end)
    ready = controller.receive(
        observer,
        "connection_ready",
        (
            "schema",
            "role",
            "engine_join",
            "preconnect_match",
            "record_join",
            "checkout_join",
            "selected_connection_join",
            "frontend_join",
            "negotiated_tls",
        ),
        controller.work_end,
    )
    connection_ready(ready, "observer")
    controller.send(observer, "connection_accept", {"role": "observer"}, controller.work_end)
    seeded = controller.receive(
        observer, "seeded", ("cycle", "actor", "scope", "request"), controller.work_end, data=True
    )
    record = actor_created(controller, "relay", controller.work_end, environment)
    relay_native = controller.launch(
        "relay.start", controller.case_end, environment, limit=65536, cleanup_end=controller.case_end
    )
    record["native"] = relay_native
    actor_live_admission(controller, "relay", record, controller.work_end, environment)
    relay = actor_peer_admission(controller, "relay", record, controller.work_end, environment)
    remaining = int((controller.work_end - time.monotonic()) * 1000)
    integer(remaining, 1, 75000)
    controller.send(
        relay, "bootstrap", {"hostname": hostname, "port": 5432, "remaining_ms": remaining}, controller.work_end
    )
    controller.receive(relay, "hello", (), controller.work_end)
    controller.send(relay, "hello_accept", {}, controller.work_end)
    mutation_cycle(controller, 1, "python", seeded, observer, relay, environment)
    controller.send(relay, "advance", {"cycle": 1, "actor": "python", "connection": 1}, controller.work_end)
    controller.receive(relay, "advanced", ("cycle", "actor", "connection"), controller.work_end)
    controller.send(observer, "advance", {"cycle": 1, "actor": "python"}, controller.work_end)
    seeded = controller.receive(
        observer, "seeded", ("cycle", "actor", "scope", "request"), controller.work_end, data=True
    )
    mutation_cycle(controller, 2, "rust", seeded, observer, relay, environment)
    controller.wait(relay_native)
    require(
        relay_native.returncode == 0
        and controller.capture(relay_native) == b""
        and controller.capture(relay_native, 1) == b"",
        "relay_native_settlement",
    )
    terminal = inspect_native(controller, "relay.terminal", controller.case_end, environment, record["cid"])
    controller.custody.terminal(record, terminal, 0)
    controller.custody.remove(record, controller.case_end, environment)
    frontend_bracket(controller, "after", controller.work_end, environment)
    controller.send(observer, "finish_permit", {"cycle": 2, "actor": "rust"}, controller.work_end)
    finished = controller.receive(observer, "finished", ("source_closed",), controller.case_end)
    require(finished["source_closed"] is True, "observer_source_closure")
    controller.observer_source_closed = True
    for cycle in controller.cycles:
        cycle.source_closed = True
    controller.wait(controller.runner_native)
    require(controller.runner_native.returncode == 0, "runner_native_status")
    junit = read_software(controller.log_tree.path / "test-results.xml", 1024 * 1024)
    private_junit(junit)
    controller.junit_admitted = True
    controller.log_tree.after_target(controller.marker_mode)


def teardown_normal(controller):
    environment = controller.target_environment
    end = controller.stage_end("teardown", 120)
    controller.teardown_end = end
    controller.schema_after = schema_native(controller, "schema_after", end, environment)
    schema_pair(controller.schema_before, controller.schema_after)
    controller.schema_matched = True
    controller.stack_down_native = controller.run("stack.down", end, environment)
    project_inventory(controller, "target_final", end, environment, kinds=("containers", "networks"))
    controller.project_disposed = True
    measured = controller.run("image.toolchain.after_use", end, environment)
    images = decode(controller.capture(measured), 16 * 1024 * 1024)
    require(
        type(images) is list
        and len(images) == 1
        and image_object(images[0]) == controller.image_custody.records["toolchain"]["first"],
        "toolchain_after_use_drift",
    )
    for record in controller.volume_custody.records.values():
        controller.volume_custody.remove(record, end, environment)
    source_final_bracket(controller, environment, end)
    controller.source_final_admitted = True


def cleanup_native_container(controller, record, end, environment):
    purpose = record["purpose"]
    if record["absent"]:
        return
    require(record["admitted"] is True and record["created"] is not None, "failure_container_unverified")
    current = inspect_native(controller, purpose + ".prekill", end, environment, record["cid"], cleanup=True)
    container_stable(record["created"], current)
    state = current["State"]
    if state.get("Running") is True:
        live_container(current)
        controller.run(purpose + ".kill", end, environment, cleanup=True)
        current = inspect_native(controller, purpose + ".postkill", end, environment, record["cid"], cleanup=True)
        container_stable(record["created"], current)
    require(
        current["State"].get("Running") is False
        and current["State"].get("Status") == "exited"
        and type(current["State"].get("ExitCode")) is int,
        "failure_container_terminal_unknown",
    )
    expected = integer(current["State"]["ExitCode"], 0, 255)
    controller.custody.terminal(record, current, expected)
    controller.custody.remove(record, end, environment, cleanup=True)


def close_owned_ipc(record, selector, *, listener):
    # Independent unregister and close; uncertain close is never retried.
    if record["close_attempted"]:
        return
    record["close_attempted"] = True
    connection = record["socket"] if listener else record["connection"]
    original = None
    try:
        if selector is not None and (not listener or record["registered"]):
            try:
                selector.unregister(connection)
            except KeyError:
                if listener:
                    raise
            else:
                if listener:
                    record["registered"] = False
    except BaseException as error:
        original = first_error(original, error)
    try:
        if not listener and record["peer"] is not None:
            record["peer"].close()
        else:
            connection.close()
        require(connection.fileno() == -1, "owned_ipc_close_unknown")
        record["closed"] = True
    except BaseException as error:
        original = first_error(original, error)
    if original is not None:
        raise original


def cleanup_all(controller, environment):
    # The emergency boundary is latched once at the first actual failure and
    # clipped by the original whole_end. No operation, actor or
    # source-close error gets a renewed success deadline. Every independent
    # cleanup still runs when an earlier cleanup operation failed.
    end = min(
        controller.emergency_end
        if controller.emergency_end is not None
        else getattr(controller, "teardown_end", controller.whole_end),
        controller.whole_end,
    )
    for native in controller.native:
        try:
            if native.process is not None and not native.group_settled and not native.kill_attempted:
                require(time.monotonic() < end, "emergency_group_deadline")
                if group_present(native.pgid):
                    native.kill_attempted = True
                    os.killpg(native.pgid, signal.SIGKILL)
            controller.settle(native)
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if hasattr(controller, "custody"):
        for record in controller.custody.records.values():
            if record["absent"]:
                continue
            try:
                cleanup_native_container(controller, record, end, environment)
            except BaseException as error:
                record["unknown"] = True
                controller.fail(error, cleanup=True)
    runner = getattr(controller, "runner_current", None)
    if runner is not None and not getattr(controller, "project_disposed", False):
        try:
            current = inspect_native(
                controller, "runner.failure.inspect", end, environment, controller.values["runner_cid"], cleanup=True
            )
            container_stable(runner, current)
            if current["State"].get("Running") is True:
                live_container(current)
                controller.run("runner.failure.kill", end, environment, cleanup=True)
            controller.runner_failure_guard = True
        except BaseException as error:
            controller.runner_failure_guard = False
            controller.fail(error, cleanup=True)
    # A failed actual object guard cannot be bypassed by whole-project down.
    records = list(getattr(controller, "custody", ContainerCustody(controller)).records.values())
    actor_guard = all(record["absent"] for record in records if record["purpose"] in ("relay", "python", "rust"))
    runner_guard = runner is None or getattr(controller, "runner_failure_guard", False)
    if "stack.down" not in controller.used and hasattr(controller, "original_model"):
        try:
            require(actor_guard and runner_guard, "failure_source_down_guard")
            controller.run("stack.down", end, environment, cleanup=True)
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if "inventory.target_final.containers" not in controller.used and hasattr(controller, "original_model"):
        try:
            project_inventory(
                controller, "target_final", end, environment, cleanup=True, kinds=("containers", "networks")
            )
            controller.project_disposed = True
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if (
        getattr(controller, "project_disposed", False)
        and all(record["absent"] for record in records)
        and hasattr(controller, "volume_custody")
    ):
        for record in controller.volume_custody.records.values():
            if record["absent"] or "volume." + record["purpose"] + ".remove" in controller.used:
                continue
            try:
                controller.volume_custody.remove(record, end, environment)
            except BaseException as error:
                controller.fail(error, cleanup=True)
    for native in controller.native:
        try:
            while not native.settled and time.monotonic() < end:
                controller.pump(end)
            require(native.settled, "final_native_unsettled")
        except BaseException as error:
            controller.fail(error, cleanup=True)
        finally:
            for entry in native.handles:
                try:
                    controller._close_handle(native, entry)
                except BaseException as error:
                    controller.fail(error, cleanup=True)
            for entry in native.captures:
                try:
                    controller._close_capture(native, entry)
                except BaseException as error:
                    controller.fail(error, cleanup=True)
    for record in controller.accepted_records:
        try:
            close_owned_ipc(record, controller.selector, listener=False)
        except BaseException as error:
            controller.fail(error, cleanup=True)
    for record in controller.listener_records:
        try:
            close_owned_ipc(record, controller.selector, listener=True)
        except BaseException as error:
            controller.fail(error, cleanup=True)
        try:
            require(record["closed"] and record["identity"] is not None, "socket_removal_unverified")
            observed = os.lstat(record["path"])
            require(
                stat.S_ISSOCK(observed.st_mode)
                and (observed.st_dev, observed.st_ino, observed.st_uid, observed.st_gid) == record["identity"],
                "socket_removal_replaced",
            )
            os.unlink(record["path"])
            require(not os.path.lexists(record["path"]), "socket_removal_remaining")
            record["removed"] = True
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if controller.selector is not None:
        try:
            controller.selector.close()
            controller.selector_closed = True
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if hasattr(controller, "workspace_tree"):
        try:
            require(time.monotonic() < end, "workspace_disposal_deadline")
            controller.workspace_tree.end = end
            controller.workspace_tree.remove(controller)
            for entry in controller.files.entries:
                if entry["path"] == controller.workspace_tree.path:
                    entry["removed"] = controller.workspace_tree.removed
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if hasattr(controller, "log_tree") and controller.log_tree.identity is not None:
        try:
            require(time.monotonic() < end, "log_disposal_deadline")
            controller.log_tree.end = end
            restore_task_log(controller, end, environment)
            controller.log_tree.remove(controller)
        except BaseException as error:
            controller.log_tree.unknown = True
            controller.fail(error, cleanup=True)
    if "inventory.target_final.volumes" not in controller.used and hasattr(controller, "original_model"):
        try:
            require(
                getattr(controller, "project_disposed", False)
                and all(record["absent"] for record in controller.custody.records.values())
                and all(record["absent"] for record in controller.volume_custody.records.values()),
                "final_volume_writers_or_objects_unknown",
            )
            census_end = min(end, getattr(controller, "teardown_end", end))
            project_inventory(controller, "target_final", census_end, environment, cleanup=True, kinds=("volumes",))
        except BaseException as error:
            controller.fail(error, cleanup=True)
    if getattr(controller, "project_disposed", False) and hasattr(controller, "image_custody"):
        for record in controller.image_custody.records.values():
            if record["absent"] or "image." + record["purpose"] + ".remove" in controller.used:
                continue
            try:
                require(
                    all(entry["absent"] for entry in controller.custody.records.values()),
                    "image_delete_live_or_unknown_container",
                )
                controller.image_custody.remove(record, end, environment)
            except BaseException as error:
                record["unknown"] = True
                controller.fail(error, cleanup=True)
    close_private_files(controller)
    if hasattr(controller, "docker_config"):
        entries = [entry for entry in controller.files.entries if entry["path"] == controller.docker_config]
        controller.credentials_disposed = len(entries) == 1 and entries[0]["removed"] is True


def exclude_log_descendant_mounts(controller):
    descriptor = None
    original = None
    raw = bytearray()
    try:
        descriptor = os.open("/proc/self/mountinfo", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), "mountinfo_profile")
        while True:
            require(time.monotonic() < controller.whole_end, "mountinfo_deadline")
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= 1024 * 1024, "mountinfo_bound")
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino) and raw and raw.endswith(b"\n"),
            "mountinfo_capture",
        )
    except BaseException as error:
        original = error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                original = first_error(original, error)
    if original is not None:
        raise original
    path = str(controller.log_tree.path)
    rows = bytes(raw).decode("ascii", "strict").splitlines()
    require(len(rows) <= 4096, "mountinfo_cardinality")
    for row in rows:
        columns = row.split(" ")
        require(len(columns) >= 10 and "-" in columns and all(columns), "mountinfo_shape")
        mount = columns[4]

        def unescape(match):
            value = match.group(0)
            require(value in ("\\040", "\\011", "\\012", "\\134"), "mountinfo_escape")
            return chr(int(value[1:], 8))

        mount = re.sub(r"\\[0-9]{3}", unescape, mount)
        require(mount.startswith("/") and "\0" not in mount and "\\" not in mount, "mountinfo_path")
        require(mount != path and not mount.startswith(path + "/"), "log_descendant_mount")


def restore_task_log(controller, end, environment):
    tree = controller.log_tree
    require(
        getattr(controller, "project_disposed", False)
        and all(native.process is None or native.settled for native in controller.native)
        and all(record["absent"] for record in controller.custody.records.values()),
        "log_restore_writers_unsettled",
    )
    require(
        controller.image_custody.records.get("api", {}).get("qualified") is True
        and controller.image_custody.records["api"]["absent"] is False
        and controller.disposable_daemon_admitted is True,
        "log_restore_image_unknown",
    )
    api = controller.image_custody.records["api"]["first"]
    require(
        api["Config"].get("Volumes") in (None, {})
        and not any(
            key.startswith("LD_") or key in ("BASH_ENV", "ENV") for key in controller.api_inherited_environment
        ),
        "log_restore_image_injection",
    )
    exclude_log_descendant_mounts(controller)
    root_before = os.lstat(tree.path)
    before = tree.snapshot()
    uid, gid = os.getuid(), os.getgid()
    requires_restore = (root_before.st_uid, root_before.st_gid) != (uid, gid) or any(
        (record["identity"][3], record["identity"][4]) != (uid, gid) for record in before
    )
    if not requires_restore:
        return
    controller.values["qualified_owned_log_dir"] = str(tree.path)
    cid = created_cid(controller, "log_restore.create", end, environment, cleanup=True)
    record = controller.custody.acquire("log_restore", cid)
    observed = inspect_native(controller, "log_restore.qualified", end, environment, cid, cleanup=True)
    native_container_config(
        controller,
        observed,
        "log-restore",
        api["Id"],
        "none",
        "0:0",
        command=[
            "--recursive",
            "--no-dereference",
            "--preserve-root",
            "-P",
            "--",
            "+" + str(uid) + ":+" + str(gid),
            "/task-log",
        ],
        entrypoint=["/usr/bin/chown"],
    )
    host = observed["HostConfig"]
    require(
        host.get("ReadonlyRootfs") is True
        and host.get("CapDrop") == ["ALL"]
        and set(host.get("CapAdd", [])) == {"CHOWN", "DAC_READ_SEARCH"}
        and host.get("SecurityOpt") == ["no-new-privileges:true"]
        and host.get("PidsLimit") == 8
        and type(host["PidsLimit"]) is int
        and host.get("Memory") == 64 * 1024 * 1024
        and host.get("NanoCpus") == 250000000
        and host.get("RestartPolicy") == {"Name": "no", "MaximumRetryCount": 0}
        and host.get("PortBindings") in (None, {})
        and host.get("Devices") in (None, [])
        and host.get("Binds") in (None, [])
        and observed["Config"].get("Healthcheck") == {"Test": ["NONE"]}
        and environment_map(observed["Config"].get("Env")) == controller.api_inherited_environment,
        "log_restore_native_profile",
    )
    require(len(observed["Mounts"]) == 1, "log_restore_mount_count")
    mount_association(observed, str(tree.path), "/task-log", False)
    controller.custody.admit_created(record, observed)
    original = None
    try:
        record["native"] = controller.run("log_restore.start", end, environment, cleanup=True)
        terminal = inspect_native(controller, "log_restore.terminal", end, environment, cid, cleanup=True)
        controller.custody.terminal(record, terminal, 0)
        controller.custody.remove(record, end, environment, cleanup=True)
    except BaseException as error:
        original = error
        controller.fail(error, cleanup=True)
        try:
            failed_end = min(controller.emergency_end, controller.whole_end)
            cleanup_native_container(controller, record, failed_end, environment)
        except BaseException as secondary:
            record["unknown"] = True
            controller.fail(secondary, cleanup=True)
    if original is not None:
        raise original
    root_after = os.lstat(tree.path)
    require(
        (root_after.st_dev, root_after.st_ino) == tree.identity
        and (root_after.st_uid, root_after.st_gid) == (uid, gid)
        and (root_before.st_mode, root_before.st_nlink, root_before.st_size, root_before.st_mtime_ns)
        == (root_after.st_mode, root_after.st_nlink, root_after.st_size, root_after.st_mtime_ns),
        "log_restore_root_effect",
    )
    after = tree.snapshot()
    first = {str(value["path"]): value["identity"] for value in before}
    second = {str(value["path"]): value["identity"] for value in after}
    require(first.keys() == second.keys(), "log_restore_membership_drift")
    for path, identity in first.items():
        actual = second[path]
        require(
            (actual[3], actual[4]) == (uid, gid)
            and tuple(actual[index] for index in (0, 1, 2, 5, 6, 7))
            == tuple(identity[index] for index in (0, 1, 2, 5, 6, 7)),
            "log_restore_member_effect",
        )


def initialize_parent(controller, environment):
    root = Path.cwd().resolve(strict=True)
    require(root.is_dir(), "candidate_root")
    controller.values.update(
        {
            "candidate_root": str(root),
            "invocation": controller.invocation,
            "actual_host_uid": str(os.getuid()),
            "actual_host_gid": str(os.getgid()),
        }
    )
    require(os.getuid() != 0 and os.getgid() != 0, "parent_nonroot")
    private = Path("/tmp") / ("bifrost-f4-parent-" + controller.invocation)
    controller.private_directory = private
    controller.files.directory(private, 0o700)
    controller.capture_directory = controller.files.directory(private / "captures", 0o700)
    controller.docker_config = controller.files.directory(private / "docker-config", 0o700)
    environment["DOCKER_CONFIG"] = str(controller.docker_config)
    workspace = controller.files.directory(private / "workspace.git", 0o700)
    workspace_stat = os.lstat(workspace)
    controller.workspace_tree = LogTreeCustody(workspace)
    controller.workspace_tree.identity = (workspace_stat.st_dev, workspace_stat.st_ino)
    controller.workspace_tree.initial_owner = (workspace_stat.st_uid, workspace_stat.st_gid)
    controller.workspace_tree.unknown = False
    controller.workspace_tree.end = controller.whole_end
    controller.values["owned_workspace_git"] = str(workspace)
    controller.values["owned_binary_path"] = str(private / "workflow_sql_vectors")
    controller.values["owned_resolved_json"] = str(private / "compose.resolved.json")
    project = "bifrost-test-" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    for key in ("owned_project", "target_project", "prepr_project"):
        controller.values[key] = project
    environment["COMPOSE_PROJECT_NAME"] = project
    controller.values["original_base"] = str(root / "docker-compose.test.yml")
    controller.values["configured_api_tag"] = "bifrost-test-api-dev:latest"
    tool_tag = "bifrost-f4-toolchain:" + controller.invocation
    for key in ("owned_toolchain_tag", "configured_toolchain_tag", "fixed_native_toolchain_tag"):
        controller.values[key] = tool_tag
    for purpose in (
        "fetch",
        "fmt",
        "clippy",
        "default_tests",
        "release",
        "schema_before",
        "schema_after",
        "relay",
        "python",
        "rust",
        "log_restore",
    ):
        controller.values[purpose + "_name"] = "bifrost-f4-" + controller.invocation + "-" + purpose
    controller.values["tool_version_name"] = "bifrost-f4-" + controller.invocation + "-tool-version"
    for purpose in ("cargo", "target"):
        controller.values[purpose + "_volume"] = "bifrost-f4-" + controller.invocation + "-" + purpose
    for role in ("observer", "relay", "python", "rust"):
        controller.values[role + "_socket"] = str(private / (role + ".sock"))
    controller.values.update(
        {
            "STATIC_READER_50ed8e4d": SOURCE_READER,
            "STATIC_ENTRYPOINT_DIGEST": FRONTEND_ENTRYPOINT,
            "STATIC_PROCESS_READBACK": FRONTEND_PROCESS,
            "STATIC_TCP_READBACK": FRONTEND_TCP,
            "STATIC_CONFIG_READBACK": FRONTEND_CONFIG,
            "STATIC_DNS_READBACK": FRONTEND_DNS,
            "config_path": "/etc/pgbouncer/pgbouncer.ini",
        }
    )
    controller.log_tree = LogTreeCustody(Path("/tmp") / ("bifrost-" + project))
    controller.log_tree.end = controller.whole_end
    controller.log_tree.create()
    controller.selector = selectors.DefaultSelector()
    controller.custody = ContainerCustody(controller)
    controller.volume_custody = VolumeCustody(controller)
    controller.image_custody = ImageCustody(controller)
    controller.project_inventories = {}
    controller.peer_sources = {}
    controller.cycles = []


def manifest_observation(controller):
    require(hasattr(controller, "sources") and hasattr(controller, "binary_source"), "manifest_source_missing")
    records = [
        {"path": path, "sha256": value["sha256"], "bytes": len(value["bytes"])}
        for path, value in sorted(controller.sources.items())
    ]
    # This is a software-content manifest only, not a hash of SQL, result rows,
    # messages, configuration, captures, credentials or customer payloads.
    raw = encode({"schema": "bifrost.test.f4-software-manifest/v1", "files": records}, 4 * 1024 * 1024)
    controller.manifest_sha256 = software_sha(raw)


def primary_status(controller):
    error = controller.first
    if error is None:
        return 0
    if isinstance(error, SystemExit) and type(error.code) is int and 0 <= error.code <= 255:
        return error.code
    if isinstance(error, KeyboardInterrupt):
        return 130
    return 1


def publish_evidence(controller):
    # Each artifact attempt is independent. A failed projection, write, close
    # or earlier disposal can never bypass the other attempts or handler restore.
    output = Path(controller.values["candidate_root"]) / "commit-fault-evidence"
    original = None
    acquired = False
    try:
        require(
            time.monotonic() < controller.whole_end and not os.path.lexists(output),
            "evidence_output_preexisting_or_expired",
        )
        os.mkdir(output, 0o700)
        current = os.lstat(output)
        require(
            stat.S_ISDIR(current.st_mode) and current.st_uid == os.getuid() and stat.S_IMODE(current.st_mode) == 0o700,
            "evidence_output_identity",
        )
        identity = file_identity(current)
        acquired = True
    except BaseException as error:
        original = first_error(original, error)
        controller.fail(error, cleanup=True)
    primary_exit = primary_status(controller)
    disposal = None
    try:
        disposal = disposal_projection(controller, primary_exit)
    except BaseException as error:
        original = first_error(original, error)
        controller.fail(error, cleanup=True)
    qualified = (
        controller.first is None
        and disposal is not None
        and disposal["complete"]
        and getattr(controller, "schema_matched", False)
        and getattr(controller, "source_final_admitted", False)
        and getattr(controller, "junit_admitted", False)
        and getattr(controller, "observer_source_closed", False)
        and len(getattr(controller, "cycles", [])) == 2
        and all(cycle.exposed() for cycle in controller.cycles)
    )
    if not qualified and controller.first is None:
        controller.fail(Failure("final_admission_failed"))
    for name, bound in (("fault.receipt.json", 16384), ("producer.json", 65536), ("disposal.json", 16384)):
        descriptor = None
        try:
            require(acquired and time.monotonic() < controller.whole_end, "publication_deadline")
            observed = os.lstat(output)
            require(
                observed.st_dev == identity[0]
                and observed.st_ino == identity[1]
                and observed.st_uid == identity[3]
                and stat.S_IMODE(observed.st_mode) == 0o700,
                "evidence_output_replaced",
            )
            primary_exit = primary_status(controller)
            if name == "fault.receipt.json":
                value = fault_projection(controller, qualified)
            elif name == "producer.json":
                value = producer_projection(controller, primary_exit, qualified)
            else:
                value = disposal_projection(controller, primary_exit)
            raw = public_json(value, bound)
            descriptor = os.open(
                output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
            )
            before = os.fstat(descriptor)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == os.getuid()
                and before.st_nlink == 1
                and before.st_size == 0,
                "safe_artifact_acquisition",
            )
            offset = 0
            while offset < len(raw):
                require(time.monotonic() < controller.whole_end, "safe_artifact_write_deadline")
                written = os.write(descriptor, raw[offset:])
                require(type(written) is int and written > 0, "safe_artifact_write")
                offset += written
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            require(
                after.st_size == len(raw)
                and after.st_nlink == 1
                and file_identity(after) == file_identity(os.lstat(output / name)),
                "safe_artifact_stable",
            )
        except BaseException as error:
            qualified = False
            original = first_error(original, error)
            controller.fail(error, cleanup=True)
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    original = first_error(original, error)
                    controller.fail(error, cleanup=True)
    if original is not None:
        raise original


def main():
    controller = Controller()
    environment = {}
    handlers = {}
    original = None
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            handlers[number] = signal.getsignal(number)

            def interrupt(actual, _frame):
                raise SystemExit(128 + actual)

            signal.signal(number, interrupt)
        environment = controlled_environment()
        initialize_parent(controller, environment)
        source_setup(controller, environment)
        cold_and_release(controller, environment)
        manifest_observation(controller)
        target_stack(controller, environment)
        runner_and_fault(controller)
        teardown_normal(controller)
    except BaseException as error:
        original = error
        controller.fail(error)
    finally:
        try:
            cleanup_all(controller, getattr(controller, "target_environment", environment))
        except BaseException as error:
            original = first_error(original, error)
            controller.fail(error, cleanup=True)
        try:
            require(
                disposal_projection(controller, primary_status(controller))["complete"],
                "final_disposal_incomplete",
            )
        except BaseException as error:
            original = first_error(original, error)
            controller.fail(error, cleanup=True)
        try:
            publish_evidence(controller)
        except BaseException as error:
            original = first_error(original, error)
            controller.fail(error, cleanup=True)
        for number, previous in handlers.items():
            try:
                signal.signal(number, previous)
            except BaseException as error:
                original = first_error(original, error)
                controller.fail(error, cleanup=True)
    if isinstance(controller.first, SystemExit):
        raise controller.first
    if isinstance(controller.first, KeyboardInterrupt):
        raise controller.first
    return 0 if controller.first is None and time.monotonic() < controller.whole_end else 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
