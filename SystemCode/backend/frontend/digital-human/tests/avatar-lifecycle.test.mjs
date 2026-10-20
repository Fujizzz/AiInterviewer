/**
 * @module avatar-lifecycle-test
 * Responsibilities: Verify connection cancellation, session replacement and complete player cleanup.
 * Implementation: Execute the real coordinators with deferred module/config replies and fake media/transport boundaries; bind the actual speech playback exports in the VM.
 * Related Modules: interview-voice.js, speech-playback.js, avatar-configuration.js and pixel-player.js; no browser, microphone, network or model requests are made.
 * Declaration Index:
 * - deferred: Retain resolution controls for deliberately late responses.
 * - deferred.callback1: Capture deferred completion controls.
 * - voiceHarness: Execute the real voice coordinator with controllable renderer resources.
 * - voiceHarness.Player: Observe renderer ownership and stale close events.
 * - voiceHarness.Player.constructor: Store renderer callbacks and register the fixture.
 * - voiceHarness.Player.connect: Record the selected endpoint.
 * - voiceHarness.Player.send: Accept renderer commands in the fixture.
 * - voiceHarness.Player.close: Expose cleanup count and obsolete callbacks.
 * - voiceHarness.Presentation: Observe presentation resource cleanup.
 * - voiceHarness.Presentation.constructor: Register an independent presentation.
 * - voiceHarness.Presentation.setActive: Accept activity changes without rendering.
 * - voiceHarness.Presentation.setState: Accept presentation state without rendering.
 * - voiceHarness.Presentation.close: Count presentation cleanup.
 * - voiceHarness.bundle.loadAvatarConfiguration: Hold a configuration request for explicit completion.
 * - voiceHarness.context.document.getElementById: Supply stable minimal controls for the real coordinator.
 * - voiceHarness.context.window.addEventListener: Accept page listener registration.
 * - voiceHarness.context.loadModule: Control renderer module availability.
 * - voiceHarness.context.clearInterval: Accept interval cleanup in a connection-only fixture.
 * - voiceHarness.context.clearTimeout: Accept timeout cleanup in a connection-only fixture.
 * - voiceHarness.callback1: Reject unexpected answer submission.
 * - voiceHarness.object2.moduleRequests.get: Expose module-load count without copying stale state.
 * - voiceHarness.object2.flush: Advance the existing bounded promise chain.
 * - callback1: Verify termination fences an unresolved renderer import.
 * - callback2: Verify session replacement ignores old replies and callbacks.
 * - callback3: Verify connection deduplication and silent late cancellation failures.
 * - callback4: Verify the real configuration loader forwards cancellation.
 * - callback4.object1.fetchImpl: Model an abort-aware pending fetch.
 * - callback4.object1.fetchImpl.callback1: Observe and reject cancellation.
 * - callback4.object1.fetchImpl.callback1.callback1: Deliver the expected fetch abort error.
 * - playerHarness: Execute the real player adapter with observable transport and media.
 * - playerHarness.Config: Retain Epic configuration supplied by the real adapter.
 * - playerHarness.Config.constructor: Capture adapter options.
 * - playerHarness.PixelStreaming: Model transport events and media cleanup interfaces.
 * - playerHarness.PixelStreaming.constructor: Allocate an observable transport and its media elements.
 * - playerHarness.PixelStreaming.constructor.media: Create a detachable media fixture.
 * - playerHarness.PixelStreaming.constructor.media.object1.pause: Record a pause request.
 * - playerHarness.PixelStreaming.constructor.this._webRtcController.videoPlayer.getVideoElement: Expose the fixture's current video element.
 * - playerHarness.PixelStreaming.constructor.this._webRtcController.destroyVideoPlayer: Simulate Epic media detachment.
 * - playerHarness.PixelStreaming.addResponseEventListener: Retain the response callback.
 * - playerHarness.PixelStreaming.addEventListener: Register one transport handler per event type.
 * - playerHarness.PixelStreaming.connect: Accept a transport connection request.
 * - playerHarness.PixelStreaming.play: Accept media playback requests.
 * - playerHarness.PixelStreaming.emitUIInteraction: Record outgoing renderer interactions.
 * - playerHarness.PixelStreaming.disconnect: Observe transport teardown and its synchronous event.
 * - playerHarness.PixelStreaming.emit: Deliver a controlled transport event.
 * - playerHarness.context.setInterval: Record periodic work without executing it.
 * - playerHarness.context.clearInterval: Observe timer cleanup.
 * - playerHarness.object1.querySelector: Expose the latest transport's video through the container facade.
 * - playerHarness.callback1: Record application events.
 * - playerHarness.callback2: Record application status messages.
 * - callback5: Verify complete cleanup and obsolete-event fencing across reconnect.
 * - callback6: Verify current connection failure releases media and reports one disconnect.
 * Variable Index:
 * - voiceSource: Real voice coordinator adapted to VM execution with controlled renderer imports.
 * - playerSource: Real player adapter adapted to VM execution with controlled Epic transport.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { validateAvatarSignallingUrl } from "../src/avatar-url-policy.js";
import { loadAvatarConfiguration } from "../src/avatar-configuration.js";
import { PcmStreamPlayer, readSpeechStream } from "../../speech-playback.js";

const voiceSource = readFileSync(new URL("../../interview-voice.js", import.meta.url), "utf8")
  .replace('import { SpeechCapture } from "./speech-capture.js";', "")
  .replace('import { PcmStreamPlayer, readSpeechStream } from "./speech-playback.js";', "")
  .replace('await import("/stream-demo/pixel-player.js")', "await loadModule()")
  .replace("export class InterviewVoice", "class InterviewVoice");
const playerSource = readFileSync(new URL("../src/pixel-player.js", import.meta.url), "utf8")
  .replace('import { Config, PixelStreaming } from "@epicgames-ps/lib-pixelstreamingfrontend-ue5.8";', "")
  .replace('import { validateAvatarSignallingUrl } from "./avatar-url-policy.js";', "")
  .replace('export { PresentationController } from "./presentation-controller.js";', "")
  .replace('export { loadAvatarConfiguration } from "./avatar-configuration.js";', "")
  .replace("export class AvatarPlayer", "class AvatarPlayer");

/** Functionality: Retain resolution controls for deliberately late responses.
 * Inputs: None. Outputs: Promise and its resolve/reject functions.
 * Logic: Capture the executor controls synchronously. Constraints: Tests decide when to settle.
 */
function deferred() {
  let resolve, reject;
  const promise = new Promise(
    /** Functionality: Capture deferred completion controls.
     * Inputs: Promise resolve/reject callbacks yes/no. Outputs: Assigns enclosing controls.
     * Logic: Save both callbacks without calling them. Constraints: No automatic completion.
     */
    (yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

/** Functionality: Execute the real voice coordinator with controllable renderer resources.
 * Inputs: Optional deferModule flag, false by default. Outputs: Coordinator and observable fixtures.
 * Logic: Bind actual playback exports, defer configuration replies and record resource ownership.
 * Constraints: These connection tests never capture speech or invoke Web Audio; microphone imports remain unused.
 */
function voiceHarness({ deferModule = false } = {}) {
  const elements = new Map(), configurations = [], players = [], presentations = [];
  const moduleReply = deferred();
  const configuration = { enabled: true, signalling_url: "wss://example.com/ws/avatar/" };
  let moduleRequests = 0;
  /** Functionality: Observe renderer ownership and stale close events.
   * Logic: Record connections and synchronously emit callbacks on close. Constraints: No transport.
   */
  class Player {
    /** Functionality: Store renderer callbacks and register the fixture.
     * Inputs: Unused container, event/status handlers. Outputs: Initialized callbacks and zero closes.
     * Logic: Append this instance to players. Constraints: Does not create a real player.
     */
    constructor(_container, onEvent, onStatus) { this.onEvent = onEvent; this.onStatus = onStatus; this.closes = 0; players.push(this); }
    /** Functionality: Record the selected endpoint.
     * Inputs: URL. Outputs: Stored url. Logic: Assign directly. Constraints: No socket is opened.
     */
    connect(url) { this.url = url; }
    /** Functionality: Accept renderer commands in the fixture.
     * Inputs: None read. Outputs: True. Logic: Constant success. Constraints: No delivery is simulated.
     */
    send() { return true; }
    /** Functionality: Expose cleanup count and obsolete callbacks.
     * Inputs: Saved handlers. Outputs: Incremented closes and disconnect/status notifications.
     * Logic: Invoke both handlers synchronously. Constraints: Coordinator must fence stale callbacks.
     */
    close() { ++this.closes; this.onEvent({ type: "avatar_disconnected" }); this.onStatus("obsolete close"); }
  }
  /** Functionality: Observe presentation resource cleanup.
   * Logic: Count close calls while accepting UI updates. Constraints: No rendering.
   */
  class Presentation {
    /** Functionality: Register an independent presentation.
     * Inputs: None read. Outputs: Zero closes and a fixture entry.
     * Logic: Append this instance. Constraints: No DOM allocation.
     */
    constructor() { this.closes = 0; presentations.push(this); }
    /** Functionality: Accept activity changes without rendering.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: Activity is outside these assertions.
     */
    setActive() {}
    /** Functionality: Accept presentation state without rendering.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: State visuals are not tested.
     */
    setState() {}
    /** Functionality: Count presentation cleanup.
     * Inputs: Instance closes. Outputs: Incremented count.
     * Logic: Add one per call. Constraints: Idempotence must be provided by the coordinator.
     */
    close() { ++this.closes; }
  }
  const bundle = { AvatarPlayer: Player, PresentationController: Presentation,
    /** Functionality: Hold a configuration request for explicit completion.
     * Inputs: Options containing the caller's AbortSignal. Outputs: Pending promise.
     * Logic: Retain signal and deferred controls. Constraints: Deliberately permits replies after abort.
     */
    loadAvatarConfiguration(options) {
      const response = deferred(); configurations.push({ ...response, signal: options.signal });
      return response.promise;
    } };
  const context = { AbortController, PcmStreamPlayer, readSpeechStream, location: { protocol: "https:" },
    document: {
      /** Functionality: Supply stable minimal controls for the real coordinator.
       * Inputs: Element ID. Outputs: Existing or newly allocated control object.
       * Logic: Cache by ID. Constraints: No native DOM behavior is simulated.
       */
      getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: "", textContent: "", checked: false });
      return elements.get(id);
    } }, window: {
      /** Functionality: Accept page listener registration.
       * Inputs: None read. Outputs: None. Logic: No operation. Constraints: Tests call lifecycle methods directly.
       */
      addEventListener() {} },
    /** Functionality: Control renderer module availability.
     * Inputs: Enclosing deferModule, moduleReply and bundle. Outputs: Module promise.
     * Logic: Count requests, then return deferred or resolved bundle. Constraints: No dynamic network import.
     */
    loadModule() { ++moduleRequests; return deferModule ? moduleReply.promise : Promise.resolve(bundle); },
    /** Functionality: Accept interval cleanup in a connection-only fixture.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: This harness schedules no intervals.
     */
    clearInterval() {},
    /** Functionality: Accept timeout cleanup in a connection-only fixture.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: This harness schedules no timeouts.
     */
    clearTimeout() {}, console };
  const Voice = vm.runInNewContext(`${voiceSource}\nInterviewVoice`, context);
  const voice = new Voice(
    /** Functionality: Reject unexpected answer submission.
     * Inputs: None read. Outputs: Assertion failure.
     * Logic: Fail if the coordinator invokes its answer callback. Constraints: Connection work must not submit.
     */
    () => assert.fail("Connection work must not submit an interview answer"));
  voice.message("Ready");
  return { voice, elements, configurations, players, presentations, configuration, bundle, moduleReply,
    /** Functionality: Expose module-load count without copying stale state.
     * Inputs: Enclosing moduleRequests. Outputs: Current count.
     * Logic: Read at access time. Constraints: Read-only observation.
     */
    get moduleRequests() { return moduleRequests; },
    /** Functionality: Advance the existing bounded promise chain.
     * Inputs: None. Outputs: Resolved promise after eight microtask turns.
     * Logic: Await resolved promises. Constraints: Does not settle deferred requests or advance real timers.
     */
    async flush() { for (let index = 0; index < 8; ++index) await Promise.resolve(); } };
}

/** Functionality: Verify termination fences an unresolved renderer import.
 * Inputs: Deferred-module voice fixture. Outputs: Assertions on resources and coordinator state.
 * Logic: Disconnect before resolving the import. Constraints: No configuration or stream may be created.
 */
test("interview termination during module import prevents any late configuration or stream", async () => {
  const harness = voiceHarness({ deferModule: true });
  const pending = harness.voice.connectAvatar();
  harness.voice.disconnectAvatar();
  harness.moduleReply.resolve(harness.bundle);
  await pending;
  assert.equal(harness.configurations.length, 0);
  assert.equal(harness.players.length, 0);
  assert.equal(harness.voice.avatarConnecting, false);
  assert.equal(harness.voice.closed, false);
});

/** Functionality: Verify session replacement ignores old replies and callbacks.
 * Inputs: Deferred configuration fixture. Outputs: Assertions on ownership, locks and displayed status.
 * Logic: Resolve cancelled and current requests separately, then emit obsolete player events.
 * Constraints: The cancelled provider deliberately ignores abort; no real connections are opened.
 */
test("new interview can connect while old configuration is pending, without stale lock or events", async () => {
  const harness = voiceHarness();
  const old = harness.voice.connectAvatar();
  await harness.flush();
  harness.voice.disconnectAvatar();
  assert.equal(harness.configurations[0].signal.aborted, true);
  const current = harness.voice.connectAvatar();
  await harness.flush();
  harness.configurations[0].resolve(harness.configuration); // A noncooperative fetch must still be fenced.
  await old;
  assert.equal(harness.voice.avatarConnecting, true);
  assert.equal(harness.players.length, 0);
  harness.configurations[1].resolve(harness.configuration);
  await current;
  assert.equal(harness.players.length, 1);
  assert.equal(harness.voice.avatarConnecting, false);
  assert.equal(harness.voice.avatarAbort, null);
  const previousPlayer = harness.players[0];
  harness.voice.disconnectAvatar();
  const next = harness.voice.connectAvatar();
  await harness.flush();
  harness.configurations[2].resolve(harness.configuration);
  await next;
  const currentPlayer = harness.voice.player;
  const before = harness.elements.get("voice-status").textContent;
  previousPlayer.onStatus("late previous-session error");
  previousPlayer.onEvent({ type: "avatar_stats", fps: 1 });
  assert.equal(harness.voice.player, currentPlayer);
  assert.equal(harness.elements.get("voice-status").textContent, before);
  assert.equal(harness.elements.get("avatar-metrics").textContent, "");
  assert.equal(previousPlayer.closes, 1);
  harness.voice.disconnectAvatar();
});

/** Functionality: Verify connection deduplication and silent late cancellation failures.
 * Inputs: One unresolved configuration fixture. Outputs: Request-count and status assertions.
 * Logic: Request twice, disconnect, then reject the obsolete request.
 * Constraints: A cancelled failure must not overwrite terminal interview status.
 */
test("concurrent connect requests are deduplicated and late cancelled failures remain silent", async () => {
  const harness = voiceHarness();
  const first = harness.voice.connectAvatar();
  await harness.voice.connectAvatar();
  await harness.flush();
  assert.equal(harness.moduleRequests, 1);
  harness.voice.disconnectAvatar();
  harness.elements.get("voice-status").textContent = "Interview complete";
  harness.configurations[0].reject(new Error("late failure"));
  await first;
  assert.equal(harness.elements.get("voice-status").textContent, "Interview complete");
  assert.equal(harness.voice.avatarConnecting, false);
});

/** Functionality: Verify the real configuration loader forwards cancellation.
 * Inputs: AbortController and a fetch substitute. Outputs: AbortError and aborted-signal assertions.
 * Logic: Abort immediately after starting the loader. Constraints: No HTTP request is issued.
 */
test("interview-end cancellation aborts the real configuration loader promptly", async () => {
  const controller = new AbortController();
  let pendingSignal;
  const pending = loadAvatarConfiguration({ signal: controller.signal, pageUrl: "https://example.com/agent/",
    /** Functionality: Model an abort-aware pending fetch.
     * Inputs: Unused URL and request signal. Outputs: Pending promise.
     * Logic: Install only a rejection path. Constraints: No successful response or real request.
     */
    fetchImpl: (_url, { signal }) => new Promise(
      /** Functionality: Observe and reject cancellation.
       * Inputs: Unused resolver and rejection callback. Outputs: Saved signal and abort listener.
       * Logic: Bind one abort event. Constraints: Requires the signal to be initially un-aborted.
       */
      (_resolve, reject) => {
      pendingSignal = signal;
      /** Functionality: Deliver the expected fetch abort error.
       * Inputs: Enclosing reject callback. Outputs: Rejected promise.
       * Logic: Create an AbortError DOMException. Constraints: Listener runs once.
       */
      signal.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")), { once: true });
    }) });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(pendingSignal.aborted, true);
});

/** Functionality: Execute the real player adapter with observable transport and media.
 * Inputs: None. Outputs: Adapter, streams, events, statuses and timer map.
 * Logic: Model Epic interfaces including detached audio; manually dispatch transport callbacks.
 * Constraints: No browser, network or actual interval execution; cannot verify native UE behavior.
 */
function playerHarness() {
  const streams = [], events = [], statuses = [], timers = new Map();
  let serial = 0;
  /** Functionality: Retain Epic configuration supplied by the real adapter.
   * Logic: Store constructor input. Constraints: No settings validation is simulated.
   */
  class Config {
    /** Functionality: Capture adapter options.
     * Inputs: Options object. Outputs: Stored options.
     * Logic: Preserve object identity. Constraints: No defaults are applied.
     */
    constructor(options) { this.options = options; } }
  /** Functionality: Model transport events and media cleanup interfaces.
   * Logic: Store listeners and expose mutable media state. Constraints: No WebRTC implementation.
   */
  class PixelStreaming {
    /** Functionality: Allocate an observable transport and its media elements.
     * Inputs: None read. Outputs: Listener map, counters, media and protected controller facade.
     * Logic: Register the instance and provide cleanup hooks. Constraints: No tracks or sockets are created.
     */
    constructor() {
      this.listeners = new Map(); this.disconnects = 0; this.interactions = [];
      /** Functionality: Create a detachable media fixture.
       * Inputs: None. Outputs: Fresh playing/attached state and pause method.
       * Logic: Allocate independent mutable fields. Constraints: srcObject is a placeholder, not a MediaStream.
       */
      const media = () => ({ srcObject: {}, paused: false, removed: false,
        /** Functionality: Record a pause request.
         * Inputs: Instance state. Outputs: paused=true.
         * Logic: Assign the flag. Constraints: No audio or video is played.
         */
        pause() { this.paused = true; } });
      this.video = media(); this.audio = media();
      this._webRtcController = { videoPlayer: {
        /** Functionality: Expose the fixture's current video element.
         * Inputs: Enclosing transport video. Outputs: Same media object.
         * Logic: Return the stored reference. Constraints: No DOM lookup.
         */
        getVideoElement: () => this.video },
        streamController: { audioElement: this.audio },
        /** Functionality: Simulate Epic media detachment.
         * Inputs: Enclosing video/audio fixtures. Outputs: Cleared streams and removed flags.
         * Logic: Update both media nodes. Constraints: Pausing is the adapter's separate responsibility.
         */
        destroyVideoPlayer: () => {
          for (const node of [this.video, this.audio]) { node.srcObject = null; node.removed = true; }
        } };
      streams.push(this);
    }
    /** Functionality: Retain the response callback.
     * Inputs: Unused registration name and callback. Outputs: Stored response handler.
     * Logic: Assign the reference. Constraints: Tests deliver responses explicitly.
     */
    addResponseEventListener(_name, callback) { this.response = callback; }
    /** Functionality: Register one transport handler per event type.
     * Inputs: Event type and callback. Outputs: Updated listener map.
     * Logic: Replace the mapped handler. Constraints: No event fires during registration.
     */
    addEventListener(type, callback) { this.listeners.set(type, callback); }
    /** Functionality: Accept a transport connection request.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: Tests emit connection events manually.
     */
    connect() {}
    /** Functionality: Accept media playback requests.
     * Inputs: None read. Outputs: None. Logic: No operation. Constraints: No browser autoplay is exercised.
     */
    play() {}
    /** Functionality: Record outgoing renderer interactions.
     * Inputs: Message. Outputs: Stored message and true.
     * Logic: Append without serialization. Constraints: No data-channel delivery is simulated.
     */
    emitUIInteraction(message) { this.interactions.push(message); return true; }
    /** Functionality: Observe transport teardown and its synchronous event.
     * Inputs: Disconnect counter and saved event listener. Outputs: Incremented count and disconnect callback.
     * Logic: Emit after counting. Constraints: Tests verify adapter fencing and idempotence.
     */
    disconnect() { ++this.disconnects; this.emit("webRtcDisconnected"); }
    /** Functionality: Deliver a controlled transport event.
     * Inputs: Event type and optional payload. Outputs: Handler effects, if registered.
     * Logic: Invoke the mapped callback. Constraints: Missing listeners are allowed.
     */
    emit(type, payload) { this.listeners.get(type)?.(payload); }
  }
  const context = { Config, PixelStreaming, validateAvatarSignallingUrl,
    window: { location: { href: "https://example.com/agent/" } },
    /** Functionality: Record periodic work without executing it.
     * Inputs: Callback. Outputs: Unique timer ID and map entry.
     * Logic: Increment serial and retain callback. Constraints: No wall-clock timer is started.
     */
    setInterval(callback) { const id = ++serial; timers.set(id, callback); return id; },
    /** Functionality: Observe timer cleanup.
     * Inputs: Timer ID. Outputs: Removed map entry.
     * Logic: Delete by ID. Constraints: Unknown IDs have no effect.
     */
    clearInterval(id) { timers.delete(id); } };
  const Player = vm.runInNewContext(`${playerSource}\nAvatarPlayer`, context);
  const player = new Player({
    /** Functionality: Expose the latest transport's video through the container facade.
     * Inputs: None read. Outputs: Latest video or undefined.
     * Logic: Inspect the last registered stream. Constraints: No selector parsing or native DOM.
     */
    querySelector: () => streams.at(-1)?.video },
    /** Functionality: Record application events.
     * Inputs: Event. Outputs: Appended event and array length.
     * Logic: Push directly. Constraints: No downstream handlers are invoked.
     */
    (event) => events.push(event),
    /** Functionality: Record application status messages.
     * Inputs: Status. Outputs: Appended status and array length.
     * Logic: Push directly. Constraints: No display rendering.
     */
    (status) => statuses.push(status));
  return { player, streams, events, statuses, timers };
}

/** Functionality: Verify complete cleanup and obsolete-event fencing across reconnect.
 * Inputs: Real adapter with fake Epic transport and media. Outputs: Resource/state/event assertions.
 * Logic: Close twice, reconnect, then emit old callbacks before the current ready response.
 * Constraints: Verifies ownership and teardown logic only, without WebRTC or browser media.
 */
test("closing a player releases both media elements and suppresses all old callbacks after reconnect", () => {
  const harness = playerHarness();
  const endpoint = "wss://example.com/ws/avatar/";
  harness.player.connect(endpoint);
  const old = harness.streams[0];
  old.emit("webRtcConnected"); old.emit("webRtcConnected");
  assert.equal(harness.timers.size, 1);
  harness.player.close(); harness.player.close();
  assert.equal(old.disconnects, 1);
  for (const media of [old.video, old.audio]) {
    assert.equal(media.paused, true); assert.equal(media.srcObject, null); assert.equal(media.removed, true);
  }
  assert.equal(harness.timers.size, 0);
  assert.equal(harness.events.length, 0);
  harness.player.connect(endpoint);
  const current = harness.player.player;
  const statusCount = harness.statuses.length;
  old.emit("webRtcConnected"); old.emit("webRtcDisconnected"); old.emit("webRtcFailed");
  old.emit("playStreamRejected"); old.emit("statsReceived", {});
  old.response('{"type":"avatar_ready"}');
  assert.equal(harness.player.player, current);
  assert.equal(harness.player.ready, false);
  assert.equal(harness.events.length, 0);
  assert.equal(harness.statuses.length, statusCount);
  assert.equal(harness.timers.size, 0);
  current.response('{"type":"avatar_ready"}');
  assert.equal(harness.player.ready, true);
  harness.player.close();
});

/** Functionality: Verify current connection failure releases media and reports one disconnect.
 * Inputs: Real adapter with fake Epic transport. Outputs: Cleanup and event-count assertions.
 * Logic: Emit webRtcFailed once and inspect current ownership and audio pause state.
 * Constraints: The event is simulated; the test does not contact a renderer.
 */
test("real connection failure releases the current stream and reports one fallback event", () => {
  const harness = playerHarness();
  harness.player.connect("wss://example.com/ws/avatar/");
  const current = harness.streams[0];
  current.emit("webRtcFailed");
  assert.equal(harness.player.player, null);
  assert.equal(current.disconnects, 1);
  assert.equal(current.audio.paused, true);
  assert.equal(harness.events.length, 1);
  assert.equal(harness.events[0].type, "avatar_disconnected");
});
