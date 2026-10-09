// Build only the two signalling workspaces, using the tested upstream commit and npm lock.
// Generated sources/dependencies stay outside Git; no engine checkout or tarball is required.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const REPOSITORY = 'https://github.com/EpicGamesExt/PixelStreamingInfrastructure.git';
const REVISION = '6b8cfb460bda09703e85178f1f77aa6faec9e890';
const INFRASTRUCTURE = path.join(__dirname, '.infrastructure');
const BUILD_RECEIPT = '.interviewer-build.json';

function run(command, args, cwd, capture = false) {
  const result = spawnSync(command, args, {
    cwd, stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit', encoding: 'utf8', shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args[0]} failed (${result.status}).`);
  return capture ? result.stdout.trim() : '';
}

function assertRuntime(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  if (!((major === 22 && minor >= 14) || major === 24)) {
    throw new Error('Use Node.js 22.14 or newer in the Node 22 LTS series, or Node 24 LTS.');
  }
}

function assertLibrary(library) {
  if (typeof library.SignallingServer !== 'function'
      || typeof library.SignallingServer.prototype.getPeerOptions !== 'function'
      || typeof library.InitLogging !== 'function') {
    throw new Error('The signalling build lacks the required per-peer TURN configuration hook.');
  }
  return library;
}

function loadInfrastructure(directory = INFRASTRUCTURE) {
  let receipt;
  try { receipt = JSON.parse(fs.readFileSync(path.join(directory, BUILD_RECEIPT), 'utf8')); }
  catch { throw new Error('Build the pinned signalling dependency first: npm run bootstrap.'); }
  if (receipt.revision !== REVISION) throw new Error('The signalling dependency has the wrong revision; run npm run bootstrap.');
  return assertLibrary(require(path.join(directory, 'Signalling')));
}

function bootstrap() {
  assertRuntime();
  if (fs.existsSync(INFRASTRUCTURE) && fs.lstatSync(INFRASTRUCTURE).isSymbolicLink()) {
    throw new Error('The infrastructure build directory must not be a symbolic link.');
  }
  if (!fs.existsSync(path.join(INFRASTRUCTURE, '.git'))) {
    if (fs.existsSync(INFRASTRUCTURE)) throw new Error('The build directory already exists without a Git checkout.');
    fs.mkdirSync(INFRASTRUCTURE);
    run('git', ['init', '--quiet'], INFRASTRUCTURE);
    run('git', ['remote', 'add', 'origin', REPOSITORY], INFRASTRUCTURE);
    run('git', ['-c', 'protocol.file.allow=never', 'fetch', '--depth=1', 'origin', REVISION], INFRASTRUCTURE);
    run('git', ['checkout', '--quiet', '--detach', 'FETCH_HEAD'], INFRASTRUCTURE);
  }
  const revision = run('git', ['rev-parse', 'HEAD'], INFRASTRUCTURE, true);
  const changes = run('git', ['status', '--porcelain', '--untracked-files=no'], INFRASTRUCTURE, true);
  if (revision !== REVISION || changes) throw new Error('Refusing to build a different or modified upstream checkout.');
  // Remove the success receipt before rebuilding so a failed build cannot look ready.
  fs.rmSync(path.join(INFRASTRUCTURE, BUILD_RECEIPT), { force: true });
  // On Windows invoke npm through its JS CLI, without shell interpolation or cmd quoting.
  const npmCli = process.env.npm_execpath;
  if (!npmCli || !fs.existsSync(npmCli)) throw new Error('Start this installer using npm run bootstrap.');
  const npm = (args) => run(process.execPath, [npmCli, ...args], INFRASTRUCTURE);
  npm(['ci', '--workspace', 'Common', '--workspace', 'Signalling', '--include-workspace-root',
    '--ignore-scripts', '--no-audit', '--no-fund']);
  // Generated protocol TS is included in the pinned sources; protoc is unnecessary.
  npm(['run', 'build:cjs', '--workspace', 'Common']);
  npm(['run', 'build:cjs', '--workspace', 'Signalling']);
  assertLibrary(require(path.join(INFRASTRUCTURE, 'Signalling')));
  fs.writeFileSync(path.join(INFRASTRUCTURE, BUILD_RECEIPT), `${JSON.stringify({ revision: REVISION })}\n`);
  console.log(`UE 5.8 signalling built from ${REVISION}.`);
}

if (require.main === module) {
  try { bootstrap(); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}

module.exports = { REPOSITORY, REVISION, assertRuntime, assertLibrary, loadInfrastructure };
