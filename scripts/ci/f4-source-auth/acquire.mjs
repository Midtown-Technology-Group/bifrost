// Specific private F4 source acquisition. Runtime/security approval is separate.
import fs from 'node:fs';
import https from 'node:https';

const REQUEST_SCHEMA = 'bifrost.private.f4-app-request/v1';
const RESULT_SCHEMA = 'bifrost.private.f4-app-result/v1';
const CODES = new Set(['input_invalid', 'key_invalid', 'token_invalid', 'http_failed',
  'body_invalid', 'association_failed', 'expiry_failed', 'permission_failed',
  'write_failed', 'deadline', 'internal']);
class Fault extends Error {
  constructor(code) { super('private acquisition failed'); this.code = code; }
}
function need(value, code = 'input_invalid') { if (!value) throw new Fault(code); }
function closed(value, names, code = 'input_invalid') {
  need(value !== null && typeof value === 'object' && !Array.isArray(value), code);
  const keys = Object.keys(value).sort();
  need(keys.length === names.length && keys.every((key, i) => key === [...names].sort()[i]), code);
}
function integer(value, low, high) {
  need(Number.isSafeInteger(value) && value >= low && value <= high);
  return value;
}
function decimal(value) {
  need(typeof value === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(value));
  need(BigInt(value) <= 18446744073709551615n);
}
function tokenBytes(value) {
  need(typeof value === 'string' && value.length >= 1 && value.length <= 4096, 'token_invalid');
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    need(code >= 0x21 && code <= 0x7e, 'token_invalid');
  }
  return Buffer.from(value, 'ascii');
}
function descriptorStat(fd, operations = fs) {
  const observed = operations.fstatSync(fd, { bigint: true });
  need(observed.isFile() && observed.nlink === 1n && (observed.mode & 0o7777n) === 0o600n);
  return observed;
}
function identity(fd, operations = fs) {
  const observed = descriptorStat(fd, operations);
  need(observed.size >= 0n && observed.size <= 16384n);
  return { dev: String(observed.dev), ino: String(observed.ino),
    uid: Number(observed.uid), gid: Number(observed.gid), mode: 384,
    nlink: 1, size: Number(observed.size) };
}
function validateIdentity(value) {
  closed(value, ['dev', 'ino', 'uid', 'gid', 'mode', 'nlink', 'size']);
  decimal(value.dev); decimal(value.ino);
  integer(value.uid, 0, 4294967295); integer(value.gid, 0, 4294967295);
  need(value.mode === 384 && value.nlink === 1);
  integer(value.size, 0, 16384);
}
function sameIdentity(actual, expected, size = expected.size) {
  validateIdentity(expected);
  need(Object.keys(actual).every(key => actual[key] === (key === 'size' ? size : expected[key])));
}
function readComplete(fd, limit, expected = null, operations = fs) {
  const before = descriptorStat(fd, operations);
  need(before.size > 0n && before.size <= BigInt(limit));
  if (expected) sameIdentity(identity(fd, operations), expected);
  const data = Buffer.alloc(Number(before.size));
  let offset = 0;
  while (offset < data.length) {
    const got = operations.readSync(fd, data, offset, data.length - offset, offset);
    need(got > 0); offset += got;
  }
  const extra = Buffer.alloc(1);
  need(operations.readSync(fd, extra, 0, 1, offset) === 0);
  const after = descriptorStat(fd, operations);
  for (const name of ['dev', 'ino', 'uid', 'gid', 'mode', 'nlink', 'size', 'mtimeNs', 'ctimeNs']) {
    need(before[name] === after[name]);
  }
  return data;
}
function frameDecode(raw, limit) {
  need(Buffer.isBuffer(raw) && raw.length > 1 && raw.length <= limit);
  need(raw.at(-1) === 10 && !raw.subarray(0, -1).includes(10) && !raw.includes(13));
  for (const byte of raw.subarray(0, -1)) need(byte >= 0x20 && byte <= 0x7e);
  const text = raw.subarray(0, -1).toString('ascii');
  const parsed = JSON.parse(text);
  // Parent produces compact JSON; canonical roundtrip rejects duplicates and
  // alternate encodings without introducing another JSON parser.
  need(JSON.stringify(parsed) === text);
  return parsed;
}
function validateRequest(value, mode) {
  if (mode === 'mint') {
    closed(value, ['schema', 'mode', 'remaining_ms', 'app_id', 'installation_id', 'repository_id', 'key', 'token']);
    for (const key of ['app_id', 'installation_id', 'repository_id']) integer(value[key], 1, Number.MAX_SAFE_INTEGER);
    validateIdentity(value.key); validateIdentity(value.token);
    need(value.key.size > 0 && value.token.size === 0);
  } else {
    need(mode === 'revoke');
    closed(value, ['schema', 'mode', 'remaining_ms', 'token', 'token_length']);
    validateIdentity(value.token); integer(value.token_length, 1, 4096);
    need(value.token.size === value.token_length);
  }
  need(value.schema === REQUEST_SCHEMA && value.mode === mode);
  integer(value.remaining_ms, 1, 60000);
  return value;
}
function permissions(value) {
  need(value !== null && typeof value === 'object' && !Array.isArray(value), 'permission_failed');
  need(Object.keys(value).every(key => key === 'contents' || key === 'metadata'), 'permission_failed');
  need(value.contents === 'read', 'permission_failed');
  const metadata = Object.hasOwn(value, 'metadata') ? value.metadata : null;
  need(metadata === null ? !Object.hasOwn(value, 'metadata') : metadata === 'read', 'permission_failed');
  return { contents: 'read', metadata };
}
function repo(value, expected) {
  need(value !== null && typeof value === 'object' && !Array.isArray(value), 'association_failed');
  need(value.id === expected && value.full_name === 'MTG-Thomas/bifrost-workspace', 'association_failed');
}
function mintedMetadata(value, expected) {
  let selected = null; let id = null; let name = null;
  if (Object.hasOwn(value, 'repository_selection')) {
    need(value.repository_selection === 'selected', 'association_failed'); selected = value.repository_selection;
  }
  if (Object.hasOwn(value, 'repositories')) {
    need(Array.isArray(value.repositories) && value.repositories.length === 1, 'association_failed');
    repo(value.repositories[0], expected);
    id = value.repositories[0].id; name = value.repositories[0].full_name;
  }
  return { selected, id, name };
}
function associatedMetadata(value, continuation, expected) {
  need(!continuation && value?.total_count === 1 && Array.isArray(value.repositories)
    && value.repositories.length === 1, 'association_failed');
  repo(value.repositories[0], expected);
  return value;
}
function expiry(value, remaining) {
  need(typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value), 'expiry_failed');
  const parsed = Date.parse(value);
  need(Number.isFinite(parsed) && new Date(parsed).toISOString() === value.slice(0, -1) + '.000Z', 'expiry_failed');
  need(parsed > Date.now() + remaining, 'expiry_failed');
  return value;
}
function account(state, bytes) {
  need(Number.isSafeInteger(bytes) && bytes >= 0, 'body_invalid');
  // Reserve exactly one response allowance for the separately scoped revoke.
  // Three mint responses <=196608 plus revoke <=65536 imply total<=262144.
  need(state.bodyBytes + bytes <= state.bodyLimit, 'body_invalid');
  state.bodyBytes += bytes;
}
function requestOnce(state, method, path, authorization, expectedStatus, body = null, requestFactory = https.request) {
  need(state.calls < (state.mode === 'mint' ? 3 : 1), 'internal');
  state.calls++;
  return new Promise((resolve, reject) => {
    let original = null; let response = null; let socket = null; let request = null;
    let requestClosed = false; let responseClosed = false; let socketClosed = false;
    let ended = false; let finished = false; let result = null;
    let size = 0; const chunks = [];
    const fail = error => {
      if (original === null) original = error;
      for (const resource of [response, request, socket]) {
        if (resource !== null) {
          try { resource.destroy(); } catch (caught) { if (original === null) original = caught; }
        }
      }
      complete();
    };
    const complete = () => {
      if (finished || !requestClosed || (response !== null && !responseClosed) || (socket !== null && !socketClosed)) return;
      finished = true;
      if (original !== null) reject(original);
      else if (ended && result !== null) resolve(result);
      else reject(new Fault('http_failed'));
    };
    try {
      need(!state.abort.signal.aborted, 'deadline');
      const headers = { Authorization: authorization, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'bifrost-f4-source-acquisition',
        'Accept-Encoding': 'identity', Connection: 'close' };
      if (body !== null) {
        need(Buffer.isBuffer(body) && body.length <= 1024, 'internal');
        headers['Content-Type'] = 'application/json'; headers['Content-Length'] = body.length;
      }
      request = requestFactory({ protocol: 'https:', hostname: 'api.github.com', port: 443,
        servername: 'api.github.com', agent: false, rejectUnauthorized: true,
        minVersion: 'TLSv1.2', maxHeaderSize: 16384, insecureHTTPParser: false,
        signal: state.abort.signal, method, path, headers });
      request.on('error', error => fail(error));
      request.once('close', () => { requestClosed = true; complete(); });
      request.once('socket', actual => {
        socket = actual;
        try {
          socket.on('error', error => fail(error));
          socket.once('close', () => { socketClosed = true; complete(); });
        } catch (error) { fail(error); }
      });
      request.once('response', actual => {
        response = actual;
        try {
        response.on('error', error => fail(error));
        response.once('aborted', () => fail(new Fault('http_failed')));
        response.once('close', () => { responseClosed = true; complete(); });
        response.on('data', chunk => {
          try {
            need(Buffer.isBuffer(chunk) && size + chunk.length <= 65536, 'body_invalid');
            account(state, chunk.length); size += chunk.length;
            if (response.statusCode === expectedStatus) chunks.push(Buffer.from(chunk));
          } catch (error) { fail(error); }
        });
        response.once('end', () => {
          try {
            need(response.complete === true, 'http_failed');
            need(response.statusCode === expectedStatus, 'http_failed');
            need(!response.headers['content-encoding'] || response.headers['content-encoding'] === 'identity', 'body_invalid');
            // Byte admission is complete BEFORE concatenation/decode/JSON.
            const raw = Buffer.concat(chunks, size);
            if (expectedStatus === 204) { need(size === 0, 'body_invalid'); result = { status: 204 }; }
            else {
              need(size > 0 && typeof response.headers['content-type'] === 'string'
                && /^application\/json(?:\s*;|$)/.test(response.headers['content-type']), 'body_invalid');
              const text = new TextDecoder('utf-8', { fatal: true }).decode(raw);
              result = { status: expectedStatus, value: JSON.parse(text), continuation: Object.hasOwn(response.headers, 'link') };
            }
            ended = true; complete();
          } catch (error) { fail(error); }
        });
        } catch (error) { fail(error); }
      });
      request.end(body === null ? undefined : body);
    } catch (error) {
      if (request === null) requestClosed = true;
      fail(error);
    }
  });
}
function writeToken(fd, raw, expected, operations = fs) {
  sameIdentity(identity(fd, operations), expected);
  let offset = 0;
  while (offset < raw.length) {
    const count = operations.writeSync(fd, raw, offset, raw.length - offset, offset);
    need(Number.isSafeInteger(count) && count > 0 && count <= raw.length - offset, 'write_failed'); offset += count;
  }
  operations.fsyncSync(fd);
  sameIdentity(identity(fd, operations), expected, raw.length);
  const actual = readComplete(fd, 4096, { ...expected, size: raw.length }, operations);
  need(actual.equals(raw), 'write_failed');
  return raw.length;
}
function closeOwnedDescriptors(owned, original, operations = fs) {
  for (const fd of owned) {
    try { operations.closeSync(fd); } catch (error) { if (original === null) original = error; }
  }
  return original;
}
function descriptorArguments(argv, mode) {
  need(mode === 'mint' || mode === 'revoke');
  need(argv.length === (mode === 'mint' ? 3 : 2));
  const fds = argv.map(value => {
    need(typeof value === 'string' && /^[1-9][0-9]*$/.test(value));
    return integer(Number(value), 3, 1048575);
  });
  need(new Set(fds).size === fds.length);
  return fds;
}
async function controls() {
  const reject = callback => { let failed = false; try { callback(); } catch (error) { need(error instanceof Error); failed = true; } need(failed); };
  const rejectCode = (callback, code) => { let caught = null; try { callback(); } catch (error) { caught = error; } need(caught instanceof Fault && caught.code === code); };
  for (const value of ['x', '!'.repeat(4096), '\\"/:;']) need(tokenBytes(value).length === value.length);
  for (const value of ['', 'x'.repeat(4097), ' ', '\r', '\n', '\0', '\x7f', '\u0080']) reject(() => tokenBytes(value));
  need(permissions({ contents: 'read' }).metadata === null);
  reject(() => permissions({ contents: 'write' })); reject(() => permissions({ contents: 'read', metadata: null }));
  const state = { bodyBytes: 0, bodyLimit: 65536 }; account(state, 65536); reject(() => account(state, 1));
  const canonical = Buffer.from('{"a":1}\n'); need(frameDecode(canonical, 1024).a === 1);
  reject(() => frameDecode(Buffer.from('{"a":1,"a":2}\n'), 1024));
  reject(() => frameDecode(Buffer.from('{"a":1}\n\n'), 1024));
  // All fixtures below are inert local data/handles passed to the SAME native-default helpers.
  const ident = { dev: '1', ino: '2', uid: 1, gid: 1, mode: 384, nlink: 1, size: 0 };
  const mint = { schema: REQUEST_SCHEMA, mode: 'mint', remaining_ms: 1,
    app_id: 1, installation_id: 2, repository_id: 3, key: { ...ident, size: 1 }, token: ident };
  const revoke = { schema: REQUEST_SCHEMA, mode: 'revoke', remaining_ms: 60000,
    token: { ...ident, size: 1 }, token_length: 1 };
  need(validateRequest(mint, 'mint') === mint && validateRequest(revoke, 'revoke') === revoke);
  need(descriptorArguments(['3', '4', '5'], 'mint').length === 3);
  need(descriptorArguments(['3', '4'], 'revoke').length === 2);
  reject(() => descriptorArguments(['3', '4', '5'], 'revoke'));
  for (const args of [[], ['3', '3', '4'], ['2', '4', '5'], ['03', '4', '5'], ['1048576', '4', '5']]) {
    reject(() => descriptorArguments(args, 'mint'));
  }
  for (const id of [true, 0, Number.MAX_SAFE_INTEGER + 1]) reject(() => validateRequest({ ...mint, app_id: id }, 'mint'));
  reject(() => validateRequest({ ...mint, mode: 'revoke' }, 'mint'));
  reject(() => validateRequest({ ...mint, extra: null }, 'mint'));
  const missing = { ...mint }; delete missing.key; reject(() => validateRequest(missing, 'mint'));
  for (const token of [{ ...ident, nlink: 2 }, { ...ident, mode: 420 }, { ...ident, dev: '01' }, { ...ident, size: 1 }]) {
    reject(() => validateRequest({ ...mint, token }, 'mint'));
  }
  reject(() => validateRequest({ ...mint, key: { ...ident, size: 0 } }, 'mint'));
  // Frame-layer maxima are valid JSON, not fabricated full request grammar.
  const maximum = Buffer.from('"' + 'x'.repeat(1021) + '"\n');
  need(maximum.length === 1024 && frameDecode(maximum, 1024).length === 1021);
  reject(() => frameDecode(Buffer.from('"' + 'x'.repeat(1022) + '"\n'), 1024));
  reject(() => frameDecode(Buffer.from('{}'), 1024));
  reject(() => frameDecode(Buffer.from('{}\n{}\n'), 1024));
  const association = { id: 3, full_name: 'MTG-Thomas/bifrost-workspace' };
  const observedSelection = mintedMetadata({ repository_selection: 'selected', repositories: [association] }, 3);
  need(observedSelection.selected === 'selected' && observedSelection.id === 3 && observedSelection.name === association.full_name);
  need(mintedMetadata({}, 3).id === null);
  for (const value of [{ repository_selection: null }, { repository_selection: 'all' }, { repositories: null },
    { repositories: [association, association] }, { repositories: [{ ...association, id: 4 }] },
    { repositories: [{ ...association, full_name: 'other/repository' }] }]) {
    rejectCode(() => mintedMetadata(value, 3), 'association_failed');
  }
  const singleton = { total_count: 1, repositories: [association] };
  need(associatedMetadata(singleton, false, 3) === singleton);
  rejectCode(() => associatedMetadata(singleton, true, 3), 'association_failed');
  for (const value of [{ ...singleton, total_count: 2 }, { ...singleton, repositories: [association, association] },
    { ...singleton, repositories: [{ ...association, id: 4 }] },
    { ...singleton, repositories: [{ ...association, full_name: 'other/repository' }] }]) {
    rejectCode(() => associatedMetadata(value, false, 3), 'association_failed');
  }
  // Actual current wallclock admission, no injected/mutated clock or control timer.
  rejectCode(() => expiry('2000-01-01T00:00:00Z', 0), 'expiry_failed');
  rejectCode(() => expiry('2030-02-30T00:00:00Z', 0), 'expiry_failed');
  function fileFixture(options = {}) {
    const fault = new Error('inert'); let stored = Buffer.alloc(0); let writes = 0;
    const stat = () => ({ isFile: () => true, dev: 1n, ino: options.drift && stored.length ? 3n : 2n,
      uid: 1n, gid: 1n, mode: 0o100600n, nlink: 1n,
      size: BigInt(stored.length + (options.sizeMismatch && stored.length ? 1 : 0)), mtimeNs: 1n, ctimeNs: 1n });
    const operations = {
      fstatSync() { return stat(); },
      writeSync(_fd, raw, offset, length, position) {
        writes++;
        if (options.prefixThrow && writes === 2) throw fault;
        if (options.zero) return 0;
        if (options.invalid) return true;
        if (options.overshoot) { need(position === stored.length && offset === stored.length); stored = Buffer.concat([stored, raw.subarray(offset, offset + length)]); return length + 1; }
        const count = options.short || options.prefixThrow ? 1 : length;
        need(position === stored.length && offset === stored.length);
        stored = Buffer.concat([stored, raw.subarray(offset, offset + count)]);
        return count;
      },
      fsyncSync() { if (options.fsyncThrow) throw fault; },
      readSync(_fd, buffer, offset, length, position) {
        if (options.earlyEOF) return 0;
        if (position === stored.length) { if (options.extraEOF) return 1; return 0; }
        const count = Math.min(length, stored.length - position);
        (options.readMismatch ? Buffer.alloc(stored.length, 120) : stored).copy(buffer, offset, position, position + count);
        return count;
      },
    };
    return { operations, fault, stored: () => stored };
  }
  const tokenRaw = Buffer.from('abc');
  const initialMismatch = fileFixture();
  rejectCode(() => writeToken(4, tokenRaw, { ...ident, ino: '3' }, initialMismatch.operations), 'input_invalid');
  need(initialMismatch.stored().length === 0);
  const short = fileFixture({ short: true });
  need(writeToken(4, tokenRaw, ident, short.operations) === 3 && short.stored().equals(tokenRaw));
  for (const option of ['zero', 'invalid', 'overshoot', 'sizeMismatch', 'drift', 'readMismatch', 'earlyEOF', 'extraEOF']) {
    const fixture = fileFixture({ [option]: true });
    let completeLength = null;
    if (option === 'overshoot') rejectCode(() => { completeLength = writeToken(4, tokenRaw, ident, fixture.operations); }, 'write_failed'); else reject(() => { completeLength = writeToken(4, tokenRaw, ident, fixture.operations); });
    need(completeLength === null);
  }
  for (const option of ['prefixThrow', 'fsyncThrow']) {
    const fixture = fileFixture({ [option]: true }); let caught = null; let completeLength = null;
    try { completeLength = writeToken(4, tokenRaw, ident, fixture.operations); } catch (error) { caught = error; }
    need(caught === fixture.fault && completeLength === null);
    if (option === 'prefixThrow') need(fixture.stored().length === 1);
  }
  const closeFault = new Error('inert close'); const prior = new Error('inert prior'); const attempts = [];
  const closeOperations = { closeSync(fd) { attempts.push(fd); if (fd === 3) throw closeFault; } };
  need(closeOwnedDescriptors([3, 4], null, closeOperations) === closeFault && attempts.join(',') === '3,4');
  attempts.length = 0;
  need(closeOwnedDescriptors([3, 4], prior, closeOperations) === prior && attempts.join(',') === '3,4');
  class InertEmitter {
    constructor(failure = null) { this.listeners = new Map(); this.failure = failure; this.destroyFailure = null; this.destroyed = 0; }
    on(event, handler) {
      if (this.failure?.event === event) throw this.failure.error;
      const listeners = this.listeners.get(event) || []; listeners.push({ handler, once: false });
      this.listeners.set(event, listeners); return this;
    }
    once(event, handler) {
      if (this.failure?.event === event) throw this.failure.error;
      const listeners = this.listeners.get(event) || []; listeners.push({ handler, once: true });
      this.listeners.set(event, listeners); return this;
    }
    emit(event, ...args) {
      const actual = [...(this.listeners.get(event) || [])];
      this.listeners.set(event, actual.filter(listener => !listener.once));
      for (const listener of actual) listener.handler(...args);
    }
    destroy() { this.destroyed++; if (this.destroyFailure !== null) throw this.destroyFailure; }
    end() {}
  }
  const turns = async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); };
  const observe = promise => {
    const observed = { settled: false, value: null, error: null };
    // Attach both observers immediately, including to intentionally pending promises.
    promise.then(value => { observed.settled = true; observed.value = value; },
      error => { observed.settled = true; observed.error = error; });
    return observed;
  };
  function httpFixture(options = {}) {
    const state = { mode: 'mint', calls: 0, bodyBytes: options.exhausted ? 196608 : 0,
      bodyLimit: 196608, abort: { signal: { aborted: Boolean(options.preAborted) } } };
    const request = new InertEmitter(); const socket = new InertEmitter();
    const response = new InertEmitter(options.registration || null);
    response.statusCode = options.status ?? 200; response.complete = !options.incomplete;
    response.headers = { 'content-type': 'application/json' }; let factoryCalls = 0;
    const promise = requestOnce(state, 'GET', '/inert', 'Bearer inert', options.expected ?? 200, null,
      actualOptions => { factoryCalls++; need(actualOptions.hostname === 'api.github.com' && actualOptions.agent === false); return request; });
    const observed = observe(promise);
    return { state, request, socket, response, observed, factoryCalls: () => factoryCalls };
  }
  const closeAll = async fixture => {
    fixture.request.emit('close'); fixture.response.emit('close'); fixture.socket.emit('close'); await turns();
  };
  const successful = httpFixture(); successful.request.emit('socket', successful.socket);
  successful.request.emit('response', successful.response); successful.response.emit('data', Buffer.from('{}'));
  successful.response.emit('end'); await turns(); need(!successful.observed.settled);
  successful.request.emit('close'); successful.response.emit('close'); await turns(); need(!successful.observed.settled);
  successful.socket.emit('close'); await turns(); need(successful.observed.settled && successful.observed.error === null);
  for (const last of ['request', 'response', 'socket']) {
    const fixture = httpFixture(); fixture.request.emit('socket', fixture.socket); fixture.request.emit('response', fixture.response);
    fixture.response.emit('data', Buffer.from('{}')); fixture.response.emit('end');
    for (const name of ['request', 'response', 'socket']) if (name !== last) fixture[name].emit('close');
    await turns(); need(!fixture.observed.settled);
    fixture[last].emit('close'); await turns(); need(fixture.observed.settled && fixture.observed.error === null);
  }
  const empty = httpFixture({ status: 204, expected: 204 }); empty.request.emit('response', empty.response);
  empty.response.emit('end'); await closeAll(empty); need(empty.observed.value?.status === 204);
  const aborted = httpFixture({ preAborted: true }); await turns();
  need(aborted.factoryCalls() === 0 && aborted.state.calls === 1
    && aborted.observed.error instanceof Fault && aborted.observed.error.code === 'deadline');
  const maximumBody = Buffer.from('"' + 'x'.repeat(65534) + '"');
  need(maximumBody.length === 65536);
  const maximumResponse = httpFixture(); maximumResponse.request.emit('socket', maximumResponse.socket);
  maximumResponse.request.emit('response', maximumResponse.response); maximumResponse.response.emit('data', maximumBody);
  maximumResponse.response.emit('end'); await closeAll(maximumResponse);
  need(maximumResponse.observed.error === null && maximumResponse.observed.value?.value.length === 65534
    && maximumResponse.state.bodyBytes === 65536);
  for (const option of ['incomplete', 'status', 'aborted', 'overflow', 'exhausted']) {
    const fixture = httpFixture(option === 'status' ? { status: 403 } : { [option]: true });
    fixture.request.emit('socket', fixture.socket); fixture.request.emit('response', fixture.response);
    if (option === 'aborted') fixture.response.emit('aborted');
    const raw = option === 'overflow' ? Buffer.from('"' + 'x'.repeat(65535) + '"') : Buffer.from('{}');
    fixture.response.emit('data', raw); fixture.response.emit('end');
    await turns(); need(!fixture.observed.settled); await closeAll(fixture);
    const code = option === 'overflow' || option === 'exhausted' ? 'body_invalid' : 'http_failed';
    need(fixture.observed.error instanceof Fault && fixture.observed.error.code === code
      && fixture.request.destroyed > 0 && fixture.response.destroyed > 0 && fixture.socket.destroyed > 0);
    if (option === 'overflow') need(raw.length === 65537 && fixture.state.bodyBytes === 0);
    if (option === 'exhausted') need(fixture.state.bodyBytes === 196608);
  }
  const invalidUTF8 = httpFixture(); invalidUTF8.request.emit('socket', invalidUTF8.socket);
  invalidUTF8.request.emit('response', invalidUTF8.response); invalidUTF8.response.emit('data', Buffer.from([34, 195, 40, 34]));
  invalidUTF8.response.emit('end'); await closeAll(invalidUTF8);
  need(invalidUTF8.observed.error instanceof TypeError && !(invalidUTF8.observed.error instanceof Fault)
    && invalidUTF8.state.bodyBytes === 4 && invalidUTF8.response.destroyed > 0);
  const missingEnd = httpFixture(); missingEnd.request.emit('socket', missingEnd.socket);
  missingEnd.request.emit('response', missingEnd.response); missingEnd.response.emit('data', Buffer.from('{}'));
  await closeAll(missingEnd);
  need(missingEnd.observed.error instanceof Fault && missingEnd.observed.error.code === 'http_failed');
  const firstFault = new Error('inert first'); const destroyFault = new Error('inert destroy');
  const failure = httpFixture(); failure.request.emit('socket', failure.socket); failure.request.emit('response', failure.response);
  failure.socket.destroyFailure = destroyFault; failure.response.emit('error', firstFault);
  await turns(); need(!failure.observed.settled); await closeAll(failure); need(failure.observed.error === firstFault);
  const registrationFault = new Error('inert registration');
  const registration = httpFixture({ registration: { event: 'data', error: registrationFault } });
  registration.request.emit('socket', registration.socket); registration.request.emit('response', registration.response);
  await turns(); need(!registration.observed.settled && registration.response.destroyed > 0);
  await closeAll(registration); need(registration.observed.error === registrationFault);
  // No response close listener exists in this fixture. Its negative remains
  // unobservable/pending even after fake closes; never synthesize settlement.
  const unknown = httpFixture({ registration: { event: 'close', error: registrationFault } });
  unknown.request.emit('response', unknown.response); await turns(); need(!unknown.observed.settled);
  await closeAll(unknown); need(!unknown.observed.settled && unknown.response.destroyed > 0);
}
function outputFrame(value) {
  const raw = Buffer.from(JSON.stringify(value) + '\n', 'ascii');
  need(raw.length <= 2048, 'internal');
  let offset = 0;
  while (offset < raw.length) { const got = fs.writeSync(1, raw, offset); need(got > 0, 'internal'); offset += got; }
}
async function main() {
  let original = null; let tokenLength = null; let evidence = null; let status = 'failure';
  let timer = null; let state = null; const owned = [];
  const mode = process.argv[2];
  try {
    need(mode === 'mint' || mode === 'revoke');
    const fds = descriptorArguments(process.argv.slice(3), mode);
    owned.push(...fds);
    await controls();
    const request = validateRequest(frameDecode(readComplete(fds[0], 1024), 1024), mode);
    state = { mode, bodyBytes: 0, bodyLimit: mode === 'mint' ? 196608 : 65536,
      calls: 0, abort: new AbortController() };
    const began = performance.now();
    timer = setTimeout(() => state.abort.abort(), request.remaining_ms);
    if (mode === 'mint') {
      const key = new TextDecoder('utf-8', { fatal: true }).decode(readComplete(fds[2], 16384, request.key));
      const { createAppAuth } = await import('@octokit/auth-app');
      const silent = { debug() {}, info() {}, warn() {}, error() {} };
      const forbiddenRequest = () => { throw new Fault('internal'); };
      const auth = createAppAuth({ appId: request.app_id, privateKey: key, request: forbiddenRequest, log: silent });
      const signed = await auth({ type: 'app' });
      const jwt = tokenBytes(signed.token).toString('ascii');
      const installation = await requestOnce(state, 'GET', '/repos/MTG-Thomas/bifrost-workspace/installation', 'Bearer ' + jwt, 200);
      need(installation.value?.id === request.installation_id, 'association_failed');
      const minted = await requestOnce(state, 'POST', `/app/installations/${request.installation_id}/access_tokens`, 'Bearer ' + jwt, 201,
        Buffer.from(JSON.stringify({ repository_ids: [request.repository_id], permissions: { contents: 'read' } })));
      const rawToken = tokenBytes(minted.value?.token);
      tokenLength = writeToken(fds[1], rawToken, request.token);
      const actualPermissions = permissions(minted.value.permissions);
      const actualExpiry = expiry(minted.value.expires_at, Math.max(0, request.remaining_ms - (performance.now() - began)));
      const metadata = mintedMetadata(minted.value, request.repository_id);
      const associated = await requestOnce(state, 'GET', '/installation/repositories?per_page=100&page=1', 'Bearer ' + rawToken.toString('ascii'), 200);
      associatedMetadata(associated.value, associated.continuation, request.repository_id);
      evidence = { installation_id: installation.value.id, repository_id: associated.value.repositories[0].id,
        repository_full_name: associated.value.repositories[0].full_name, total_count: associated.value.total_count,
        expires_at: actualExpiry, permissions: actualPermissions, minted_selection: metadata.selected,
        minted_repository_id: metadata.id, minted_repository_full_name: metadata.name };
    } else {
      const rawToken = readComplete(fds[1], 4096, request.token);
      tokenBytes(rawToken.toString('ascii'));
      need(rawToken.length === request.token_length && rawToken.every(byte => byte >= 0x21 && byte <= 0x7e), 'token_invalid');
      tokenLength = rawToken.length;
      const revoked = await requestOnce(state, 'DELETE', '/installation/token', 'Bearer ' + rawToken.toString('ascii'), 204);
      evidence = { http_status: revoked.status };
    }
    need(!state.abort.signal.aborted, 'deadline'); status = 'success';
  } catch (error) { original = error; }
  finally {
    if (timer !== null) clearTimeout(timer);
    if (state !== null) { try { state.abort.abort(); } catch (error) { if (original === null) original = error; } }
    original = closeOwnedDescriptors(owned, original);
  }
  if (original !== null) { status = 'failure'; evidence = null; }
  const code = original === null ? null : original instanceof Fault && CODES.has(original.code) ? original.code : 'internal';
  outputFrame({ schema: RESULT_SCHEMA, mode, status, code, token_length: tokenLength, evidence });
  process.exitCode = original === null ? 0 : 1;
}
// Never hand an uncaught library/network error to Node's default formatter.
main().catch(() => { process.exitCode = 1; });
