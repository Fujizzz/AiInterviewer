/**
 * @module presentation-tests
 * Responsibilities: Verify Agent-first dynamic face profiles, recorded fallback, idle initialization and bounded lifecycle without cloud calls.
 * Implementation: Stub profile HTTP and native transport; advance fake monotonic clocks and resolve requests out of order.
 * Related Modules: presentation-controller.js owns profiles; InterviewVoice preserves speech and answer flow; native tests verify curve ownership.
 * Declaration Index:
 * - Deferred: Hold an offline HTTP request for explicit delivery.
 * - Deferred.constructor: Capture promise settlement callbacks.
 * - Deferred.constructor.callback1: Save settlement callbacks without I/O.
 * - FakeClock: Store deterministic presentation timers.
 * - FakeClock.constructor: Initialize monotonic time and pending timer ownership.
 * - FakeClock.now: Return current fake milliseconds.
 * - FakeClock.schedule: Store one timer with a deadline.
 * - FakeClock.cancel: Remove a pending timer.
 * - FakeClock.advance: Run due callbacks chronologically without real waiting.
 * - fullProfile: Build a complete bounded dynamic profile fixture.
 * - modelPlan: Build a version 2 response correlated to its request.
 * - response: Wrap a fixture as a successful fetch result.
 * - response.object1.json: Return the supplied fixture.
 * - Harness: Record native commands and deferred profile requests.
 * - Harness.constructor: Initialize offline transports and optional deterministic timers.
 * - Harness.send: Save an immutable native command copy.
 * - Harness.status: Save diagnostic output.
 * - Harness.fetch: Save a service request and return its deferred response.
 * - Harness.resolve: Deliver one recorded response.
 * - flush: Settle asynchronous response continuations.
 * - callback1: Verify generation fences and approved question copies.
 * - callback2: Verify question-free first-ready idle initialization.
 * - callback3: Verify diagnostics connect and preview consume no model quota.
 * - callback4: Verify explicit clear holds fallback after reconnect.
 * - callback5: Verify failure switches ownership to recorded clips with no retry.
 * - callback6: Verify strict full-profile bounds and all four states.
 * - callback6.callback1: Reject each independently invalid fixture.
 * - callback7: Verify invalid model output cannot be forwarded.
 * - callback8: Verify utterance correlation and absence of timed speaking cues.
 * - callback9: Verify transport and diagnostic exceptions remain isolated.
 * - callback9.object1.send: Simulate a disconnected transport throwing.
 * - callback9.object1.onStatus: Simulate a diagnostic consumer throwing.
 * - callback10: Verify independent bounded timeout and no retry.
 * - callback10.object1.fetchImpl: Reject an offline request after abort.
 * - callback10.object1.fetchImpl.callback1: Register the abort listener.
 * - callback10.object1.fetchImpl.callback1.callback1: Reject at the fetch abort boundary.
 * - callback11: Verify same-origin CSRF headers.
 * - callback12: Verify preview expiry and permanent closure.
 * - callback13: Verify copied ownership and native size bounds.
 * - callback14: Verify sparse quiet refresh and speech deferral.
 * - callback15: Verify refresh failure and expiry do not revive old control.
 * - callback16: Verify terminal idle cache retains its original deadline.
 * - callback17: Verify reconnect and rebind never renew a deadline.
 * - voicePage: Install minimal stable offline voice controls.
 * - voicePage.globalThis.document.getElementById: Return cached DOM control objects.
 * - voicePage.globalThis.window.addEventListener: Ignore unused page listeners.
 * - callback18: Verify real voice hooks preserve replay, thinking, terminal idle and answer flow.
 * - callback18.callback1: Detect unwanted answer submission.
 * - callback18.voice.player.close: Close the offline player.
 * - callback18.globalThis.fetch: Supply one offline TTS identity per replay.
 * - callback18.globalThis.fetch.object1.json: Return the offline TTS response.
 * - callback18.callback2: Find first speech delivery for ordering checks.
 * - callback18.callback3: Find the first utterance-correlated face plan.
 * - callback18.callback4: Find the replay's correlated plan.
 * - callback18.callback5: Verify a new question retains Agent control throughout voice cleanup.
 * - callback19: Verify healthy profile retention during question replacement and recorded fallback only on failure.
 * - callback20: Verify native global fetch keeps its required browser receiver.
 * - callback20.globalThis.fetch: Simulate receiver-sensitive native fetch without network I/O.
 * - callback21: Verify sanitized HTTP, JSON and network diagnostic codes.
 * - callback21.object1.fetchImpl: Return an unauthorized response without provider I/O.
 * - callback21.object3.fetchImpl: Return malformed offline response JSON.
 * - callback21.object3.fetchImpl.object1.json: Reject JSON parsing without exposing response text.
 * - callback21.object5.fetchImpl: Simulate a network error with sensitive raw text.
 * - callback22: Verify optional motion compatibility, bounds and preview defaults.
 * - callback22.callback1: Reject an out-of-range optional motion control.
 * - callback23: Verify voiced heartbeat throttling and natural-pause debounce.
 * - callback24: Verify ended captures, generation fences and cancelled pause timers.
 * - callback25: Verify reconnect recreates observation without speech or model work.
 * - callback26: Verify optional observer failure cannot prevent lifecycle completion.
 * - callback26.callback1: Ignore the unused offline transcript callback.
 * - callback26.callback2: Ignore the unused offline final callback.
 * - callback26.callback3: Record unexpected recognition errors.
 * - callback26.object1.onPresence: Simulate an optional observer throwing.
 * - callback27: Verify real voice capture callbacks fence stale observations and leave answer timing unchanged.
 * - callback27.SpeechCapture.prototype.start: Simulate ready capture without microphone or model I/O.
 * - callback27.callback1: Record unexpected answer submission.
 * - callback28: Verify maximum-sized four-profile native commands remain within the existing wire limit.
 * Variable Index:
 * - PRESENTATION_ID: Stable offline profile identity.
 * - UTTERANCE_ONE: First offline TTS identity.
 * - UTTERANCE_TWO: Second offline TTS identity.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { PresentationController, validatePresentationPlan } from "../src/presentation-controller.js";
import { InterviewVoice } from "../../interview-voice.js";
import { SpeechCapture } from "../../speech-capture.js";

const PRESENTATION_ID = "aaaaaaaa-0000-0000-0000-000000000001";
const UTTERANCE_ONE = "bbbbbbbb-0000-0000-0000-000000000001";
const UTTERANCE_TWO = "bbbbbbbb-0000-0000-0000-000000000002";

/** Hold one HTTP result until the test chooses its completion, intentionally allowing stale-response tests. */
class Deferred {
  /** Capture settlement operations without performing network I/O. */
  constructor() {
    this.promise = new Promise(/** Save the deferred promise callbacks. */ (resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;
    });
  }
}

/** Store deterministic timers so ten-minute lifetime tests take no wall-clock time. */
class FakeClock {
  /** Initialize monotonic milliseconds and timer identity. */
  constructor() { this.time = 0; this.sequence = 0; this.pending = new Map(); }
  /** Return deterministic monotonic milliseconds. */
  now() { return this.time; }
  /** Store one callback with its requested due time. */
  schedule(callback, delay) { const id = ++this.sequence; this.pending.set(id, { due: this.time + delay, callback }); return id; }
  /** Release only the specified pending timer. */
  cancel(id) { this.pending.delete(id); }
  /** Advance chronologically, allowing callbacks to schedule another timer without running past the requested target. */
  advance(milliseconds) {
    const target = this.time + milliseconds;
    for (;;) {
      let next = null;
      for (const [id, timer] of this.pending) if (timer.due <= target && (!next || timer.due < next.timer.due)) next = { id, timer };
      if (!next) break;
      this.time = next.timer.due;
      this.pending.delete(next.id);
      next.timer.callback();
    }
    this.time = target;
  }
}

/** Build the exact backend/native profile shape with restrained motion and valid scheduling ranges. */
function fullProfile(expression = "attentive", intensity = 0.21) {
  return { expression, intensity, variation: 0.6, blink_min_ms: 3000, blink_max_ms: 6000,
    blink_duration_ms: 180, gaze_amplitude: 0.05, gaze_hold_min_ms: 1400, gaze_hold_max_ms: 3200,
    eye_contact: 0.9, warmth: 0.05, motion_min_ms: 3500, motion_max_ms: 8000 };
}

/** Return one valid Agent response with identities copied from the recorded request. */
function modelPlan(request) {
  return { version: 2, presentation_id: request.presentation_id, generation: request.generation,
    question_id: request.question?.question_id ?? "session-idle", source: "model", reason: null,
    transition_ms: 350, valid_ms: 600000,
    states: { idle: fullProfile("neutral", 0.12), listening: fullProfile(),
      thinking: fullProfile("thoughtful", 0.22), speaking: fullProfile("attentive", 0.18) } };
}

/** Wrap a fixture as the minimal successful fetch response. */
function response(plan) { return { ok: true, /** Return fixture JSON without interpretation. */ async json() { return plan; } }; }

/** Record plan requests and commands while supplying manually resolved HTTP boundaries. */
class Harness {
  /** Initialize isolated recordings; an optional fake clock supplies all presentation timer operations. */
  constructor(options = {}, clock = null) {
    this.requests = []; this.commands = []; this.statuses = [];
    const clocks = clock ? { nowImpl: clock.now.bind(clock), setTimeoutImpl: clock.schedule.bind(clock), clearTimeoutImpl: clock.cancel.bind(clock) } : {};
    this.controller = new PresentationController({ presentationId: PRESENTATION_ID,
      send: this.send.bind(this), onStatus: this.status.bind(this), fetchImpl: this.fetch.bind(this), ...clocks, ...options });
  }
  /** Save immutable command snapshots to expose accidental external mutation. */
  send(command) { this.commands.push(structuredClone(command)); return true; }
  /** Record optional diagnostic values rather than displaying UI. */
  status(value) { this.statuses.push(value); }
  /** Save the copied request and its independent abort signal. */
  fetch(url, options) { const deferred = new Deferred(); this.requests.push({ url, options, body: JSON.parse(options.body), deferred }); return deferred.promise; }
  /** Deliver a recorded request with its valid fixture or an explicitly invalid test plan. */
  resolve(index, plan = modelPlan(this.requests[index].body)) { this.requests[index].deferred.resolve(response(plan)); }
}

/** Let HTTP and JSON continuations settle without real waiting. */
async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }

/** Verify approved business data stays immutable and obsolete Agent output cannot alter a newer question. */
test("generation fencing copies approved inputs without modifying interview data", async () => {
  const harness = new Harness();
  const question = Object.freeze({ question_id: "q-one", text: "Describe your contribution.", question_type: "project",
    intent: "clarify", dialogue_action: "probe", last_evaluation: { score: 9 }, answer_text: "Private answer" });
  const first = harness.controller.setQuestion(question);
  const second = harness.controller.setQuestion({ question_id: "q-two", text: "What did you measure?" });
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[0].options.signal.aborted, true);
  assert.deepEqual(harness.requests[0].body.question, { question_id: "q-one", text: question.text,
    question_type: "project", intent: "clarify", dialogue_action: "probe" });
  assert.equal(harness.requests[0].body.version, 2);
  harness.resolve(1); await second;
  const count = harness.commands.length;
  harness.resolve(0); await first;
  assert.equal(harness.commands.length, count);
  assert.equal(harness.commands.at(-1).question_id, "q-two");
  assert.equal(harness.commands.at(-1).source, "model");
  assert.equal(question.answer_text, "Private answer");
  harness.controller.close();
});

/** Verify first-ready initializes real idle dynamics before any question and subsequent pings do not repeat the model. */
test("avatar first-ready bootstraps nullable idle exactly once", async () => {
  const harness = new Harness();
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].body.question, null);
  assert.equal(harness.commands.at(-1).source, "fallback");
  harness.resolve(0); await flush();
  assert.equal(harness.commands.at(-1).question_id, "session-idle");
  assert.equal(harness.commands.at(-1).source, "model");
  assert.equal(harness.commands.at(-1).states.idle.variation, 0.6);
  assert.equal(Object.hasOwn(harness.commands.at(-1), "speaking"), false);
  harness.controller.close();
});

/** Verify diagnostics only request profiles after explicit action, while preview drives all four states locally. */
test("diagnostic connection and explicit preview make no implicit model request", () => {
  const harness = new Harness({ bootstrapIdle: false });
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  assert.equal(harness.requests.length, 0);
  assert.equal(harness.controller.preview("friendly", 0.22), true);
  assert.equal(harness.commands.at(-1).source, "preview");
  assert.equal(harness.commands.at(-1).states.speaking.expression, "friendly");
  harness.controller.onAvatarEvent({ type: "avatar_disconnected" });
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  assert.equal(harness.requests.length, 0);
  harness.controller.close();
});

/** Verify explicit clear is an intentional recorded-animation hold, including reconnect and late responses. */
test("explicit clear fences HTTP and holds recorded fallback through reconnect", async () => {
  const harness = new Harness();
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.controller.clear();
  const cleared = harness.commands.at(-1);
  assert.deepEqual(cleared, { type: "expression_clear", presentation_id: PRESENTATION_ID, generation: 2 });
  harness.resolve(0); await pending;
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  harness.controller.setState("idle");
  assert.deepEqual(harness.commands.at(-1), cleared);
  assert.equal(harness.requests.length, 1);
  harness.controller.close();
});

/** Verify Agent failure selects recorded clips rather than a mislabeled procedural accent and does not retry on ordinary state changes. */
test("Agent HTTP failure selects recorded fallback with no automatic retry", async () => {
  const harness = new Harness();
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.controller.setState("listening");
  harness.requests[0].deferred.resolve({ ok: false }); await pending;
  assert.equal(harness.commands.at(-1).source, "fallback");
  assert.equal(harness.commands.at(-1).reason, "request_failed");
  harness.controller.setState("thinking"); harness.controller.setState("idle");
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  harness.controller.clear({ resumeIdle: true }); harness.controller.setState("idle");
  assert.equal(harness.requests.length, 1);
  harness.controller.close();
});

/** Verify bounds, exact profile fields, required speaking profile and lack of obsolete timed cue support. */
test("strict v2 validation requires complete bounded profiles for all states", () => {
  const request = { version: 2, presentation_id: PRESENTATION_ID, generation: 1, question: null };
  const variants = [];
  let plan = modelPlan(request); plan.states.idle.expression = "CTRL_expressions_jawOpen"; variants.push(plan);
  plan = modelPlan(request); plan.states.listening.intensity = 0.9; variants.push(plan);
  plan = modelPlan(request); plan.states.thinking.blink_max_ms = 3100; variants.push(plan);
  plan = modelPlan(request); plan.states.idle.gaze_amplitude = NaN; variants.push(plan);
  plan = modelPlan(request); plan.states.idle.eye_contact = "0.9"; variants.push(plan);
  plan = modelPlan(request); plan.states.idle.motion_max_ms = 4001.1; variants.push(plan);
  plan = modelPlan(request); delete plan.states.speaking; variants.push(plan);
  plan = modelPlan(request); plan.states.idle.raw_curves = {}; variants.push(plan);
  plan = modelPlan(request); plan.generation = 2; variants.push(plan);
  plan = modelPlan(request); plan.speaking = []; variants.push(plan);
  plan = modelPlan(request); plan.valid_ms = 600001; variants.push(plan);
  for (const invalid of variants) assert.throws(/** Reject each independently invalid service fixture. */ () => validatePresentationPlan(invalid, request), /invalid_plan/);
  assert.equal(validatePresentationPlan(modelPlan(request), request).question_id, "session-idle");
});

/** Verify successful HTTP cannot promote invalid output to active native control. */
test("invalid model output is isolated and switches to recorded fallback", async () => {
  const harness = new Harness();
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  const invalid = modelPlan(harness.requests[0].body); invalid.states.thinking.variation = Infinity;
  harness.resolve(0, invalid); await pending;
  assert.equal(harness.commands.at(-1).source, "fallback");
  assert.equal(harness.statuses.at(-1).reason, "invalid_plan");
  harness.controller.close();
});

/** Verify TTS identity is retained only for correlation and stale speech events cannot affect the current profile. */
test("utterance correlation supports replay without any timed speaking cues", async () => {
  const harness = new Harness();
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.controller.bindUtterance(UTTERANCE_ONE);
  harness.controller.onAvatarEvent({ type: "playback_finished", utterance_id: UTTERANCE_TWO });
  assert.equal(harness.controller.utteranceId, UTTERANCE_ONE);
  harness.controller.onAvatarEvent({ type: "playback_finished", utterance_id: UTTERANCE_ONE });
  harness.resolve(0); await pending;
  assert.equal(harness.commands.at(-1).utterance_id, "");
  assert.equal(Object.hasOwn(harness.commands.at(-1), "speaking"), false);
  assert.equal(harness.controller.bindUtterance(UTTERANCE_TWO), true);
  assert.equal(harness.commands.at(-1).states.speaking.expression, "attentive");
  assert.equal(harness.controller.bindUtterance("untrusted.wav"), false);
  harness.controller.close();
});

/** Verify optional UI and data-channel exceptions do not become interview failures. */
test("optional transport and diagnostic failures stay isolated", async () => {
  const harness = new Harness({ /** Simulate an unavailable data channel. */ send() { throw new Error("offline"); },
    /** Simulate an optional panel consumer failure. */ onStatus() { throw new Error("panel absent"); } });
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await pending;
  assert.equal(harness.controller.plan.source, "model");
  harness.controller.close();
});

/** Verify the independent HTTP deadline does not retry or interfere with state observation. */
test("bounded timeout aborts profile HTTP and selects recorded fallback", async () => {
  let calls = 0;
  const harness = new Harness({ timeoutMs: 5,
    /** Reject the offline request at its abort boundary without provider I/O. */ fetchImpl(url, options) {
      ++calls;
      return new Promise(/** Register the offline request's abort rejection. */ (resolve, reject) => {
        options.signal.addEventListener("abort", /** Deliver the rejected fetch boundary. */ () => reject(new Error("aborted")), { once: true });
      });
    } });
  await harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  assert.equal(calls, 1);
  assert.equal(harness.controller.plan.source, "fallback");
  assert.equal(harness.statuses.at(-1).reason, "timeout");
  harness.controller.close();
});

/** Verify same-origin credentials and the existing CSRF token without leaking unrelated cookies. */
test("profile HTTP uses same-origin CSRF and no unrelated cookie header", async () => {
  const previousDocument = globalThis.document;
  globalThis.document = { cookie: "other=private; csrftoken=csrf%2Dfixture" };
  const harness = new Harness();
  try {
    const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
    assert.equal(harness.requests[0].options.credentials, "same-origin");
    assert.equal(harness.requests[0].options.headers["X-CSRFToken"], "csrf-fixture");
    assert.equal(JSON.stringify(harness.requests[0].options.headers).includes("private"), false);
    harness.resolve(0); await pending;
  } finally { harness.controller.close(); globalThis.document = previousDocument; }
});

/** Verify local preview has a bounded lifetime and closed controllers never restart requests or timers. */
test("preview expires to recorded clips and closure is permanent", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  assert.equal(harness.controller.preview("friendly", 0.22), true);
  assert.equal(harness.controller.preview("jawOpen", 0.2), false);
  assert.equal(harness.controller.preview("friendly", 0.36), false);
  clock.advance(600000);
  assert.equal(harness.commands.at(-1).source, "fallback");
  assert.equal(harness.commands.at(-1).reason, "expired");
  assert.equal(harness.requests.length, 0);
  harness.controller.close();
  const count = harness.commands.length;
  await harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  assert.equal(harness.controller.preview("friendly"), false);
  assert.equal(harness.commands.length, count);
  assert.equal(clock.pending.size, 0);
});

/** Verify native payload limits and copied ownership even for maximum-length Unicode question identifiers. */
test("bounded native payload and copied plans protect ownership", async () => {
  const harness = new Harness();
  await harness.controller.setQuestion({ question_id: "q-long", text: "x".repeat(1201) });
  assert.equal(harness.requests.length, 0);
  const pending = harness.controller.setQuestion({ question_id: "问".repeat(128), text: "Explain your project." });
  harness.resolve(0); await pending;
  const command = harness.controller.command();
  assert.ok(new TextEncoder().encode(JSON.stringify(command)).length <= 4096);
  command.states.listening.intensity = 1;
  assert.equal(harness.controller.command().states.listening.intensity, 0.21);
  harness.controller.close();
});

/** Verify ordinary state changes make no model calls and eight-minute renewal waits until speech has completed. */
test("active sparse renewal defers while speaking and resumes once quiet", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  harness.controller.connected = true; harness.controller.setActive(true);
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await pending;
  harness.controller.setState("thinking"); harness.controller.setState("listening"); harness.controller.setState("speaking");
  clock.advance(480000);
  assert.equal(harness.requests.length, 1);
  harness.controller.setState("listening");
  assert.equal(harness.requests.length, 2);
  harness.controller.setState("thinking");
  assert.equal(harness.requests.length, 2);
  harness.resolve(1); await flush();
  assert.equal(harness.commands.at(-1).generation, 2);
  assert.equal(harness.commands.at(-1).valid_ms, 600000);
  harness.controller.close();
});

/** Verify refresh failure retires prior successful control, and unrefreshed inactive control has a strict deadline. */
test("refresh failure and expiry never revive old Agent control", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  harness.controller.connected = true; harness.controller.setActive(true);
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await pending;
  clock.advance(480000);
  assert.equal(harness.requests.length, 2);
  harness.requests[1].deferred.resolve({ ok: false }); await flush();
  assert.equal(harness.commands.at(-1).source, "fallback");
  assert.equal(harness.controller.lastModel, null);
  clock.advance(600000);
  assert.equal(harness.requests.length, 2);
  harness.controller.close();
  const other = new Harness({}, new FakeClock());
  const model = other.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  other.resolve(0); await model;
  other.controller.validUntil = other.controller.now() - 1;
  assert.equal(other.controller.command().source, "fallback");
  other.controller.close();
});

/** Verify terminal idle reuses approved control without another call and cannot extend its original lifetime. */
test("terminal idle rebind keeps original deadline without another model call", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  harness.controller.connected = true;
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await pending;
  clock.advance(100000);
  harness.controller.clear({ resumeIdle: true });
  const clearFence = harness.commands.at(-1).generation;
  harness.controller.setState("idle");
  assert.equal(harness.commands.at(-1).source, "model");
  assert.equal(harness.commands.at(-1).question_id, "session-idle");
  assert.ok(harness.commands.at(-1).generation > clearFence, "Native clear fences reject plans from the same generation");
  assert.equal(harness.commands.at(-1).valid_ms, 500000);
  assert.equal(harness.requests.length, 1);
  clock.advance(500000);
  assert.equal(harness.commands.at(-1).source, "fallback");
  harness.controller.close();
});

/** Verify speech rebinding and reconnect restore only remaining validity, avoiding accidental indefinite control. */
test("rebind and reconnect preserve remaining profile lifetime", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  const pending = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await pending;
  clock.advance(100000);
  harness.controller.bindUtterance(UTTERANCE_ONE);
  assert.equal(harness.commands.at(-1).valid_ms, 500000);
  harness.controller.onAvatarEvent({ type: "avatar_disconnected" });
  clock.advance(100000);
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  assert.equal(harness.commands.at(-1).valid_ms, 400000);
  assert.equal(harness.commands.at(-1).utterance_id, "");
  assert.equal(harness.requests.length, 1);
  harness.controller.close();
});

/** Install stable controls without browser automation, microphone access or real speech services. */
function voicePage() {
  const elements = new Map();
  globalThis.document = { /** Return stable controls with harmless display fields. */ getElementById(id) {
    if (!elements.has(id)) elements.set(id, { checked: false, disabled: false, textContent: "", value: "" });
    return elements.get(id);
  } };
  globalThis.window = { /** Ignore unused page subscriptions. */ addEventListener() {} };
}

/** Drive actual voice methods against actual profile control with offline TTS and no answer submission. */
test("real voice preserves TTS ordering, replay, thinking and terminal Agent idle", async () => {
  const previousDocument = globalThis.document; const previousWindow = globalThis.window; const previousFetch = globalThis.fetch;
  voicePage(); const clock = new FakeClock(); const harness = new Harness({}, clock);
  let answers = 0;
  const voice = new InterviewVoice(/** Detect expression work accidentally submitting an answer. */ () => { ++answers; });
  voice.presentation = harness.controller;
  voice.player = { send: harness.send.bind(harness), /** Close the offline player without browser resources. */ close() {} };
  let ttsCalls = 0;
  /** Supply a fresh offline identity for each explicit TTS replay. */
  globalThis.fetch = async () => {
    const utterance = ++ttsCalls === 1 ? UTTERANCE_ONE : UTTERANCE_TWO;
    return { ok: true, /** Return a cached-style audio address without a provider call. */ async json() {
      return { utterance_id: utterance, audio_url: `http://127.0.0.1:8765/api/speech/audio/${utterance}/`, generation_ms: 0 };
    } };
  };
  try {
    voice.setQuestion({ question_id: "q-one", text: "Explain your project." }); await voice.speak();
    const speakIndex = harness.commands.findIndex(/** Find the first speech delivery. */ (command) => command.type === "speak");
    const boundIndex = harness.commands.findIndex(/** Find prior correlated face control. */ (command) => command.type === "expression_plan" && command.utterance_id === UTTERANCE_ONE);
    assert.ok(boundIndex >= 0 && boundIndex < speakIndex);
    voice.avatarEvent({ type: "playback_started", utterance_id: UTTERANCE_ONE });
    voice.avatarEvent({ type: "playback_finished", utterance_id: UTTERANCE_ONE });
    assert.equal(voice.busy, false); assert.equal(voice.presentation.utteranceId, "");
    harness.resolve(0); await flush();
    assert.equal(voice.presentation.plan.source, "model");
    assert.equal(Object.hasOwn(voice.presentation.command(), "speaking"), false);
    await voice.speak();
    const replayPlan = harness.commands.findLast(/** Find the explicit replay's face correlation. */ (command) => command.type === "expression_plan" && command.utterance_id === UTTERANCE_TWO);
    assert.equal(replayPlan.states.speaking.expression, "attentive");
    const generation = voice.presentation.generation;
    voice.reset(false); voice.setState("thinking");
    assert.equal(voice.presentation.generation, generation);
    assert.equal(voice.presentation.plan.source, "model");
    const boundary = harness.commands.length;
    voice.setQuestion({ question_id: "q-two", text: "How did you verify it?" });
    assert.equal(voice.presentation.plan.source, "model");
    assert.ok(harness.commands.slice(boundary).every(/** New-question speech cleanup must not insert a native face-clear or recorded fallback phase. */ (command) => command.type !== "expression_clear" && (command.type !== "expression_plan" || command.source === "model")));
    harness.resolve(1); await flush();
    voice.presentation.connected = true;
    voice.reset(); voice.setState("idle");
    assert.equal(voice.presentation.plan.question_id, "session-idle");
    assert.equal(harness.requests.length, 2); assert.equal(ttsCalls, 2); assert.equal(answers, 0);
  } finally { voice.close(); globalThis.document = previousDocument; globalThis.window = previousWindow; globalThis.fetch = previousFetch; }
});

/** Verify pending replacement does not use recorded clips, preserves native correlation/deadline, and only actual failure selects fallback. */
test("question replacement retains healthy Agent control until validated commit or failure", async () => {
  const clock = new FakeClock(); const harness = new Harness({}, clock);
  const first = harness.controller.setQuestion({ question_id: "q-one", text: "Explain your project." });
  harness.resolve(0); await first;
  clock.advance(1000);
  const oldGeneration = harness.controller.plan.generation;
  const second = harness.controller.setQuestion({ question_id: "q-two", text: "How did you verify it?" });
  assert.equal(harness.requests[1].body.question.question_id, "q-two");
  assert.ok(harness.requests[1].body.generation > oldGeneration);
  assert.equal(harness.commands.at(-1).source, "model");
  assert.equal(harness.commands.at(-1).generation, oldGeneration);
  assert.equal(harness.commands.at(-1).question_id, "q-one");
  assert.equal(harness.commands.at(-1).valid_ms, 599000);
  harness.controller.bindUtterance(UTTERANCE_TWO);
  assert.equal(harness.commands.at(-1).generation, oldGeneration);
  assert.equal(harness.commands.at(-1).utterance_id, UTTERANCE_TWO);
  harness.resolve(1); await second;
  assert.equal(harness.commands.at(-1).question_id, "q-two");
  assert.equal(harness.commands.at(-1).generation, harness.requests[1].body.generation);
  assert.equal(harness.commands.at(-1).valid_ms, 600000);
  const third = harness.controller.setQuestion({ question_id: "q-three", text: "What would you improve?" });
  assert.equal(harness.commands.at(-1).source, "model");
  harness.requests[2].deferred.resolve({ ok: false }); await third;
  assert.equal(harness.commands.at(-1).source, "fallback");
  assert.equal(harness.commands.at(-1).question_id, "q-three");
  assert.equal(harness.commands.at(-1).reason, "request_failed");
  harness.controller.close();
});

/** Verify the default fetch retains Window/globalThis as its receiver, catching a bug hidden by bound HTTP test doubles. */
test("default native fetch keeps the global receiver rather than the controller instance", async () => {
  const savedFetch = globalThis.fetch;
  let correctReceiver = false;
  let calls = 0;
  /** Require the receiver expected by browser-native fetch without performing network I/O. */
  globalThis.fetch = function () {
    ++calls;
    correctReceiver = this === globalThis;
    if (!correctReceiver) throw new TypeError("Illegal invocation");
    return Promise.resolve({ ok: false, status: 401 });
  };
  const controller = new PresentationController({ presentationId: PRESENTATION_ID, bootstrapIdle: false });
  try {
    assert.equal(controller.timeoutMs, 35000, "Profile timeout covers the backend's 30-second maximum independently of speech");
    await controller.setQuestion({ question_id: "q-one", text: "Offline request." });
    assert.equal(correctReceiver, true);
    assert.equal(calls, 1);
    assert.equal(controller.plan.reason, "http_401");
  } finally { controller.close(); globalThis.fetch = savedFetch; }
});

/** Verify diagnostic codes explain failures without exposing raw response bodies, credentials or provider error text. */
test("HTTP, malformed JSON and network failures report only sanitized diagnostic codes", async () => {
  const forbidden = new Harness({ /** Return an unauthorized offline status without calling the model. */ fetchImpl() { return Promise.resolve({ ok: false, status: 403 }); } });
  await forbidden.controller.setQuestion({ question_id: "q-one", text: "Offline request." });
  assert.equal(forbidden.controller.plan.reason, "http_403");
  forbidden.controller.close();
  const malformed = new Harness({ /** Supply a successful status whose JSON parsing fails. */ fetchImpl() {
    return Promise.resolve({ ok: true, /** Reject parsing without surfacing the response body. */ json() { return Promise.reject(new Error("secret response body")); } });
  } });
  await malformed.controller.setQuestion({ question_id: "q-one", text: "Offline request." });
  assert.equal(malformed.controller.plan.reason, "invalid_json");
  malformed.controller.close();
  const network = new Harness({ /** Simulate a fetch boundary failure containing text that must not be shown. */ fetchImpl() { return Promise.reject(new Error("secret API credential")); } });
  await network.controller.setQuestion({ question_id: "q-one", text: "Offline request." });
  assert.equal(network.controller.plan.reason, "network_error");
  network.controller.close();
});

/** Verify old thirteen-field profiles remain safe, while new bounded motion knobs enable restrained preview movement. */
test("optional head and audio controls accept legacy zero defaults and reject invalid values", () => {
  const request = { version: 2, presentation_id: PRESENTATION_ID, generation: 1, question: null };
  const legacy = validatePresentationPlan(modelPlan(request), request);
  assert.equal(legacy.states.listening.head_motion_strength, 0);
  assert.equal(legacy.states.speaking.audio_emphasis_strength, 0);
  for (const field of ["head_motion_strength", "head_motion_probability", "audio_emphasis_strength"]) {
    const invalid = modelPlan(request); invalid.states.idle[field] = 1.001;
    assert.throws(/** Reject each optional control outside its local movement bound. */ () => validatePresentationPlan(invalid, request), /invalid_plan/);
  }
  const harness = new Harness({ bootstrapIdle: false });
  harness.controller.preview("attentive", 0.2);
  assert.equal(harness.commands.at(-1).states.listening.head_motion_strength, 0.5);
  assert.equal(harness.commands.at(-1).states.thinking.head_motion_probability, 0.55);
  assert.equal(harness.commands.at(-1).states.speaking.audio_emphasis_strength, 0.35);
  harness.controller.close();
});

/** Verify low-bandwidth observations distinguish real pauses from brief gaps without affecting STT or making model requests. */
test("listening observation debounces pauses and throttles continuous voice heartbeats", () => {
  const clock = new FakeClock(); const harness = new Harness({ bootstrapIdle: false }, clock);
  harness.controller.preview("attentive", 0.2);
  const captureId = harness.controller.beginListeningCapture();
  assert.match(captureId, /^[0-9a-f-]{36}$/);
  assert.deepEqual(harness.commands.at(-1), { type: "listening_activity", presentation_id: PRESENTATION_ID,
    capture_id: captureId, capture_generation: 1, sequence: 1, active: false, ended: false });
  harness.controller.observeListeningActivity(true);
  assert.equal(harness.commands.at(-1).sequence, 2);
  clock.advance(100); harness.controller.observeListeningActivity(false);
  clock.advance(200); harness.controller.observeListeningActivity(true);
  clock.advance(200);
  assert.equal(harness.commands.at(-1).sequence, 2, "Brief speech gaps do not trigger a pause");
  clock.advance(500); harness.controller.observeListeningActivity(true);
  assert.equal(harness.commands.at(-1).sequence, 3);
  harness.controller.observeListeningActivity(false);
  clock.advance(449);
  assert.equal(harness.commands.at(-1).active, true);
  clock.advance(1);
  assert.equal(harness.commands.at(-1).sequence, 4);
  assert.equal(harness.commands.at(-1).active, false);
  assert.equal(harness.commands.at(-1).ended, false);
  harness.controller.observeListeningActivity(false); clock.advance(1000);
  assert.equal(harness.commands.at(-1).sequence, 4);
  assert.equal(harness.requests.length, 0);
  harness.controller.close();
});

/** Verify ended identities cannot be revived by a delayed pause, and subsequent captures use a higher independent generation. */
test("capture end cancels quiet timers and fences subsequent microphone observation identities", () => {
  const clock = new FakeClock(); const harness = new Harness({ bootstrapIdle: false }, clock);
  const firstId = harness.controller.beginListeningCapture();
  assert.equal(harness.controller.beginListeningCapture(), firstId, "Repeated startup is idempotent");
  harness.controller.observeListeningActivity(true);
  harness.controller.observeListeningActivity(false);
  harness.controller.endListeningCapture();
  const ended = harness.commands.at(-1);
  assert.equal(ended.ended, true); assert.equal(ended.active, false);
  const count = harness.commands.length;
  clock.advance(1000);
  assert.equal(harness.commands.length, count);
  assert.equal(harness.controller.observeListeningActivity(true), false);
  const nextId = harness.controller.beginListeningCapture();
  assert.notEqual(nextId, firstId);
  assert.equal(harness.commands.at(-1).capture_generation, 2);
  assert.equal(harness.commands.at(-1).sequence, 1);
  assert.equal(harness.controller.observeListeningActivity("true"), false);
  harness.controller.close();
  assert.equal(clock.pending.size, 0);
});

/** Verify disconnection ends local observation and a resumed microphone can start a distinct native lifecycle without reusing old IDs. */
test("disconnected listening observation ends and reconnect startup gets a fresh generation", () => {
  const harness = new Harness({ bootstrapIdle: false });
  harness.controller.preview("attentive", 0.2);
  const firstId = harness.controller.beginListeningCapture();
  harness.controller.observeListeningActivity(true);
  harness.controller.onAvatarEvent({ type: "avatar_disconnected" });
  assert.equal(harness.commands.at(-1).ended, true);
  harness.controller.onAvatarEvent({ type: "avatar_ready" });
  const nextId = harness.controller.beginListeningCapture();
  assert.notEqual(nextId, firstId);
  assert.equal(harness.commands.at(-1).capture_generation, 2);
  assert.equal(harness.requests.length, 0);
  harness.controller.close();
});

/** Verify the optional avatar observer cannot interrupt capture cleanup or create another ended callback. */
test("optional presence observer exceptions cannot prevent capture lifecycle cleanup", async () => {
  let observed = 0; let errors = 0;
  const capture = new SpeechCapture(/** Ignore unused draft text in this local lifecycle test. */ () => {},
    /** Ignore unused final text in this local lifecycle test. */ () => {},
    /** Record an unexpected recognition failure from the optional observer. */ () => { ++errors; },
    { /** Simulate an optional avatar callback throwing without affecting the microphone lifecycle. */ onPresence() { ++observed; throw new Error("observer failed"); } });
  capture.notifyPresence(true); capture.notifyPresence(false);
  await capture.close(); await capture.close(); capture.notifyPresence(true);
  assert.equal(observed, 3);
  assert.equal(errors, 0);
  assert.equal(capture.closed, true);
});

/** Drive real voice epoch/capture guards so obsolete PCM observation cannot trigger movement in another answer. */
test("real voice observes only current capture and preserves answering deadlines", async () => {
  const previousDocument = globalThis.document; const previousWindow = globalThis.window;
  const originalStart = SpeechCapture.prototype.start;
  voicePage(); const clock = new FakeClock(); const harness = new Harness({ bootstrapIdle: false }, clock);
  /** Mark capture ready without requesting a microphone or opening STT. */
  SpeechCapture.prototype.start = async function () { this.recording = true; };
  let answers = 0;
  const voice = new InterviewVoice(/** Detect unwanted submission caused by presentation observations. */ () => { ++answers; });
  voice.presentation = harness.controller; voice.eligible = true;
  try {
    voice.question = { question_id: "q-one", text: "Offline question." }; await voice.record();
    const first = voice.capture;
    const deadline = voice.deadline;
    first.notifyPresence(true);
    assert.equal(harness.commands.at(-1).active, true);
    assert.equal(voice.deadline, deadline, "Presence observation cannot move the answer clock");
    first.notifyPresence(false); clock.advance(450);
    assert.equal(harness.commands.at(-1).active, false);
    voice.reset(true, true);
    assert.equal(harness.commands.at(-1).ended, true);
    voice.question = { question_id: "q-two", text: "Another offline question." }; await voice.record();
    const second = voice.capture;
    const currentId = harness.controller.listeningCapture.id;
    const count = harness.commands.length;
    first.options.onPresence({ active: true, ended: false });
    assert.equal(harness.commands.length, count);
    second.notifyPresence(true);
    assert.equal(harness.commands.at(-1).capture_id, currentId);
    voice.avatarEvent({ type: "avatar_disconnected" });
    assert.equal(harness.controller.listeningCapture, null);
    voice.avatarEvent({ type: "avatar_ready" });
    assert.ok(harness.controller.listeningCapture);
    assert.notEqual(harness.controller.listeningCapture.id, currentId);
    assert.equal(answers, 0);
  } finally { voice.close(); SpeechCapture.prototype.start = originalStart; globalThis.document = previousDocument; globalThis.window = previousWindow; }
});

/** Verify maximum UTF-8 text, full profiles and high-precision finite controls still fit the existing 4096-byte native boundary. */
test("four complete motion profiles with maximal text fit the existing native wire limit", async (context) => {
  const harness = new Harness({}, new FakeClock());
  const pending = harness.controller.setQuestion({ question_id: "问".repeat(128), text: "Offline question." });
  const plan = modelPlan(harness.requests[0].body);
  plan.reason = "测".repeat(96);
  for (const state of Object.keys(plan.states)) plan.states[state] = {
    expression: "thoughtful", intensity: 0.3333333333333333, variation: 0.9999999999999999,
    blink_min_ms: 8999, blink_max_ms: 11999, blink_duration_ms: 249,
    gaze_amplitude: 0.14999999999999997, gaze_hold_min_ms: 4499, gaze_hold_max_ms: 6499,
    eye_contact: 0.9999999999999999, warmth: 0.14999999999999997,
    motion_min_ms: 7999, motion_max_ms: 15999, head_motion_strength: 0.9999999999999999,
    head_motion_probability: 0.9999999999999999, audio_emphasis_strength: 0.9999999999999999,
  };
  harness.resolve(0, plan); await pending;
  harness.controller.bindUtterance(UTTERANCE_TWO);
  const command = harness.controller.command(); command.generation = 2147483647;
  const bytes = new TextEncoder().encode(JSON.stringify(command)).length;
  context.diagnostic(`Maximal four-profile command: ${bytes} UTF-8 bytes`);
  assert.ok(bytes <= 4096);
  assert.equal(harness.controller.send(command), true);
  assert.equal(harness.commands.at(-1).generation, 2147483647);
  harness.controller.close();
});
