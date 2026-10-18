import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { pathChanged, readCaptureManifest } from "./decide-captures.mjs";

const scriptPath = fileURLToPath(
  new URL("./decide-captures.mjs", import.meta.url),
);
const pipelinePath = fileURLToPath(
  new URL("./run-pipeline.sh", import.meta.url),
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

function fixturePipelineWorktrees() {
  const root = mkdtempSync(join(tmpdir(), "capture-pipeline-worktrees-"));
  const first = join(root, "first");
  const second = join(root, "second");
  const firstScript = join(first, "scripts", "docs", "run-pipeline.sh");

  mkdirSync(join(first, "scripts", "docs"), { recursive: true });
  copyFileSync(pipelinePath, firstScript);
  execFileSync("git", ["init", "-q"], { cwd: first });
  execFileSync("git", ["config", "user.email", "test@example.com"], {
    cwd: first,
  });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: first });
  execFileSync("git", ["add", "scripts/docs/run-pipeline.sh"], {
    cwd: first,
  });
  execFileSync("git", ["commit", "-qm", "fixture"], { cwd: first });
  execFileSync("git", ["worktree", "add", "-q", "-b", "second", second], {
    cwd: first,
  });

  return { root, first, second, firstScript };
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

test("selector runs when invoked through a symlink", () => {
  const directory = mkdtempSync(join(tmpdir(), "decide-captures-link-"));
  const link = join(directory, "selector.mjs");
  try {
    symlinkSync(scriptPath, link);
    const result = spawnSync(process.execPath, [link, "--full"], {
      encoding: "utf8",
      input: "entries: [{ id: symlinked }]\n",
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, "symlinked");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("pipeline rejects a different Bifrost worktree before side effects", () => {
  const { root, first, second, firstScript } = fixturePipelineWorktrees();
  const docsRepo = join(root, "docs");
  const captureMarker = join(docsRepo, ".tmp-captures", "keep");
  mkdirSync(join(docsRepo, ".tmp-captures"), { recursive: true });
  writeFileSync(captureMarker, "preserve\n");

  try {
    const result = spawnSync(
      "/bin/bash",
      [
        firstScript,
        "--docs-repo",
        docsRepo,
        "--bifrost-repo",
        second,
        "--full",
      ],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 1);
    assert.match(
      result.stderr,
      /--bifrost-repo must match the checkout containing run-pipeline\.sh/,
    );
    assert.equal(existsSync(captureMarker), true);
    assert.equal(
      existsSync(join(first, "scripts", "docs", "node_modules")),
      false,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
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
