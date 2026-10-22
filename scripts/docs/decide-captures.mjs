#!/usr/bin/env node
/**
 * Decide which manifest entries should be re-captured based on source-glob
 * diffs against each entry's `captured_at.bifrost_sha`.
 *
 * Modes:
 *   default: only entries whose source_globs changed since their captured SHA.
 *   --full:  every entry (the post-processor's pixel-diff still gates commits).
 *
 * Outputs (stdout): comma-separated IDs to capture, suitable for
 * DOCS_CAPTURE_IDS. Empty string if nothing to do (also exit 0).
 */
import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MAX_MANIFEST_BYTES = 1024 * 1024;
const BIFROST_REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function parseArgs(argv) {
  const out = { full: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--full") out.full = true;
    else if (a === "--ids") out.ids = argv[++i];
    else if (a === "-h" || a === "--help") {
      console.log(
        "usage: git show HEAD:screenshots.yaml | decide-captures.mjs [--full|--ids id1,id2]",
      );
      process.exit(0);
    } else {
      console.error(`unknown arg: ${a}`);
      process.exit(2);
    }
  }
  return out;
}

export function pathChanged(repo, sinceSha, globs) {
  if (!sinceSha) return true; // never captured before
  if (!globs || !globs.length) return true; // no globs = always recapture
  if (!/^[0-9a-f]{40}$/i.test(sinceSha)) return true;
  try {
    execFileSync(
      "/usr/bin/git",
      ["rev-parse", "--verify", `${sinceSha}^{commit}`],
      {
        cwd: repo,
        stdio: "ignore",
      },
    );
  } catch {
    return true; // unknown sha — treat as changed
  }
  try {
    execFileSync(
      "/usr/bin/git",
      ["diff", "--quiet", sinceSha, "HEAD", "--", ...globs],
      {
        cwd: repo,
        stdio: "ignore",
      },
    );
    return false;
  } catch {
    return true;
  }
}

export async function readCaptureManifest(input) {
  const chunks = [];
  let size = 0;
  for await (const chunk of input) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_MANIFEST_BYTES) {
      throw new Error("capture manifest must be at most 1 MiB");
    }
    chunks.push(buffer);
  }
  if (size === 0) throw new Error("capture manifest input is required");
  return Buffer.concat(chunks, size).toString("utf8");
}

async function main() {
  const args = parseArgs(process.argv);
  const { load: loadYaml } = await import("js-yaml");
  const manifest = loadYaml(await readCaptureManifest(process.stdin));

  // External entries (Azure portal, VSCode, etc.) are never candidates.
  const captureable = manifest.entries.filter((e) => !e.external);

  if (args.ids) {
    const wanted = args.ids
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const valid = new Set(captureable.map((e) => e.id));
    const filtered = wanted.filter((id) => valid.has(id));
    process.stdout.write(filtered.join(","));
    return;
  }

  if (args.full) {
    process.stdout.write(captureable.map((e) => e.id).join(","));
    return;
  }

  const candidates = [];
  for (const entry of captureable) {
    const sinceSha = entry.captured_at?.bifrost_sha ?? null;
    const globs = entry.source_globs ?? [];
    if (pathChanged(BIFROST_REPO, sinceSha, globs)) {
      candidates.push(entry.id);
    }
  }
  process.stdout.write(candidates.join(","));
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href
) {
  await main();
}
