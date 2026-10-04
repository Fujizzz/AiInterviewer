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
 * - page.globalThis.window.addEventListener: Ignore page event subscriptions not triggered by this test.
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
 * - callback8: Verify 15 seconds of preparation and five silent seconds without real microphone access.
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
  globalThis.document = { /**
 *  Create and cache control state on demand.
 */ getElementById(id) {
    if (!elements.has(id)) elements.set(id, { disabled: false, checked: false, textContent: "", value: "" });
    return elements.get(id);
  }};
  globalThis.window = { /**
 *  Ignore page event subscriptions not triggered by this test.
 */ addEventListener() {} };
  return elements;
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
  voice.player = { /**
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

/** Verify 15 seconds of preparation and five silent seconds without real microphone access. */
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
    assert.ok(voice.deadline - before >= 15000);
    assert.equal(starts, 0);
    assert.match(document.getElementById("answer-countdown").textContent, /15s/);
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
