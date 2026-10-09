const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { readConfiguration, createPeerOptions } = require('../server.cjs');
const { REVISION, assertRuntime, assertLibrary, loadInfrastructure } = require('../bootstrap.cjs');

// Synthetic inputs only: importing server.cjs must not start sockets or read private env files.
const env = { AVATAR_PUBLIC_ORIGIN: 'https://interview.example', AVATAR_TURN_HOST: 'turn.example',
  AVATAR_TURN_SECRET: '0123456789abcdef0123456789abcdef' };

test('public origin is exact HTTPS origin without credentials, query, or fragment', () => {
  assert.equal(readConfiguration(env).origin, 'https://interview.example');
  for (const origin of ['', 'http://interview.example', 'https://user:password@interview.example',
    'https://interview.example/agent/', 'https://interview.example/?query',
    'https://interview.example/#fragment', ' https://interview.example']) {
    assert.throws(() => readConfiguration({ ...env, AVATAR_PUBLIC_ORIGIN: origin }), /HTTPS origin/);
  }
});

test('TURN host and shared secret are validated without leaking the supplied secret', () => {
  for (const host of ['', 'turn.example:3478', 'turn.example/path', 'turn.example?secret']) {
    assert.throws(() => readConfiguration({ ...env, AVATAR_TURN_HOST: host }), /TURN host/);
  }
  assert.throws(() => readConfiguration({ ...env, AVATAR_TURN_SECRET: 'short-private-input' }),
    (error) => !error.message.includes('short-private-input') && /shared secret/.test(error.message));
});

test('each peer gets distinct one-hour REST credentials for both TURN transports', () => {
  const config = readConfiguration(env);
  const before = Math.floor(Date.now() / 1000);
  const first = createPeerOptions(config);
  const second = createPeerOptions(config);
  const turn = first.iceServers[1];
  const expires = Number(turn.username.split(':')[0]);
  assert.ok(expires >= before + 3600 && expires <= Math.floor(Date.now() / 1000) + 3600);
  assert.notEqual(turn.username, second.iceServers[1].username);
  assert.equal(turn.credential, crypto.createHmac('sha1', env.AVATAR_TURN_SECRET).update(turn.username).digest('base64'));
  assert.deepEqual(turn.urls, ['turn:turn.example:3478?transport=udp', 'turn:turn.example:3478?transport=tcp']);
  assert.ok(!JSON.stringify(first).includes(env.AVATAR_TURN_SECRET));
  assert.equal(first.iceTransportPolicy, 'all');
});

test('relay is explicit and malformed relay mode fails closed', () => {
  assert.equal(createPeerOptions(readConfiguration({ ...env, AVATAR_FORCE_RELAY: 'true' })).iceTransportPolicy, 'relay');
  assert.throws(() => readConfiguration({ ...env, AVATAR_FORCE_RELAY: 'TRUE' }), /true or false/);
});

test('runtime and dynamic-credential hook must match the tested build', () => {
  assertRuntime('22.14.0');
  assertRuntime('22.23.2');
  assertRuntime('24.0.0');
  assertRuntime('24.19.0');
  for (const version of ['20.19.0', '22.13.0', '23.0.0']) assert.throws(() => assertRuntime(version), /Node.js 22/);
  assert.throws(() => assertLibrary({ SignallingServer: class {}, InitLogging() {} }), /per-peer TURN/);
});

test('missing or mismatched build receipt is rejected before loading upstream code', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'interviewer-pixel-build-'));
  try {
    assert.throws(() => loadInfrastructure(directory), /npm run bootstrap/);
    fs.writeFileSync(path.join(directory, '.interviewer-build.json'), JSON.stringify({ revision: 'different' }));
    assert.throws(() => loadInfrastructure(directory), /wrong revision/);
    assert.match(REVISION, /^[a-f0-9]{40}$/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
