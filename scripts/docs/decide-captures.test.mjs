import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { pathChanged } from "./decide-captures.mjs";

function fixtureRepo() {
  const repo = mkdtempSync(join(tmpdir(), "decide-captures-"));
  execFileSync("git", ["init", "-q"], { cwd: repo });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: repo });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: repo });
  writeFileSync(join(repo, "tracked.txt"), "initial\n");
  execFileSync("git", ["add", "tracked.txt"], { cwd: repo });
  execFileSync("git", ["commit", "-qm", "initial"], { cwd: repo });
  const sha = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: repo,
    encoding: "utf8",
  }).trim();
  return { repo, sha };
}

test("manifest SHA is data, not shell input", () => {
  const { repo } = fixtureRepo();
  const marker = join(repo, "sha-injected");
  try {
    assert.equal(pathChanged(repo, `HEAD;touch ${marker}`, ["tracked.txt"]), true);
    assert.equal(existsSync(marker), false);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("manifest pathspec is passed as one argv value", () => {
  const { repo, sha } = fixtureRepo();
  const marker = join(repo, "glob-injected");
  try {
    const maliciousGlob = `tracked.txt';touch ${marker};#`;
    assert.equal(pathChanged(repo, sha, [maliciousGlob]), false);
    assert.equal(existsSync(marker), false);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("changed matching path is detected", () => {
  const { repo, sha } = fixtureRepo();
  try {
    writeFileSync(join(repo, "tracked.txt"), "changed\n");
    execFileSync("git", ["add", "tracked.txt"], { cwd: repo });
    execFileSync("git", ["commit", "-qm", "change"], { cwd: repo });
    assert.equal(pathChanged(repo, sha, ["tracked.txt"]), true);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});
