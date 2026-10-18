import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { pathChanged, readCaptureManifest } from "./decide-captures.mjs";

function fixtureRepo() {
  const repo = mkdtempSync(join(tmpdir(), "decide-captures-"));
  execFileSync("git", ["init", "-q"], { cwd: repo });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: repo });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: repo });
  writeFileSync(join(repo, "screenshots.yaml"), "entries: []\n");
  writeFileSync(join(repo, "tracked.txt"), "initial\n");
  execFileSync("git", ["add", "screenshots.yaml", "tracked.txt"], { cwd: repo });
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

test("capture manifest is read from the committed Git blob", () => {
  const { repo } = fixtureRepo();
  try {
    assert.equal(readCaptureManifest(repo), "entries: []\n");
    writeFileSync(join(repo, "screenshots.yaml"), "entries: [uncommitted]\n");
    assert.throws(() => readCaptureManifest(repo), /must be committed/);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("capture manifest cannot be a committed symlink", () => {
  const { repo } = fixtureRepo();
  try {
    rmSync(join(repo, "screenshots.yaml"));
    symlinkSync("tracked.txt", join(repo, "screenshots.yaml"));
    execFileSync("git", ["add", "screenshots.yaml"], { cwd: repo });
    execFileSync("git", ["commit", "-qm", "replace manifest with symlink"], { cwd: repo });
    assert.throws(() => readCaptureManifest(repo), /committed regular file/);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("missing capture history or source filters require recapture", () => {
  const { repo, sha } = fixtureRepo();
  try {
    assert.equal(pathChanged(repo, null, ["tracked.txt"]), true);
    assert.equal(pathChanged(repo, sha, []), true);
    assert.equal(pathChanged(repo, "0".repeat(40), ["tracked.txt"]), true);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});
