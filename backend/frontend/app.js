/**
 * @module app
 * Responsibilities: Entry point for streaming diagnostics; coordinates StreamClient, media capture, and DemoView through a complete test lifecycle.
 * Implementation: Run one explicit test at a time, localize status copy, and release connection and media resources on completion or page exit.
 * Related Modules: media.js captures and echoes media, stream-client.js implements the transport protocol, and view.js owns DOM and playback resources.
 * Declaration Index:
 * - appText: Read current-locale diagnostic copy without changing the test protocol.
 * - registerControls: Store callbacks that stop and release the active media capture.
 * - runTest: Serialize connection, test execution, and result publication; always clear page-running state.
 * - runTest.object1.onProgress: Forward verified chunk progress to the view without recalculating counters.
 * - runTest.object1.onError: Stop active capture after a connection failure so the existing final-chunk drain can run.
 * - testPing: Verify a ping/pong round trip and show locally measured latency.
 * - testBinary: Send fixed-size binary chunks and verify the echoed payloads.
 * - testBinary.callback1: Generate deterministic bytes from the offset and chunk number.
 * - testBinary.callback2: Preserve the fixed 50 ms interval between test chunks.
 * - testMedia: Delegate capture, chunk echo, and playback to the media module.
 * - dispose: Cancel the active test and release media and playback resources.
 * - callback1: Route the ping control to the shared runTest lifecycle.
 * - callback2: Route the binary test through the shared connection and cleanup lifecycle.
 * - callback3: Start the shared lifecycle with the selected media mode.
 * - callback3.callback1: Pass the current connection and closed-over media mode to capture.
 * - callback4: Preserve final-chunk draining on stop; page exit releases resources immediately.
 * - callback5: Clear playback and page results only while no test is active.
 * Variable Index:
 * - appText: Localized diagnostic-copy lookup with a key fallback for isolated tests.
 * - view: DemoView instance for the current document.
 * - wsUrl: Same-origin echo URL derived from the current page protocol and host.
 * - client: Active diagnostic connection, or null while idle.
 * - running: Mutex preventing concurrent tests on this page.
 * - stopRecording: Stop callback for the active recorder.
 * - cleanupMedia: Cleanup callback for the active media source.
 *
 * Constraints:
 * stream-client.js owns networking, media.js owns devices and recording, and view.js owns DOM and Blob URLs. Test parameters, timeouts, and failure semantics remain fixed.
 */
import { captureAndEcho } from "./media.js";
import { StreamClient } from "./stream-client.js";
import { DemoView } from "./view.js";

const view = new DemoView(document);
const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/echo/`;
view.element("endpoint").textContent = wsUrl;
/** Read localized diagnostic copy; when i18n.js is absent, return the key used by isolated tests. */
const appText = (key, values = {}) => window.AppI18n?.t(key, values) ?? key;
let client = null;
let running = false;
let stopRecording = null;
let cleanupMedia = null;

/** Register stop and cleanup callbacks for cancellation, connection errors, and pagehide. */
function registerControls(stop, cleanup) {
  stopRecording = stop;
  cleanupMedia = cleanup;
}

/**
 * Functionality: Run one explicitly selected diagnostic test.
 * Inputs: Mode label and test callback receiving the connected StreamClient.
 * Outputs: Promise resolving after the result is shown; returns immediately if another test is active.
 * Logic: Connect, execute the callback, and await finish; finally clear the running lock and client reference.
 * Constraints: Errors close the connection and display failure; no alternate test, implicit retry, or persistence is used.
 */
async function runTest(mode, test) {
  if (running) return;
  running = true;
  view.busy(true);
  view.reset();
  view.status(appText("status_connecting"));
  client = new StreamClient(wsUrl, {
    /** Forward verified chunk progress to the view; do not recalculate counters. */
    onProgress: (progress) => view.progress(progress),
    /** Stop active capture after connection failure and preserve the existing final-chunk drain. */
    onError: () => stopRecording?.(),
  });
  try {
    const hello = await client.connect();
    view.log(`${appText("status_connection")} ${hello.connection_id} · ${mode}`);
    view.status(appText("status_testing"));
    await test(client);
    view.success(mode, await client.finish());
  } catch (error) {
    client.close();
    view.failure(error);
  } finally {
    client = null;
    running = false;
    view.busy(false);
  }
}

/** Send a uniquely identified ping and measure its matching pong with the local monotonic clock. */
async function testPing(connection) {
  const rtt = await connection.ping();
  view.element("latency").textContent = rtt.toFixed(1);
  view.log(appText("status_pong", { ms: rtt.toFixed(1) }));
}

/** Send twelve deterministic 32 KiB payloads with the existing 50 ms test interval. */
async function testBinary(connection) {
  await connection.start("binary", "application/octet-stream");
  for (let index = 0; index < 12; index += 1) {
    const payload = Uint8Array.from({ length: 32768 }, /** Generate deterministic bytes from each offset and the chunk number. */ (_, offset) => (offset + index) % 256);
    await connection.sendChunk(payload.buffer);
    await new Promise(/** Preserve the fixed 50 ms interval between chunks. */ (resolve) => setTimeout(resolve, 50));
  }
}

/** Delegate media capture and echo; give the view only the fully verified Blob. */
async function testMedia(connection, mode) {
  view.playback(await captureAndEcho(connection, mode, view, registerControls));
}

/** Stop capture and connection and revoke the playback URL on page exit; write no recovery state. */
function dispose() {
  stopRecording?.();
  cleanupMedia?.();
  client?.close();
  view.releasePlayback();
}

/** Route controls through the shared lifecycle; media controls pass an explicit capture mode. */
view.element("ping").onclick = () => runTest("ping", testPing);
/** Start the binary-chunk test through the shared connection and cleanup lifecycle. */
view.element("binary").onclick = () => runTest("binary", testBinary);
for (const mode of ["synthetic", "audio", "video"]) {
  /** Start the shared test lifecycle with this control's selected media mode. */
  view.element(mode).onclick = () => runTest(mode, /** Pass the active connection and selected mode into media capture. */ (connection) => testMedia(connection, mode));
}
/** Stop capture while preserving final-chunk draining; page exit requests immediate resource release. */
view.element("stop").onclick = () => stopRecording?.();
/** Clear playback and page results only while idle. */
view.element("clear").onclick = () => { if (!running) view.clear(); };
window.addEventListener("pagehide", dispose);
