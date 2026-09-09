import { randomBytes } from "node:crypto";
import { writeFile } from "node:fs/promises";

// Local-only setup. Never prints the token and never replaces an existing identity.
const target = new URL("../.dev.vars", import.meta.url);
const content = [
  "LAB_ENABLED=true",
  `LAB_TOKEN=${randomBytes(32).toString("hex")}`,
  "LAB_USER_ID=33333333-3333-4333-8333-333333333333",
  "LAB_ORG_ID=11111111-1111-4111-8111-111111111111",
  "",
].join("\n");
try {
  await writeFile(target, content, { flag: "wx", mode: 0o600 });
  console.log("Created ignored .dev.vars with a random lab token and synthetic identity. No remote resources changed.");
} catch (error) {
  console.error(error?.code === "EEXIST"
    ? ".dev.vars already exists; refusing to overwrite it."
    : "Could not create local lab configuration.");
  process.exitCode = 1;
}
