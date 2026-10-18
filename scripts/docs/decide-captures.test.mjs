import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { pathChanged, readCaptureManifest } from "./decide-captures.mjs";

const scriptPath = fileURLToPath(
  new URL("./decide-captures.mjs", import.meta.url),
);

function fixtureRepo() {
  const repo = mkdtempSync(join(tmpdir(), "decide-captures-"));
  execFileSync("git", ["init", "-q"], { cwd: repo });
  execFileSync("git", ["config", "user.email", "test@example.com"], {
    cwd: repo,
  });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: repo });
  writeFileSync(join(repo, "screenshots.yaml"), "entries: []\n");
  writeFileSync(join(repo, "tracked.txt"), "initial\n");
  execFileSync("git", ["add", "screenshots.yaml", "tracked.txt"], {
    cwd: repo,
  });
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
    assert.equal(
      pathChanged(repo, `HEAD;touch ${marker}`, ["tracked.txt"]),
      true,
    );
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

test("capture manifest is read from bounded standard input", async () => {
  assert.equal(
    await readCaptureManifest(Readable.from(["entries: []\n"])),
    "entries: []\n",
  );
});

test("capture manifest input is required and limited to 1 MiB", async () => {
  await assert.rejects(
    readCaptureManifest(Readable.from([])),
    /manifest input is required/,
  );
  await assert.rejects(
    readCaptureManifest(Readable.from([Buffer.alloc(1024 * 1024 + 1)])),
    /at most 1 MiB/,
  );
});

test("selector rejects checkout paths from CLI input", () => {
  for (const option of ["--docs-repo", "--bifrost-repo"]) {
    const result = spawnSync(
      process.execPath,
      [scriptPath, option, "/tmp/other"],
      {
        encoding: "utf8",
        input: "entries: []\n",
      },
    );
    assert.equal(result.status, 2);
    assert.match(result.stderr, new RegExp(`unknown arg: ${option}`));
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
