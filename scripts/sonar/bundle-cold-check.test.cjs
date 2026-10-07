const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {reviewConfig} = require('../../docs/design-modernization/fixtures/bundle-cold-check.cjs');

const required = {BIFROST_REVIEW_URL: 'http://127.0.0.1:3000', BIFROST_REVIEW_AUTH: 'private-state.json'};
function temporary(t) {
 const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bifrost-cold-config-test-'));
 t.after(() => fs.rmSync(root, {recursive: true, force: true}));
 return root;
}
test('missing preview configuration rejects before creating an output directory', t => {
 const root = temporary(t);
 const output = path.join(root, 'new-output');
 for (const env of [{}, {BIFROST_REVIEW_URL: required.BIFROST_REVIEW_URL}, {BIFROST_REVIEW_AUTH: required.BIFROST_REVIEW_AUTH}]) {
  assert.throws(() => reviewConfig({...env, BIFROST_REVIEW_OUTPUT: output}), /Set BIFROST_REVIEW_URL/);
 }
 assert.equal(fs.existsSync(output), false);
});
test('default output is a unique private directory', t => {
 const first = reviewConfig(required);
 const second = reviewConfig(required);
 t.after(() => {fs.rmSync(first.outputDir, {recursive: true, force: true}); fs.rmSync(second.outputDir, {recursive: true, force: true});});
 assert.notEqual(first.outputDir, second.outputDir);
 assert.equal(fs.statSync(first.outputDir).mode & 0o777, 0o700);
 assert.equal(first.baseUrl, required.BIFROST_REVIEW_URL);
 assert.equal(first.authPath, required.BIFROST_REVIEW_AUTH);
});
test('explicit private output is preserved', t => {
 const root = temporary(t);
 assert.equal(reviewConfig({...required, BIFROST_REVIEW_OUTPUT: root}).outputDir, root);
});
test('world-readable output is rejected without changing permissions', t => {
 const root = temporary(t);
 fs.chmodSync(root, 0o755);
 assert.throws(() => reviewConfig({...required, BIFROST_REVIEW_OUTPUT: root}), /private directory/);
 assert.equal(fs.statSync(root).mode & 0o777, 0o755);
});
test('symlink output is rejected without changing its target', t => {
 const root = temporary(t);
 const target = path.join(root, 'target');
 fs.mkdirSync(target, {mode: 0o700});
 const link = path.join(root, 'link');
 fs.symlinkSync(target, link);
 assert.throws(() => reviewConfig({...required, BIFROST_REVIEW_OUTPUT: link}), /symlink/);
 assert.deepEqual(fs.readdirSync(target), []);
});
