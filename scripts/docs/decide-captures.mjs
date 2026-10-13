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
import { readFileSync, existsSync, realpathSync, lstatSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const out = { full: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--docs-repo") out.docsRepo = argv[++i];
    else if (a === "--bifrost-repo") out.bifrostRepo = argv[++i];
    else if (a === "--full") out.full = true;
    else if (a === "--ids") out.ids = argv[++i];
    else if (a === "-h" || a === "--help") {
      console.log("usage: decide-captures.mjs --docs-repo <path> --bifrost-repo <path> [--full|--ids id1,id2]");
      process.exit(0);
    } else {
      console.error(`unknown arg: ${a}`);
      process.exit(2);
    }
  }
  if (!out.docsRepo || !out.bifrostRepo) {
    console.error("--docs-repo and --bifrost-repo are required");
    process.exit(2);
  }
  return out;
}

export function pathChanged(repo, sinceSha, globs) {
  if (!sinceSha) return true; // never captured before
  if (!globs || !globs.length) return true; // no globs = always recapture
  if (!/^[0-9a-f]{40}$/i.test(sinceSha)) return true;
  try {
    execFileSync("/usr/bin/git", ["rev-parse", "--verify", `${sinceSha}^{commit}`], {
      cwd: repo,
      stdio: "ignore",
    });
  } catch {
    return true; // unknown sha — treat as changed
  }
  try {
    execFileSync("/usr/bin/git", ["diff", "--quiet", sinceSha, "HEAD", "--", ...globs], {
      cwd: repo,
      stdio: "ignore",
    });
    return false;
  } catch {
    return true;
  }
}

export function readCaptureManifest(docsRepo) {
  const checkout = realpathSync(docsRepo);
  if (!existsSync(resolve(checkout, ".git"))) {
    throw new Error("docs repository must be a Git checkout");
  }
  const manifestPath = resolve(checkout, "screenshots.yaml");
  const stat = lstatSync(manifestPath);
  if (!stat.isFile() || stat.size > 1024 * 1024) {
    throw new Error("capture manifest must be a regular file of at most 1 MiB");
  }
  return readFileSync(manifestPath, "utf8");
}

async function main() {
  const args = parseArgs(process.argv);
  const manifestPath = resolve(args.docsRepo, "screenshots.yaml");
  if (!existsSync(manifestPath)) {
    console.error(`manifest not found at ${manifestPath}`);
    process.exit(1);
  }
  const { load: loadYaml } = await import("js-yaml");
  const manifest = loadYaml(readCaptureManifest(args.docsRepo));

  // External entries (Azure portal, VSCode, etc.) are never candidates.
  const captureable = manifest.entries.filter((e) => !e.external);

  if (args.ids) {
    const wanted = args.ids.split(",").map((s) => s.trim()).filter(Boolean);
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
    if (pathChanged(args.bifrostRepo, sinceSha, globs)) {
      candidates.push(entry.id);
    }
  }
  process.stdout.write(candidates.join(","));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
