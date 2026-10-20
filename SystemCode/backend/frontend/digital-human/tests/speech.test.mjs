/**
 *
 * @module speech-tests
 * Responsibilities: Verify offline speech, automatic preparation, inactivity and end-finalization contracts.
 * Implementation: Stub DOM, provider/device boundaries and capture startup; drive monotonic deadlines explicitly.
 * Related Modules: InterviewVoice, SpeechCapture, speech-worklet and PCM16Resampler; no real microphone or cloud model is used.
 *
 * Declaration Index:
 * - page: Create minimal DOM and event listener stubs.
 * - page.globalThis.document.getElementById: Create and cache control state on demand.
 * - page.globalThis.window.addEventListener: Register offline page event subscriptions.
 * - page.globalThis.window.dispatchEvent: Dispatch the page's normal controls event to the registered voice coordinator.
 * - readyRenderer: Select the retained WAV protocol with an explicitly ready offline renderer.
 * - readyRenderer.object1.send: Accept control messages without transport or playback.
 * - readyRenderer.object1.close: Accept renderer cleanup without resources.
 * - OfflineQuestionAudio: Simulate question audio without starting a real browser media pipeline.
 * - OfflineQuestionAudio.constructor: Retain an offline URL and a verifiable stopped flag.
 * - OfflineQuestionAudio.play: Resolve startup while leaving completion under the test's control.
 * - OfflineQuestionAudio.pause: Mark this simulated playback stopped.
 * - VoicePlaybackHarness: Drive full initial/replay question lifecycles using deterministic timers and offline media boundaries.
 * - VoicePlaybackHarness.constructor: Save existing globals, then replace only the clock, playback transport and microphone startup.
 * - VoicePlaybackHarness.constructor.globalThis.performance.now: Return deterministic monotonic time.
 * - VoicePlaybackHarness.constructor.SpeechCapture.prototype.start: Count simulated startup without opening a microphone or recognizer.
 * - VoicePlaybackHarness.constructor.callback1: Detect accidental answer submission during playback.
 * - VoicePlaybackHarness.constructor.this.voice.player.send: Record controls and simulate UE channel availability.
 * - VoicePlaybackHarness.constructor.this.voice.player.close: Close the offline transport without resources.
 * - VoicePlaybackHarness.addTimer: Schedule a deterministic timeout or interval callback.
 * - VoicePlaybackHarness.addInterval: Schedule a countdown callback without a real timer.
 * - VoicePlaybackHarness.clearTimer: Cancel one deterministic timer.
 * - VoicePlaybackHarness.advance: Move time forward, running each due callback once to expose deadline transitions.
 * - VoicePlaybackHarness.fetch: Capture TTS requests while allowing deliberately late responses despite cancellation.
 * - VoicePlaybackHarness.fetch.callback1: Retain response and rejection controls without network access.
 * - VoicePlaybackHarness.completeTts: Resolve one TTS boundary with fixed metadata or a fixed public error.
 * - VoicePlaybackHarness.completeTts.object3.json: Return only the fixed offline response body.
 * - VoicePlaybackHarness.flush: Drain Promise callbacks without delaying wall-clock time.
 * - VoicePlaybackHarness.close: Close the tested voice lifecycle before restoring real globals and capture startup.
 * - pendingAvatar: Remove readiness after renderer selection to exercise its existing WAV recovery boundary.
 * - pendingAvatar.harness.voice.player.send: Observe delivered renderer commands without changing the harness channel result.
 * - pendingAvatar.globalThis.Audio: Track fallback media construction while retaining the offline audio lifecycle.
 * - pendingAvatar.globalThis.Audio.constructor: Record this browser fallback instance and its inherited URL.
 * - pendingAvatar.observation.ready: Announce readiness only for the currently attached renderer.
 * - callback1: Verify chunked capture results match full segment resampling results.
 * - callback1.callback1: Generate fixed-sampling-rate sinusoidal test input.
 * - callback2: Verify worklet sends little-endian PCM tail first, then confirms flush, and does not replay input.
 * - callback2.globalThis.AudioWorkletProcessor: Provide a stub base class for worklet with message recording capability.
 * - callback2.globalThis.AudioWorkletProcessor.constructor: Create message recording port.
 * - callback2.globalThis.AudioWorkletProcessor.constructor.this.port.postMessage: Record worklet message send order.
 * - callback2.globalThis.registerProcessor: Save the tested worklet class.
 * - callback2.callback1: Verify output buffer remains silent at all times.
 * - callback2.callback2: Extract message types to verify send order.
 * - callback3: Verify old TTS responses do not trigger a new playback round.
 * - callback3.globalThis.fetch: Provide a manually endable TTS request.
 * - callback3.globalThis.fetch.callback1: Save TTS request completion callback.
 * - callback3.globalThis.Audio: Record browser audio instance creation count.
 * - callback3.globalThis.Audio.constructor: Increment audio creation counter.
 * - callback3.object2.json: Return expired question audio.
 * - callback4: Verify old UE events do not alter current turn and do not cause duplicate browser sound playback.
 * - callback4.voice.player.send: Record UE control messages and simulate successful send.
 * - callback4.voice.player.close: Provide a player close stub requiring no resource cleanup.
 * - callback4.globalThis.fetch: Provide successful TTS response for current question.
 * - callback4.globalThis.fetch.object1.json: Return audio identifier and address for current question.
 * - callback4.globalThis.Audio: Detect unexpected browser duplicate playback.
 * - callback4.globalThis.Audio.constructor: Immediately fail test if browser creates duplicate audio.
 * - callback4.callback1: Check whether speak message has been sent.
 * - callback4.callback2: Check whether stop message has been sent.
 * - callback5: Verify voice answer can still be initiated after TTS quota failure.
 * - callback5.globalThis.fetch: Provide TTS quota insufficient response.
 * - callback5.globalThis.fetch.object1.json: Return fixed public error message.
 * - callback6: Verify late authorization after device rejection or cancellation does not leak audio track.
 * - callback6.object1.value.mediaDevices.getUserMedia: Simulate microphone permission denial.
 * - callback6.callback1: Temporary transcription callback.
 * - callback6.callback2: Final transcription callback.
 * - callback6.callback3: Recognition error callback.
 * - callback6.navigator.mediaDevices.getUserMedia: Provide late device authorization result.
 * - callback6.navigator.mediaDevices.getUserMedia.callback1: Save device authorization completion callback.
 * - callback6.callback4: Cancel session temporary transcription callback.
 * - callback6.callback5: Cancel session final transcription callback.
 * - callback6.callback6: Cancel session recognition error callback.
 * - callback6.object2.getTracks: Return test audio track with verifiable stop status.
 * - callback6.object2.getTracks.object1.stop: Mark late audio track as released.
 * - callback7: New account API requires TTS write requests to carry page token; does not weaken server-side CSRF checks.
 * - callback7.globalThis.fetch: Save client request headers; fix failure to prevent browser audio creation during testing.
 * - callback7.globalThis.fetch.object1.json: Return offline error, prohibiting real vendor calls.
 * - callback8: Verify 10 seconds of preparation and five silent seconds without real microphone access.
 * - callback8.callback1: Observe exactly one final answer submission.
 * - callback8.SpeechCapture.prototype.start: Replace device/provider startup with a recording-state transition only.
 * - callback8.SpeechCapture.prototype.end: Deliver the empty final provider text after automatic flush.
 * - callback9: Verify semantic completion submits once and obsolete recognition callbacks cannot affect the new question.
 * - callback9.callback1: Save final text and optional receipt for protocol assertions.
 * - callback9.SpeechCapture.prototype.start: Simulate ready capture without creating real device or network resources.
 * - callback9.SpeechCapture.prototype.end: Simulate receipt-bound provider finalization.
 * - callback10: Verify early ending consumes final STT rather than partial captions and prevents normal answer submission.
 * - callback10.callback1: Detect accidental normal submission while ending.
 * - callback10.SpeechCapture.prototype.start: Mark the offline capture ready for finalization.
 * - callback10.SpeechCapture.prototype.end: Return final words distinct from the preceding partial subtitles.
 * - callback11: Verify a denied automatic microphone attempt is explicit and is not silently retried.
 * - callback11.SpeechCapture.prototype.start: Reject device startup at the same boundary as browser permission refusal.
 * - callback12: Verify slow synthesis and UE readiness cannot consume or abort the user's preparation time.
 * - callback13: A renderer ready at selection can recover during synthesis without another WAV request.
 * - callback13.callback1: Select only actual speech commands.
 * - callback14: A renderer replaced after selection binds its existing WAV identity to the new presentation.
 * - callback14.voice.presentation.bindUtterance: Record identity binding after presentation configuration finishes.
 * - callback14.voice.presentation.onAvatarEvent: Accept lifecycle events without renderer resources.
 * - callback14.voice.presentation.setState: Accept presentation state without animation.
 * - callback14.voice.presentation.endListeningCapture: Complete a simulated listening observation.
 * - callback14.voice.presentation.clear: Clear simulated presentation state.
 * - callback14.voice.presentation.close: Release the resource-free presentation double.
 * - callback14.callback1: Select speech commands for identity assertions.
 * - callback15: Readiness lost after WAV selection reaches one bounded fallback without duplicate late playback.
 * - callback15.callback1: Detect duplicate renderer speech after fallback.
 * - callback16: Disconnecting after WAV selection wakes its pending request without duplicate fallback resources.
 * - callback16.callback1: Detect speech revived by late readiness.
 * - callback17: Reset/end invalidates an already selected renderer request before late readiness can revive playback.
 * - callback17.callback1: Verify each termination path fences late callbacks independently.
 * - callback17.callback1.callback1: Detect stale renderer speech after termination.
 * - callback18: Verify explicit replay preserves preparation already spent and stale events cannot grant another ten seconds.
 * - callback19: Verify failure and explicit skip paths grant preparation while cancelled or obsolete TTS cannot revive playback.
 * - callback20: Verify fallback audible completion starts preparation, and retired media callbacks cannot touch a new question.
 * - callback21: Verify preparation never replaces independent TTS, UE startup or bounded browser playback deadlines.
 * - callback22: Verify an early-end dialog pauses preparation rather than losing the clock or granting a new full period.
 * - callback23: Verify browser autoplay refusal is a bounded voice fallback, never a blocked microphone preparation.
 * - callback23.OfflineQuestionAudio.prototype.play: Reject only the offline browser playback startup.
 * - callback24: Verify the page's direct choosing-dialog suspension, rather than only the later save/finalization path.
 * - callback25: Verify a long allowed WAV receives a bounded CPU preparation window rather than a premature legacy fallback.
 * - callback26: Verify provider-independent duration metadata uses exact bounds and cannot weaken finite playback protection.
 * Variable Index:
 * None
 *
 */
import test from "node:test";
import assert from "node:assert/strict";
import { PCM16Resampler } from "../../pcm-resampler.js";
import { InterviewVoice } from "../../interview-voice.js";
import { SpeechCapture } from "../../speech-capture.js";

/**
 *  Create minimal DOM and event listener stubs.
 */
function page() {
  const elements = new Map();
  const listeners = new Map();
  globalThis.document = { /**
 *  Create and cache control state on demand.
 */ getElementById(id) {
    if (!elements.has(id)) elements.set(id, { disabled: false, checked: false, textContent: "", value: "" });
    return elements.get(id);
  }};
  globalThis.window = { /**
 *  Register offline page event subscriptions.
 */ addEventListener(type, listener) { listeners.set(type, listener); },
  /** Dispatch the page's normal controls event to the registered voice coordinator. */
  dispatchEvent(event) { listeners.get(event.type)?.(event); return true; } };
  return elements;
}

/** Functionality: Select the retained WAV protocol with an explicitly ready offline renderer.
 * Inputs: None. Outputs: Ready player with inert control and cleanup methods.
 * Logic: Keep renderer-protocol request/error tests separate from real browser PCM tests.
 * Constraints: No transport, media or readiness events are generated.
 */
function readyRenderer() {
  return {
    ready: true,
    /** Accept control messages without transport or playback. */ send() { return true; },
    /** Accept renderer cleanup without resources. */ close() {},
  };
}

/** Simulate question audio without starting a real browser media pipeline. */
class OfflineQuestionAudio {
  /** Retain an offline URL and a verifiable stopped flag. */
  constructor(url) { this.url = url; this.paused = false; }
  /** Resolve startup while leaving completion under the test's control. */
  play() { return Promise.resolve(); }
  /** Mark this simulated playback stopped. */
  pause() { this.paused = true; }
}

/** Drive full initial/replay question lifecycles using deterministic timers and offline media boundaries. */
class VoicePlaybackHarness {
  /** Save existing globals, then replace only the clock, playback transport and microphone startup. */
  constructor() {
    this.original = { performance: globalThis.performance, fetch: globalThis.fetch, Audio: globalThis.Audio,
      document: globalThis.document, window: globalThis.window, setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval,
      captureStart: SpeechCapture.prototype.start };
    this.now = 0; this.nextTimer = 0; this.timers = new Map(); this.requests = []; this.commands = [];
    this.starts = 0; this.answers = 0; this.ueAvailable = true;
    globalThis.performance = { /** Return deterministic monotonic time. */ now: () => this.now };
    globalThis.setTimeout = this.addTimer.bind(this);
    globalThis.setInterval = this.addInterval.bind(this);
    globalThis.clearTimeout = this.clearTimer.bind(this);
    globalThis.clearInterval = this.clearTimer.bind(this);
    globalThis.fetch = this.fetch.bind(this);
    globalThis.Audio = OfflineQuestionAudio;
    page();
    const harness = this;
    /** Count simulated startup without opening a microphone or recognizer. */
    SpeechCapture.prototype.start = async function () { harness.starts++; this.recording = true; };
    this.voice = new InterviewVoice(/** Detect accidental answer submission during playback. */ () => { this.answers++; });
    this.voice.eligible = true;
    this.voice.player = {
      ready: true, // Renderer protocol tests explicitly start with an established channel.
      /** Record controls and simulate UE channel availability. */ send: (message) => { this.commands.push(message); return this.ueAvailable; },
      /** Close the offline transport without resources. */ close() {},
    };
    document.getElementById("voice-enabled").checked = true;
  }
  /** Schedule a deterministic timeout or interval callback. */
  addTimer(callback, delay, repeat = false) {
    const id = ++this.nextTimer;
    this.timers.set(id, { callback, at: this.now + delay, interval: repeat ? delay : 0 });
    return id;
  }
  /** Schedule a countdown callback without a real timer. */
  addInterval(callback, delay) { return this.addTimer(callback, delay, true); }
  /** Cancel one deterministic timer. */
  clearTimer(id) { this.timers.delete(id); }
  /** Move time forward, running each due callback once to expose deadline transitions. */
  advance(milliseconds) {
    this.now += milliseconds;
    for (const [id, timer] of [...this.timers]) {
      if (!this.timers.has(id) || timer.at > this.now) continue;
      if (timer.interval) timer.at = this.now + timer.interval;
      else this.timers.delete(id);
      timer.callback();
    }
  }
  /** Capture TTS requests while allowing deliberately late responses despite cancellation. */
  fetch(_url, options) {
    return new Promise(/** Retain response and rejection controls without network access. */ (resolve, reject) => {
      this.requests.push({ options, resolve, reject });
    });
  }
  /** Resolve one TTS boundary with fixed metadata or a fixed public error. */
  completeTts(index = 0, ok = true, durationMs = undefined) {
    const result = ok ? { utterance_id: `audio-${index}`, audio_url: `/offline-${index}.wav`, generation_ms: 7 }
      : { error: { code: "quota_exhausted", detail: "Offline quota failure" } };
    if (durationMs !== undefined) result.duration_ms = durationMs;
    this.requests[index].resolve({ ok, /** Return only the fixed offline response body. */ json: async () => result });
  }
  /** Drain Promise callbacks without delaying wall-clock time. */
  async flush() { for (let index = 0; index < 8; index++) await Promise.resolve(); }
  /** Close the tested voice lifecycle before restoring real globals and capture startup. */
  close() {
    this.voice.close();
    SpeechCapture.prototype.start = this.original.captureStart;
    for (const [name, value] of Object.entries(this.original)) if (name !== "captureStart") globalThis[name] = value;
  }
}

/** Remove controller readiness during an already selected WAV request.
 * Inputs: Offline playback harness whose speech request has started. Outputs: Command/media observations.
 * Logic: Mark its channel unready and expose an explicit ready event. Constraints: No browser or renderer.
 */
function pendingAvatar(harness) {
  harness.ueAvailable = false;
  harness.voice.player.player = {};
  harness.voice.player.ready = false;
  const observation = { accepted: [], audio: [] };
  const send = harness.voice.player.send;
  /** Observe delivered renderer commands without changing the harness channel result. */
  harness.voice.player.send = (message) => {
    const delivered = send(message);
    if (delivered) observation.accepted.push(message);
    return delivered;
  };
  /** Track fallback media construction while retaining the offline audio lifecycle. */
  globalThis.Audio = class extends OfflineQuestionAudio {
    /** Record this browser fallback instance and its inherited URL. */
    constructor(url) { super(url); observation.audio.push(this); }
  };
  /** Announce readiness only for the currently attached renderer. */
  observation.ready = () => {
    harness.ueAvailable = true;
    if (harness.voice.player) harness.voice.player.ready = true;
    harness.voice.avatarEvent({ type: "avatar_ready" });
  };
  return observation;
}

/**
 *  Verify chunked capture results match full segment resampling results.
 */
test("resampling keeps exact phase across arbitrary 44.1 and 48 kHz capture blocks", () => {
  for (const rate of [44100, 48000]) {
    const input = Float32Array.from({ length: rate }, /**
 *  Generate fixed-sampling-rate sinusoidal test input.
 */ (_, index) => Math.sin(index * 0.13) * 0.8);
    const expected = new PCM16Resampler(rate).process(input);
    const resampler = new PCM16Resampler(rate);
    const actual = [];
    for (let offset = 0; offset < input.length; offset += 128) {
      actual.push(...resampler.process(input.subarray(offset, offset + 128)));
    }
    assert.equal(actual.length, 16000);
    assert.deepEqual(actual, expected);
  }
  assert.deepEqual(new PCM16Resampler(16000).process([2, -2, 0.5]), [32767, -32768, 16384]);
});

/**
 *  Verify worklet sends little-endian PCM tail first, then confirms flush, and does not replay input.
 */
test("worklet flush sends little-endian tail before acknowledgement and never echoes", async () => {
  const events = [];
  let Processor;
  globalThis.sampleRate = 48000;
  /**
 *  Provide a stub base class for worklet with message recording capability.
 */
  globalThis.AudioWorkletProcessor = class { /**
 *  Create message recording port.
 */ constructor() { this.port = { /**
 *  Record worklet message send order.
 */ postMessage: (event) => events.push(event) }; } };
  /**
 *  Save the tested worklet class.
 */
  globalThis.registerProcessor = (_name, type) => { Processor = type; };
  await import("../../speech-worklet.js");
  const worklet = new Processor();
  const output = new Float32Array(512).fill(1);
  worklet.process([[new Float32Array(512).fill(0.5)]], [[output]]);
  assert.ok(output.every(/**
 *  Verify output buffer remains silent at all times.
 */ (sample) => sample === 0));
  worklet.port.onmessage({ data: "flush" });
  assert.deepEqual(events.map(/**
 *  Extract message types to verify send order.
 */ (event) => event.type), ["pcm", "flushed"]);
  assert.equal(events[0].buffer.byteLength, 340);
  assert.equal(new DataView(events[0].buffer).getInt16(0, true), 16384);
  assert.equal(worklet.process([], [[output]]), false);
});

/**
 *  Verify old TTS responses do not trigger a new playback round.
 */
test("late TTS result cannot start playback after the next question is displayed", async () => {
  page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Old question" };
  voice.player = readyRenderer(); // Exercise cancellation of the retained renderer WAV request.
  let resolve;
  /**
 *  Provide a manually endable TTS request.
 */
  globalThis.fetch = () => new Promise(/**
 *  Save TTS request completion callback.
 */ (done) => { resolve = done; });
  let audioCount = 0;
  /**
 *  Record browser audio instance creation count.
 */
  globalThis.Audio = class { /**
 *  Increment audio creation counter.
 */ constructor() { audioCount++; } };
  const pending = voice.speak();
  voice.setQuestion({ text: "New question" });
  resolve({ ok: true, /**
 *  Return expired question audio.
 */ json: async () => ({ utterance_id: "old", audio_url: "/old.wav" }) });
  await pending;
  assert.equal(audioCount, 0);
  assert.equal(voice.question.text, "New question");
  assert.equal(voice.busy, false);
  voice.close();
});

/**
 *  Verify old UE events do not alter current turn and do not cause duplicate browser sound playback.
 */
test("old UE events are ignored and UE audio is never also played in the browser", async () => {
  page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Current question" };
  const sent = [];
  voice.player = { ready: true, /**
 *  Record UE control messages and simulate successful send.
 */ send: (message) => { sent.push(message); return true; }, /**
 *  Provide a player close stub requiring no resource cleanup.
 */ close() {} };
  /**
 *  Provide successful TTS response for current question.
 */
  globalThis.fetch = async () => ({ ok: true, /**
 *  Return audio identifier and address for current question.
 */ json: async () => ({ utterance_id: "current", audio_url: "/current.wav", generation_ms: 10 }) });
  /**
 *  Detect unexpected browser duplicate playback.
 */
  globalThis.Audio = class { /**
 *  Immediately fail test if browser creates duplicate audio.
 */ constructor() { assert.fail("Browser audio duplicated UE playback"); } };
  await voice.speak();
  voice.avatarEvent({ type: "playback_finished", utterance_id: "old" });
  assert.equal(voice.busy, true);
  voice.avatarEvent({ type: "playback_started", utterance_id: "current" });
  voice.avatarEvent({ type: "playback_finished", utterance_id: "current" });
  assert.equal(voice.busy, false);
  assert.ok(sent.some(/**
 *  Check whether speak message has been sent.
 */ (message) => message.type === "speak"));
  assert.ok(sent.some(/**
 *  Check whether stop message has been sent.
 */ (message) => message.type === "stop"));
  voice.close();
});

/**
 *  Verify voice answer can still be initiated after TTS quota failure.
 */
test("TTS failure leaves voice recording available", async () => {
  const elements = page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Question" };
  voice.player = readyRenderer(); // Keep this quota regression on the renderer WAV endpoint.
  /**
 *  Provide TTS quota insufficient response.
 */
  globalThis.fetch = async () => ({ ok: false, /**
 *  Return fixed public error message.
 */ json: async () => ({ error: { code: "quota_exhausted", detail: "Use text" } }) });
  await voice.speak();
  assert.equal(voice.eligible, true);
  assert.equal(voice.capture, null);
  assert.match(elements.get("voice-status").textContent, /quota_exhausted/);
  voice.close();
});

/**
 *  Verify late authorization after device rejection or cancellation does not leak audio track.
 */
test("microphone refusal reports failure and late permission cannot keep a cancelled stream alive", async () => {
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    mediaDevices: { /**
 *  Simulate microphone permission denial.
 */ getUserMedia: async () => { throw new Error("Permission denied"); } },
  }});
  const denied = new SpeechCapture(/**
 *  Temporary transcription callback.
 */ () => {}, /**
 *  Final transcription callback.
 */ () => {}, /**
 *  Recognition error callback.
 */ () => {});
  await assert.rejects(denied.start(), /Permission denied/);
  assert.equal(denied.closed, true);
  let resolve;
  let stopped = false;
  /**
 *  Provide late device authorization result.
 */
  navigator.mediaDevices.getUserMedia = () => new Promise(/**
 *  Save device authorization completion callback.
 */ (done) => { resolve = done; });
  const cancelled = new SpeechCapture(/**
 *  Cancel session temporary transcription callback.
 */ () => {}, /**
 *  Cancel session final transcription callback.
 */ () => {}, /**
 *  Cancel session recognition error callback.
 */ () => {});
  const pending = cancelled.start();
  await cancelled.close();
  resolve({ /**
 *  Return test audio track with verifiable stop status.
 */ getTracks: () => [{ /**
 *  Mark late audio track as released.
 */ stop: () => { stopped = true; } }] });
  await pending;
  assert.equal(stopped, true);
  assert.equal(cancelled.recording, false);
});

/**
 *  New account API requires TTS write requests to carry page token; does not weaken server-side CSRF checks.
 */
test("TTS includes the page CSRF token for authenticated sessions", async () => {
  page();
  document.getElementById("csrf-token").content = "public-test-csrf";
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Describe one contribution." };
  voice.player = readyRenderer(); // The streaming endpoint has separate authentication coverage.
  let headers;
  /**
 *  Save client request headers; fix failure to prevent browser audio creation during testing.
 */
  globalThis.fetch = async (_url, options) => {
    headers = options.headers;
    return { ok: false, /**
 *  Return offline error, prohibiting real vendor calls.
 */ json: async () => ({ error: { code: "offline", detail: "Test only" } }) };
  };
  await voice.speak();
  assert.equal(headers["X-CSRFToken"], "public-test-csrf");
  assert.equal(headers["Content-Type"], "application/json");
  voice.close();
});

/** Verify 10 seconds of preparation and five silent seconds without real microphone access. */
test("preparation opens capture once and five silent seconds submit an empty answer", async () => {
  page();
  const submitted = [];
  const voice = new InterviewVoice(/** Observe exactly one final answer submission. */ (text) => submitted.push(text));
  voice.eligible = true;
  const originalStart = SpeechCapture.prototype.start;
  const originalEnd = SpeechCapture.prototype.end;
  let starts = 0;
  /** Replace device/provider startup with a recording-state transition only. */
  SpeechCapture.prototype.start = async function () { starts++; this.recording = true; };
  /** Deliver the empty final provider text after automatic flush. */
  SpeechCapture.prototype.end = async function () { this.recording = false; this.onFinal("", 1); await this.close(); };
  try {
    const before = performance.now();
    voice.setQuestion({ question_id: "q1", text: "Question" });
    assert.ok(voice.deadline - before >= 10000);
    assert.equal(starts, 0);
    assert.match(document.getElementById("answer-countdown").textContent, /10s/);
    voice.deadline = performance.now() - 1;
    voice.tick();
    await Promise.resolve();
    assert.equal(starts, 1);
    assert.equal(voice.phase, "answering");
    voice.tick();
    assert.equal(starts, 1);
    const capture = voice.capture;
    voice.deadline = performance.now() + 20;
    capture.options.onActivity();
    assert.ok(voice.deadline - performance.now() > 4900);
    voice.deadline = performance.now() - 1;
    voice.tick();
    await Promise.resolve();
    voice.tick();
    assert.deepEqual(submitted, [""]);
    assert.equal(voice.capture, null);
  } finally { voice.close(); SpeechCapture.prototype.start = originalStart; SpeechCapture.prototype.end = originalEnd; }
});

/** Verify semantic completion submits once and obsolete recognition callbacks cannot affect the new question. */
test("semantic completion flushes once and stale text cannot refresh a new question", async () => {
  page();
  const submitted = [];
  const voice = new InterviewVoice(/** Save final text and optional receipt for protocol assertions. */ (text, receipt) => submitted.push({ text, receipt }));
  voice.eligible = true;
  voice.completionEnabled = true;
  const originalStart = SpeechCapture.prototype.start;
  const originalEnd = SpeechCapture.prototype.end;
  let endings = 0;
  /** Simulate ready capture without creating real device or network resources. */
  SpeechCapture.prototype.start = async function () { this.recording = true; };
  /** Simulate receipt-bound provider finalization. */
  SpeechCapture.prototype.end = async function () { endings++; this.recording = false; this.onFinal("Real answer", 1, "receipt"); await this.close(); };
  try {
    voice.setQuestion({ question_id: "q1", text: "First" });
    voice.deadline = performance.now() - 1; voice.tick();
    await Promise.resolve();
    const previous = voice.capture;
    previous.options.onCompletion(); previous.options.onCompletion();
    await Promise.resolve();
    assert.equal(endings, 1);
    assert.deepEqual(submitted, [{ text: "Real answer", receipt: "receipt" }]);
    voice.setQuestion({ question_id: "q2", text: "Second" });
    const deadline = voice.deadline;
    previous.onPartial("Late words"); previous.options.onActivity();
    assert.equal(voice.deadline, deadline);
    assert.equal(document.getElementById("answer-subtitle").textContent, "");
  } finally { voice.close(); SpeechCapture.prototype.start = originalStart; SpeechCapture.prototype.end = originalEnd; }
});

/** Verify early ending consumes final STT rather than partial captions and prevents normal answer submission. */
test("early end flushes final text without submitting another answer or question", async () => {
  page();
  const submitted = [];
  const voice = new InterviewVoice(/** Detect accidental normal submission while ending. */ (text) => submitted.push(text));
  voice.eligible = true;
  const originalStart = SpeechCapture.prototype.start;
  const originalEnd = SpeechCapture.prototype.end;
  /** Mark the offline capture ready for finalization. */
  SpeechCapture.prototype.start = async function () { this.recording = true; };
  /** Return final words distinct from the preceding partial subtitles. */
  SpeechCapture.prototype.end = async function () { this.recording = false; this.onFinal("Final answer", 1); await this.close(); };
  try {
    voice.setQuestion({ question_id: "q1", text: "Question" });
    voice.deadline = performance.now() - 1; voice.tick();
    await Promise.resolve();
    voice.capture.onPartial("Partial caption");
    assert.equal(await voice.takeFinalAnswer(), "Final answer");
    assert.deepEqual(submitted, []);
    assert.equal(voice.answerTimer, null);
  } finally { voice.close(); SpeechCapture.prototype.start = originalStart; SpeechCapture.prototype.end = originalEnd; }
});

/** Verify a denied automatic microphone attempt is explicit and is not silently retried. */
test("automatic microphone failure stops clocks and does not retry", async () => {
  page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  const originalStart = SpeechCapture.prototype.start;
  let attempts = 0;
  /** Reject device startup at the same boundary as browser permission refusal. */
  SpeechCapture.prototype.start = async function () { attempts++; throw new Error("Permission denied"); };
  try {
    voice.setQuestion({ question_id: "q1", text: "Question" });
    voice.deadline = performance.now() - 1; voice.tick();
    await Promise.resolve();
    voice.tick();
    assert.equal(attempts, 1);
    assert.equal(voice.phase, "error");
    assert.equal(voice.answerTimer, null);
    assert.match(document.getElementById("voice-status").textContent, /Permission denied/);
  } finally { voice.close(); SpeechCapture.prototype.start = originalStart; }
});

/** Verify slow synthesis and UE readiness cannot consume or abort the user's preparation time. */
test("initial preparation starts after audible completion despite slow TTS and UE readiness", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
    assert.equal(voice.phase, "generating"); assert.equal(voice.deadline, null);
    harness.advance(20000); await harness.flush();
    assert.equal(harness.requests[0].options.signal.aborted, false);
    assert.equal(harness.starts, 0); assert.equal(voice.busy, true);
    harness.completeTts(); await harness.flush();
    assert.equal(voice.phase, "avatar_preparing"); assert.equal(voice.deadline, null);
    assert.match(document.getElementById("voice-status").textContent, /mouth animation/);
    harness.advance(16000); await harness.flush();
    assert.equal(voice.audio, null); assert.equal(voice.busy, true); assert.equal(harness.starts, 0);
    voice.avatarEvent({ type: "playback_started", utterance_id: "audio-0" });
    const playbackTimer = voice.playbackTimer;
    voice.avatarEvent({ type: "playback_started", utterance_id: "audio-0" });
    assert.equal(voice.playbackTimer, playbackTimer);
    harness.advance(12000);
    assert.equal(voice.deadline, null); assert.equal(harness.starts, 0);
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-0" });
    assert.equal(voice.phase, "preparing"); assert.equal(voice.deadline, harness.now + 10000);
    harness.advance(9900); await harness.flush(); assert.equal(harness.starts, 0);
    harness.advance(100); await harness.flush(); assert.equal(harness.starts, 1);
    const capture = voice.capture; const answerDeadline = voice.deadline;
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-0" });
    await voice.speak();
    assert.equal(voice.capture, capture); assert.equal(voice.deadline, answerDeadline);
    assert.equal(harness.requests.length, 1); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** A renderer ready at selection can recover during synthesis without duplicating its chosen WAV request. */
test("renderer selected while ready can recover during synthesis without another TTS request", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
    const observation = pendingAvatar(harness);
    harness.completeTts(); await harness.flush();
    assert.equal(voice.audio, null); assert.equal(observation.audio.length, 0);
    assert.equal(voice.deadline, null); assert.equal(harness.starts, 0);
    harness.advance(3000); await harness.flush();
    assert.equal(observation.audio.length, 0);
    observation.ready(); await harness.flush();
    const spoken = observation.accepted.filter(/** Select only actual speech commands. */ (message) => message.type === "speak");
    assert.deepEqual(spoken, [{ type: "speak", utterance_id: "audio-0", audio_url: "/offline-0.wav" }]);
    assert.equal(observation.audio.length, 0); assert.equal(voice.phase, "avatar_preparing");
    assert.equal(harness.requests.length, 1); assert.equal(voice.deadline, null);
    voice.avatarEvent({ type: "playback_started", utterance_id: "audio-0" });
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-0" });
    assert.equal(voice.deadline, harness.now + 10000);
    assert.equal(harness.starts, 0); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** A renderer chosen while ready may be replaced during synthesis; its new presentation receives the same WAV identity. */
test("selected renderer replacement during synthesis binds the new presentation without another request", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  const boundUtterances = [];
  try {
    voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
    const observation = pendingAvatar(harness);
    const pendingPlayer = voice.player;
    voice.player = null;
    voice.avatarConnecting = true;
    harness.completeTts(); await harness.flush();
    assert.equal(observation.audio.length, 0); assert.equal(voice.deadline, null);
    voice.player = pendingPlayer;
    voice.avatarConnecting = false;
    voice.presentation = {
      /** Record identity binding after presentation configuration finishes. */
      bindUtterance(value) { boundUtterances.push(value); },
      /** Accept lifecycle events without renderer resources. */ onAvatarEvent() {},
      /** Accept presentation state without animation. */ setState() {},
      /** Complete a simulated listening observation. */ endListeningCapture() {},
      /** Clear simulated presentation state. */ clear() {},
      /** Release the resource-free presentation double. */ close() {},
    };
    observation.ready(); await harness.flush();
    assert.deepEqual(boundUtterances, ["audio-0"]);
    assert.deepEqual(observation.accepted.filter(/** Select speech commands for identity assertions. */ (message) => message.type === "speak"),
      [{ type: "speak", utterance_id: "audio-0", audio_url: "/offline-0.wav" }]);
    assert.equal(observation.audio.length, 0); assert.equal(harness.requests.length, 1);
    assert.equal(voice.phase, "avatar_preparing"); assert.equal(harness.starts, 0); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** A renderer lost after WAV selection retains its existing bounded fallback without duplicate late playback. */
test("readiness lost during synthesis times out to one fallback from the selected WAV", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
    const observation = pendingAvatar(harness);
    harness.completeTts(); await harness.flush();
    harness.advance(7999); await harness.flush();
    assert.equal(observation.audio.length, 0); assert.equal(voice.deadline, null);
    harness.advance(1); await harness.flush();
    assert.equal(observation.audio.length, 1);
    assert.equal(observation.audio[0].url, "/offline-0.wav");
    assert.equal(voice.audio, observation.audio[0]); assert.equal(voice.phase, "reading");
    voice.avatarEvent({ type: "avatar_disconnected" }); await harness.flush();
    assert.equal(observation.audio.length, 1, "A late connection failure must not restart browser fallback audio.");
    observation.ready(); await harness.flush();
    assert.equal(observation.audio.length, 1);
    assert.equal(observation.accepted.filter(/** Detect duplicate renderer speech after fallback. */ (message) => message.type === "speak").length, 0);
    assert.equal(harness.requests.length, 1); assert.equal(harness.starts, 0); assert.equal(harness.answers, 0);
    voice.audio.onended();
    assert.equal(voice.deadline, harness.now + 10000);
  } finally { harness.close(); }
});

/** Disconnecting after renderer selection wakes its pending WAV once, without duplicate fallback resources. */
test("renderer disconnect after WAV selection creates only one fallback", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
    const observation = pendingAvatar(harness);
    harness.completeTts(); await harness.flush();
    voice.avatarEvent({ type: "avatar_disconnected" }); await harness.flush();
    assert.equal(observation.audio.length, 1); assert.equal(voice.audio, observation.audio[0]);
    assert.equal(observation.audio[0].url, "/offline-0.wav");
    harness.advance(8000); await harness.flush();
    observation.ready(); await harness.flush();
    assert.equal(observation.audio.length, 1);
    assert.equal(observation.accepted.filter(/** Detect speech revived by late readiness. */ (message) => message.type === "speak").length, 0);
    assert.equal(harness.requests.length, 1); assert.equal(harness.starts, 0); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** Reset/end invalidates an already selected renderer request before late readiness can revive playback. */
test("reset and interview end cancel recovery of an already selected renderer", async (t) => {
  for (const ending of ["reset", "end", "page-close"]) {
    await t.test(ending, /** Verify each termination path fences late callbacks independently. */ async () => {
      const harness = new VoicePlaybackHarness(); const voice = harness.voice;
      try {
        voice.setQuestion({ question_id: "q1", text: "Explain one contribution." });
        const observation = pendingAvatar(harness);
        harness.completeTts(); await harness.flush();
        if (ending === "page-close") voice.close();
        else {
          voice.reset();
          if (ending === "end") voice.disconnectAvatar();
        }
        await harness.flush();
        observation.ready(); await harness.flush();
        harness.advance(20000); await harness.flush();
        assert.equal(observation.audio.length, 0); assert.equal(voice.audio, null);
        assert.equal(observation.accepted.filter(/** Detect stale renderer speech after termination. */ (message) => message.type === "speak").length, 0);
        assert.equal(voice.question, null); assert.equal(voice.capture, null); assert.equal(voice.deadline, null);
        assert.equal(harness.requests.length, 1); assert.equal(harness.starts, 0); assert.equal(harness.answers, 0);
      } finally { harness.close(); }
    });
  }
});

/** Verify explicit replay preserves preparation already spent and stale events cannot grant another ten seconds. */
test("replay pauses preparation and resumes its remaining time", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    document.getElementById("voice-enabled").checked = false;
    voice.setQuestion({ question_id: "q1", text: "Explain your project." });
    harness.advance(5000);
    const replay = voice.speak();
    assert.equal(voice.preparationRemainingMs, 5000); assert.equal(voice.deadline, null);
    harness.advance(18000); assert.equal(harness.starts, 0);
    harness.completeTts(); await replay;
    voice.avatarEvent({ type: "playback_started", utterance_id: "audio-0" });
    harness.advance(8000);
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-0" });
    const deadline = voice.deadline;
    assert.equal(deadline, harness.now + 5000);
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-0" });
    voice.stopPlayback();
    assert.equal(voice.deadline, deadline); assert.equal(harness.starts, 0);
    harness.advance(5000); await harness.flush(); assert.equal(harness.starts, 1);
  } finally { harness.close(); }
});

/** Verify failure and explicit skip paths grant preparation while cancelled or obsolete TTS cannot revive playback. */
test("TTS failure, disabled voice and explicit interruption grant preparation once", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "First question." });
    harness.advance(23000); harness.completeTts(0, false); await harness.flush();
    assert.equal(voice.deadline, harness.now + 10000);
    assert.match(document.getElementById("voice-status").textContent, /quota_exhausted/);
    document.getElementById("voice-enabled").checked = false;
    document.getElementById("voice-enabled").onchange();
    const deadline = voice.deadline;
    assert.equal(deadline, harness.now + 10000);
    document.getElementById("voice-enabled").checked = true;
    voice.setQuestion({ question_id: "q2", text: "Second question." });
    harness.advance(10000); document.getElementById("interrupt-speech").onclick();
    assert.equal(voice.deadline, harness.now + 10000);
    assert.equal(harness.requests[1].options.signal.aborted, true);
    const interruptedDeadline = voice.deadline;
    harness.completeTts(1); await harness.flush();
    assert.equal(voice.utteranceId, null); assert.equal(voice.deadline, interruptedDeadline);
    document.getElementById("voice-enabled").checked = false;
    voice.setQuestion({ question_id: "q3", text: "Third question." });
    assert.equal(voice.deadline, harness.now + 10000); assert.equal(harness.requests.length, 2);
    assert.equal(harness.starts, 0);
  } finally { harness.close(); }
});

/** Verify fallback audible completion starts preparation, and retired media callbacks cannot touch a new question. */
test("browser fallback completion and failure grant preparation without stale resets", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    harness.ueAvailable = false;
    voice.setQuestion({ question_id: "q1", text: "First question." });
    harness.completeTts(); await harness.flush();
    const audio = voice.audio;
    assert.ok(audio); assert.equal(voice.deadline, null);
    harness.advance(18000); assert.equal(harness.starts, 0);
    audio.onended(); const completedDeadline = voice.deadline;
    assert.equal(completedDeadline, harness.now + 10000);
    audio.onerror(); assert.equal(voice.deadline, completedDeadline);
    voice.setQuestion({ question_id: "q2", text: "Second question." });
    harness.completeTts(1); await harness.flush();
    const nextAudio = voice.audio;
    audio.onended(); audio.onerror();
    assert.equal(voice.audio, nextAudio); assert.equal(voice.deadline, null);
    nextAudio.onerror();
    assert.equal(voice.deadline, harness.now + 10000); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** Verify preparation never replaces independent TTS, UE startup or bounded browser playback deadlines. */
test("speech deadlines preserve bounded fallbacks before preparation begins", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "First question." });
    harness.advance(50000);
    assert.equal(harness.requests[0].options.signal.aborted, true);
    harness.requests[0].reject(new Error("Offline TTS timeout")); await harness.flush();
    assert.equal(voice.deadline, harness.now + 10000);
    voice.setQuestion({ question_id: "q2", text: "Second question." });
    harness.completeTts(1); await harness.flush();
    harness.advance(18000); await harness.flush();
    const audio = voice.audio;
    assert.ok(audio); assert.equal(voice.deadline, null);
    voice.avatarEvent({ type: "playback_started", utterance_id: "audio-1" });
    voice.avatarEvent({ type: "playback_finished", utterance_id: "audio-1" });
    assert.equal(voice.audio, audio); assert.equal(voice.deadline, null);
    harness.advance(125000); await harness.flush();
    assert.equal(audio.paused, true); assert.equal(voice.deadline, harness.now + 10000);
    assert.equal(harness.starts, 0); assert.equal(harness.requests.length, 2);
  } finally { harness.close(); }
});

/** Verify an early-end dialog pauses preparation rather than losing the clock or granting a new full period. */
test("dismissing early end resumes only remaining preparation", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    document.getElementById("voice-enabled").checked = false;
    voice.setQuestion({ question_id: "q1", text: "Explain your project." });
    harness.advance(6000);
    assert.equal(await voice.takeFinalAnswer(), ""); assert.equal(voice.deadline, null);
    assert.equal(voice.preparationRemainingMs, 4000);
    harness.advance(30000); voice.resumeAnswer();
    assert.equal(voice.deadline, harness.now + 4000);
    harness.advance(4000); await harness.flush();
    assert.equal(harness.starts, 1); assert.equal(harness.answers, 0);
  } finally { harness.close(); }
});

/** Verify browser autoplay refusal is a bounded voice fallback, never a blocked microphone preparation. */
test("autoplay refusal starts full preparation without automatic synthesis retry", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  const originalPlay = OfflineQuestionAudio.prototype.play;
  /** Reject only the offline browser playback startup. */
  OfflineQuestionAudio.prototype.play = function () { return Promise.reject(new Error("Autoplay denied")); };
  try {
    harness.ueAvailable = false;
    voice.setQuestion({ question_id: "q1", text: "Explain your contribution." });
    harness.advance(8000); harness.completeTts(); await harness.flush();
    assert.equal(voice.busy, false); assert.equal(voice.deadline, harness.now + 10000);
    assert.match(document.getElementById("voice-status").textContent, /enable audio/);
    assert.equal(harness.requests.length, 1); assert.equal(harness.starts, 0);
  } finally { OfflineQuestionAudio.prototype.play = originalPlay; harness.close(); }
});

/** Verify the page's direct choosing-dialog suspension, rather than only the later save/finalization path. */
test("actual end-choice controls path preserves preparation and newly arrived questions", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    document.getElementById("voice-enabled").checked = false;
    voice.setQuestion({ question_id: "q1", text: "First question." });
    harness.advance(6000);
    voice.suspended = true;
    window.dispatchEvent({ type: "interview-controls", detail: { active: true, answering: false } });
    assert.equal(voice.deadline, null); assert.equal(voice.preparationRemainingMs, 4000);
    harness.advance(30000);
    window.dispatchEvent({ type: "interview-controls", detail: { active: true, answering: true } });
    voice.resumeAnswer();
    assert.equal(voice.deadline, harness.now + 4000); assert.equal(harness.starts, 0);
    voice.suspended = true;
    window.dispatchEvent({ type: "interview-controls", detail: { active: true, answering: false } });
    voice.setQuestion({ question_id: "q2", text: "Question delivered while choosing." });
    voice.suspended = true;
    assert.equal(voice.deadline, null); assert.equal(voice.preparationRemainingMs, 10000);
    harness.advance(30000);
    window.dispatchEvent({ type: "interview-controls", detail: { active: true, answering: true } });
    voice.resumeAnswer();
    assert.equal(voice.deadline, harness.now + 10000);
    harness.advance(10000); await harness.flush();
    assert.equal(harness.starts, 1); assert.equal(voice.question.question_id, "q2");
  } finally { harness.close(); }
});

/** Verify a long allowed WAV receives a bounded CPU preparation window rather than a premature legacy fallback. */
test("long WAV readiness extends beyond eighteen seconds and remains bounded", async () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    voice.setQuestion({ question_id: "q1", text: "A longer interview question." });
    harness.completeTts(0, true, 120000); await harness.flush();
    assert.equal(harness.timers.get(voice.playbackTimer).at, 125000);
    harness.advance(18000); await harness.flush();
    assert.equal(voice.audio, null); assert.equal(voice.busy, true);
    assert.equal(voice.phase, "avatar_preparing"); assert.equal(voice.deadline, null);
    harness.advance(106999); assert.equal(voice.audio, null);
    harness.advance(1); await harness.flush();
    assert.ok(voice.audio); assert.equal(voice.phase, "reading"); assert.equal(voice.deadline, null);
    voice.audio.onended();
    assert.equal(voice.deadline, harness.now + 10000); assert.equal(harness.starts, 0);
  } finally { harness.close(); }
});

/** Verify provider-independent duration metadata uses exact bounds and cannot weaken finite playback protection. */
test("duration-aware readiness rejects malformed metadata and preserves legacy defaults", () => {
  const harness = new VoicePlaybackHarness(); const voice = harness.voice;
  try {
    for (const invalid of [undefined, null, false, true, "120000", 0, -1, NaN, Infinity, {}, [], 120001]) {
      assert.equal(voice.avatarPreparationTimeout(invalid), 18000);
    }
    assert.equal(voice.avatarPreparationTimeout(1000), 18000);
    assert.equal(voice.avatarPreparationTimeout(5600), 23400);
    assert.equal(voice.avatarPreparationTimeout(7520), 26280);
    assert.equal(voice.avatarPreparationTimeout(120000), 125000);
  } finally { harness.close(); }
});
