// Private UE 5.8 signalling. Django authenticates players; SSH carries the streamer.
// Both sockets stay on loopback. Media takes a direct WebRTC path or authenticated TURN.
const http = require('node:http');
const crypto = require('node:crypto');
const { loadInfrastructure, assertRuntime } = require('./bootstrap.cjs');

function readConfiguration(env = process.env) {
  let origin;
  try { origin = new URL(env.AVATAR_PUBLIC_ORIGIN || ''); }
  catch { throw new Error('AVATAR_PUBLIC_ORIGIN must be an HTTPS origin.'); }
  if (origin.protocol !== 'https:' || origin.pathname !== '/' || origin.search || origin.hash
      || origin.username || origin.password || env.AVATAR_PUBLIC_ORIGIN.trim() !== env.AVATAR_PUBLIC_ORIGIN) {
    throw new Error('AVATAR_PUBLIC_ORIGIN must be an HTTPS origin.');
  }
  const turnHost = env.AVATAR_TURN_HOST;
  const turnSecret = env.AVATAR_TURN_SECRET;
  if (!turnHost || !/^[a-zA-Z0-9.-]+$/.test(turnHost) || !turnSecret || turnSecret.length < 32) {
    throw new Error('Configure a TURN host and a private TURN shared secret.');
  }
  const relayMode = env.AVATAR_FORCE_RELAY || 'false';
  if (!['true', 'false'].includes(relayMode)) throw new Error('AVATAR_FORCE_RELAY must be true or false.');
  return { origin: origin.origin, turnHost, turnSecret, forceRelay: relayMode === 'true',
    logDir: env.AVATAR_LOG_DIR || '/var/lib/ai-interviewer-pixel/logs' };
}

function createPeerOptions(config) {
  const username = `${Math.floor(Date.now() / 1000) + 3600}:avatar-${crypto.randomBytes(6).toString('hex')}`;
  const credential = crypto.createHmac('sha1', config.turnSecret).update(username).digest('base64');
  return {
    iceServers: [
      { urls: [`stun:${config.turnHost}:3478`] },
      { urls: [`turn:${config.turnHost}:3478?transport=udp`, `turn:${config.turnHost}:3478?transport=tcp`], username, credential },
    ],
    iceTransportPolicy: config.forceRelay ? 'relay' : 'all',
  };
}

function main() {
  assertRuntime();
  const config = readConfiguration();
  const { SignallingServer, InitLogging } = loadInfrastructure();
  // Epic debug-level config messages contain ICE credentials. Keep both outputs at warning.
  InitLogging({ logDir: config.logDir, logLevelConsole: 'warning', logLevelFile: 'warning' });
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify({ service: 'interviewer-signalling', protocol: 'UE5.8' }));
  });
  new SignallingServer({
    httpServer: server,
    streamerPort: 8888,
    streamerWsOptions: { host: '127.0.0.1', maxPayload: 1024 * 1024 },
    playerWsOptions: {
      maxPayload: 1024 * 1024,
      verifyClient: ({ origin: browserOrigin }) => browserOrigin === config.origin,
    },
    peerOptions: {},
    peerOptionsProvider: () => createPeerOptions(config),
    maxSubscribers: 1,
    playerKeepaliveTimeout: 30000,
  });
  server.listen(8889, '127.0.0.1', () => console.log('Private interviewer signalling is ready on loopback.'));
  function shutdown() { server.close(); process.exit(0); }
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

if (require.main === module) main();
module.exports = { readConfiguration, createPeerOptions };
