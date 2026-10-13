const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const filename = path.join(__dirname, "tailwind.js");

async function run(input, { inputLimit = 4096, outputLimit = 4096, css = "compiled", failure } = {}) {
  const handlers = {};
  let output = "";
  let exitCode = 0;
  let entry;
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), {
    Buffer, __dirname,
    require(name) {
      assert.equal(name, "@tailwindcss/node");
      return { async compile(value) {
        entry = value;
        if (failure) throw failure;
        return { build: () => css };
      } };
    },
    process: {
      argv: ["node", filename, String(outputLimit), String(inputLimit)],
      stdin: { setEncoding() {}, on(name, handler) { handlers[name] = handler; } },
      stdout: { write(value) { output += value; } },
      exit(code) { exitCode = code; },
    },
  }, { filename });
  handlers.data(input);
  await handlers.end();
  return { result: JSON.parse(output), exitCode, entry };
}

test("Tailwind helper accepts bounded authored CSS", async () => {
  const outcome = await run(JSON.stringify({
    candidates: ["flex"], user_css: [{ path: "styles.css", content: ".x { color: red; }" }],
  }));
  assert.equal(outcome.result.css, "compiled");
  assert.equal(outcome.result.error, null);
  assert.match(outcome.entry, /color: red/);
});

test("Tailwind helper rejects oversized UTF-8 input and output", async () => {
  const input = await run("éé", { inputLimit: 3 });
  assert.match(input.result.error, /input exceeds/);
  assert.equal(input.exitCode, 1);
  const output = await run("{}", { css: "éé", outputLimit: 3 });
  assert.match(output.result.error, /output exceeds/);
  assert.equal(output.exitCode, 1);
});

test("Tailwind helper reports malformed JSON and compiler errors", async () => {
  assert.equal((await run("{")).exitCode, 1);
  const outcome = await run("{}", { failure: new Error("compile rejected") });
  assert.equal(outcome.result.error, "compile rejected");
  assert.equal(outcome.result.css, null);
});
