/**
 * Responsibilities: Verify connection cancellation, session replacement and complete player cleanup.
 * Implementation: Execute the real coordinators with deferred module/config replies and fake media/transport boundaries.
 * Constraints: No browser, microphone, network or model requests are made.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { validateAvatarSignallingUrl } from "../src/avatar-url-policy.js";
import { loadAvatarConfiguration } from "../src/avatar-configuration.js";

const voiceSource = readFileSync(new URL("../../interview-voice.js", import.meta.url), "utf8")
  .replace('import { SpeechCapture } from "./speech-capture.js";', "")
  .replace('await import("/stream-demo/pixel-player.js")', "await loadModule()")
  .replace("export class InterviewVoice", "class InterviewVoice");
const playerSource = readFileSync(new URL("../src/pixel-player.js", import.meta.url), "utf8")
  .replace('import { Config, PixelStreaming } from "@epicgames-ps/lib-pixelstreamingfrontend-ue5.8";', "")
  .replace('import { validateAvatarSignallingUrl } from "./avatar-url-policy.js";', "")
  .replace('export { PresentationController } from "./presentation-controller.js";', "")
  .replace('export { loadAvatarConfiguration } from "./avatar-configuration.js";', "")
  .replace("export class AvatarPlayer", "class AvatarPlayer");

/** Retain resolution controls to simulate responses arriving after interview termination. */
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

/** Execute the real voice coordinator, replacing only the external module and connection resources. */
function voiceHarness({ deferModule = false } = {}) {
  const elements = new Map(), configurations = [], players = [], presentations = [];
  const moduleReply = deferred();
  const configuration = { enabled: true, signalling_url: "wss://example.com/ws/avatar/" };
  let moduleRequests = 0;
  class Player {
    constructor(_container, onEvent, onStatus) { this.onEvent = onEvent; this.onStatus = onStatus; this.closes = 0; players.push(this); }
    connect(url) { this.url = url; }
    send() { return true; }
    close() { ++this.closes; this.onEvent({ type: "avatar_disconnected" }); this.onStatus("obsolete close"); }
  }
  class Presentation {
    constructor() { this.closes = 0; presentations.push(this); }
    setActive() {}
    setState() {}
    close() { ++this.closes; }
  }
  const bundle = { AvatarPlayer: Player, PresentationController: Presentation,
    loadAvatarConfiguration(options) {
      const response = deferred(); configurations.push({ ...response, signal: options.signal });
      return response.promise;
    } };
  const context = { AbortController, location: { protocol: "https:" },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: "", textContent: "", checked: false });
      return elements.get(id);
    } }, window: { addEventListener() {} },
    loadModule() { ++moduleRequests; return deferModule ? moduleReply.promise : Promise.resolve(bundle); },
    clearInterval() {}, clearTimeout() {}, console };
  const Voice = vm.runInNewContext(`${voiceSource}\nInterviewVoice`, context);
  const voice = new Voice(() => assert.fail("Connection work must not submit an interview answer"));
  voice.message("Ready");
  return { voice, elements, configurations, players, presentations, configuration, bundle, moduleReply,
    get moduleRequests() { return moduleRequests; },
    async flush() { for (let index = 0; index < 8; ++index) await Promise.resolve(); } };
}

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

test("interview-end cancellation aborts the real configuration loader promptly", async () => {
  const controller = new AbortController();
  let pendingSignal;
  const pending = loadAvatarConfiguration({ signal: controller.signal, pageUrl: "https://example.com/agent/",
    fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
      pendingSignal = signal;
      signal.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")), { once: true });
    }) });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(pendingSignal.aborted, true);
});

/** Model Epic's public/protected player and media interfaces, including its detached audio element. */
function playerHarness() {
  const streams = [], events = [], statuses = [], timers = new Map();
  let serial = 0;
  class Config { constructor(options) { this.options = options; } }
  class PixelStreaming {
    constructor() {
      this.listeners = new Map(); this.disconnects = 0; this.interactions = [];
      const media = () => ({ srcObject: {}, paused: false, removed: false,
        pause() { this.paused = true; } });
      this.video = media(); this.audio = media();
      this._webRtcController = { videoPlayer: { getVideoElement: () => this.video },
        streamController: { audioElement: this.audio }, destroyVideoPlayer: () => {
          for (const node of [this.video, this.audio]) { node.srcObject = null; node.removed = true; }
        } };
      streams.push(this);
    }
    addResponseEventListener(_name, callback) { this.response = callback; }
    addEventListener(type, callback) { this.listeners.set(type, callback); }
    connect() {}
    play() {}
    emitUIInteraction(message) { this.interactions.push(message); return true; }
    disconnect() { ++this.disconnects; this.emit("webRtcDisconnected"); }
    emit(type, payload) { this.listeners.get(type)?.(payload); }
  }
  const context = { Config, PixelStreaming, validateAvatarSignallingUrl,
    window: { location: { href: "https://example.com/agent/" } },
    setInterval(callback) { const id = ++serial; timers.set(id, callback); return id; },
    clearInterval(id) { timers.delete(id); } };
  const Player = vm.runInNewContext(`${playerSource}\nAvatarPlayer`, context);
  const player = new Player({ querySelector: () => streams.at(-1)?.video },
    (event) => events.push(event), (status) => statuses.push(status));
  return { player, streams, events, statuses, timers };
}

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
