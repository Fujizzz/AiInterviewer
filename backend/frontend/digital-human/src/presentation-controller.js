/**
 * @module presentation-controller
 * Responsibilities: Request independent Agent profiles for full facial dynamics; recorded clips provide fallback rather than the normal quiet-state baseline.
 * Implementation: Fence question generations, initialize question-free idle, refresh profiles sparsely, and preserve existing speech ownership of jaw and mouth.
 * Related Modules: interview-voice.js owns speech and answers; pixel-player.js supplies the native channel; interviews/presentation supplies bounded profiles.
 * Declaration Index:
 * - bounded: Validate one finite profile parameter without coercion.
 * - profile: Copy only whitelisted, bounded dynamic profile controls.
 * - validatePresentationPlan: Validate version 2 correlation, validity and all four profiles.
 * - questionContext: Copy approved question fields without altering interview data.
 * - csrfToken: Read the existing page or cookie CSRF token.
 * - defaultProfile: Build safe dynamics for explicit offline previews.
 * - fallbackPlan: Mark recorded animation ownership explicitly.
 * - PresentationController: Coordinate facial profiles independently from interview or speech control.
 * - PresentationController.constructor: Initialize transports and lifecycle clocks without I/O.
 * - PresentationController.status: Isolate optional diagnostic failures.
 * - PresentationController.send: Bound native messages and isolate transport failures.
 * - PresentationController.command: Copy a profile with remaining validity and speech correlation.
 * - PresentationController.publish: Forward a profile or cancellation fence without renewing expiry.
 * - PresentationController.cancelRequest: Abort only the expression request.
 * - PresentationController.cancelProfileTimers: Clear expiry and refresh clocks.
 * - PresentationController.armProfileTimers: Schedule expiry and one sparse refresh.
 * - PresentationController.armProfileTimers.callback1: Release expired control to recorded fallback.
 * - PresentationController.armProfileTimers.callback2: Request one active quiet refresh.
 * - PresentationController.request: Copy the current service request envelope.
 * - PresentationController.setQuestion: Request a nonblocking profile for one approved question.
 * - PresentationController.ensureIdlePlan: Initialize idle once or restore valid cached control.
 * - PresentationController.restoreIdle: Rebind valid profiles to idle without another model call.
 * - PresentationController.requestPlan: Validate asynchronous output and fence obsolete results.
 * - PresentationController.requestPlan.callback1: Abort the bounded profile request.
 * - PresentationController.refresh: Renew only while connected, active and quiet.
 * - PresentationController.setActive: Observe interview activity without changing business flow.
 * - PresentationController.setState: Observe state and resume a due deferred refresh.
 * - PresentationController.bindUtterance: Retain TTS correlation without scheduling speech cues.
 * - PresentationController.beginListeningCapture: Create an independent bounded local microphone-observation lifecycle.
 * - PresentationController.publishListeningActivity: Send only capture identity, sequence and local activity flags.
 * - PresentationController.observeListeningActivity: Debounce natural pauses and throttle voiced heartbeats without interpreting answers.
 * - PresentationController.observeListeningActivity.callback1: Publish a natural pause only if the current capture remains quiet.
 * - PresentationController.endListeningCapture: Fence a capture with an explicit ended message and cancel pending pauses.
 * - PresentationController.onAvatarEvent: Initialize idle, restore current control and retire matching speech identity.
 * - PresentationController.preview: Preview explicit local profiles with no model call.
 * - PresentationController.clear: Fence current work and optionally allow cached terminal idle.
 * - PresentationController.close: Release requests, timers and cache permanently.
 * Variable Index:
 * - EXPRESSIONS: Whitelisted semantic expressions interpreted by the native controller.
 * - UUID: Accepted presentation and TTS identity format.
 * - STATES: Four Agent-controlled presentation states.
 * - QUIET_STATES: States in which sparse renewal is permitted.
 * - PROFILE_FIELDS: Exact dynamic profile keys accepted from the service.
 * - OPTIONAL_PROFILE_FIELDS: Backward-compatible local motion controls defaulting to zero.
 * - VALID_MS: Maximum ten-minute lifetime.
 * - REFRESH_MS: Eight-minute renewal threshold.
 */

const EXPRESSIONS = new Set(["neutral", "attentive", "thoughtful", "friendly", "emphasis"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATES = ["idle", "listening", "thinking", "speaking"];
const QUIET_STATES = ["idle", "listening", "thinking"];
const PROFILE_FIELDS = ["expression", "intensity", "variation", "blink_min_ms", "blink_max_ms",
  "blink_duration_ms", "gaze_amplitude", "gaze_hold_min_ms", "gaze_hold_max_ms", "eye_contact",
  "warmth", "motion_min_ms", "motion_max_ms"];
const OPTIONAL_PROFILE_FIELDS = ["head_motion_strength", "head_motion_probability", "audio_emphasis_strength"];
const VALID_MS = 600000;
const REFRESH_MS = 480000;

/** Reject non-finite, coerced and out-of-range values; timing controls require exact integers. */
function bounded(value, minimum, maximum, integer = false) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum
      || (integer && !Number.isInteger(value))) throw new Error("invalid_plan");
  return value;
}

/** Copy all bounded Agent-owned dynamics, rejecting unknown controls and impossible scheduling ranges. */
function profile(value) {
  if (!value || !EXPRESSIONS.has(value.expression) || Object.keys(value).length < PROFILE_FIELDS.length
      || Object.keys(value).length > PROFILE_FIELDS.length + OPTIONAL_PROFILE_FIELDS.length) throw new Error("invalid_plan");
  for (const field of Object.keys(value)) if (!PROFILE_FIELDS.includes(field) && !OPTIONAL_PROFILE_FIELDS.includes(field)) throw new Error("invalid_plan");
  const result = {
    expression: value.expression, intensity: bounded(value.intensity, 0, 0.35),
    variation: bounded(value.variation, 0, 1), blink_min_ms: bounded(value.blink_min_ms, 1800, 9000, true),
    blink_max_ms: bounded(value.blink_max_ms, 2500, 12000, true),
    blink_duration_ms: bounded(value.blink_duration_ms, 120, 250, true),
    gaze_amplitude: bounded(value.gaze_amplitude, 0, 0.15),
    gaze_hold_min_ms: bounded(value.gaze_hold_min_ms, 1000, 4500, true),
    gaze_hold_max_ms: bounded(value.gaze_hold_max_ms, 1800, 6500, true),
    eye_contact: bounded(value.eye_contact, 0.65, 1), warmth: bounded(value.warmth, 0, 0.15),
    motion_min_ms: bounded(value.motion_min_ms, 2500, 8000, true),
    motion_max_ms: bounded(value.motion_max_ms, 4000, 16000, true),
    head_motion_strength: bounded(Object.hasOwn(value, "head_motion_strength") ? value.head_motion_strength : 0, 0, 1),
    head_motion_probability: bounded(Object.hasOwn(value, "head_motion_probability") ? value.head_motion_probability : 0, 0, 1),
    audio_emphasis_strength: bounded(Object.hasOwn(value, "audio_emphasis_strength") ? value.audio_emphasis_strength : 0, 0, 1),
  };
  if (result.blink_max_ms < result.blink_min_ms + 500
      || result.gaze_hold_max_ms < result.gaze_hold_min_ms + 300
      || result.motion_max_ms < result.motion_min_ms + 500) throw new Error("invalid_plan");
  return result;
}

/** Validate full profiles and request correlation; old speaking cue arrays and arbitrary raw curves are not accepted. */
export function validatePresentationPlan(value, request) {
  const questionId = request.question?.question_id ?? "session-idle";
  if (!value || value.version !== 2 || value.presentation_id !== request.presentation_id
      || value.generation !== request.generation || value.question_id !== questionId
      || !["model", "fallback"].includes(value.source) || Object.hasOwn(value, "speaking")
      || (value.reason !== null && (typeof value.reason !== "string" || value.reason.length > 96))
      || value.transition_ms !== 350 || value.valid_ms !== VALID_MS || !value.states
      || Object.keys(value.states).length !== STATES.length) throw new Error("invalid_plan");
  const states = {};
  for (const state of STATES) states[state] = profile(value.states[state]);
  return {
    version: 2, presentation_id: request.presentation_id, generation: request.generation,
    question_id: questionId, source: value.source, reason: value.reason,
    transition_ms: 350, valid_ms: VALID_MS, states,
  };
}

/** Copy only the approved subset; null is a legitimate idle bootstrap before a question exists. */
function questionContext(question) {
  if (question === null) return null;
  if (!question || typeof question.question_id !== "string" || !question.question_id
      || question.question_id.length > 128 || typeof question.text !== "string"
      || !question.text.trim() || question.text.length > 1200) throw new Error("invalid_question");
  const context = { question_id: question.question_id, text: question.text };
  for (const field of ["question_type", "intent", "dialogue_action"]) {
    const maximum = field === "intent" ? 400 : 80;
    if (typeof question[field] === "string" && question[field].length <= maximum) context[field] = question[field];
  }
  return context;
}

/** Read only the established page or cookie CSRF token, without copying candidate or account data. */
function csrfToken() {
  const pageToken = globalThis.document?.getElementById?.("csrf-token")?.content;
  if (typeof pageToken === "string" && pageToken) return pageToken;
  for (const cookie of (globalThis.document?.cookie ?? "").split(";")) {
    const separator = cookie.indexOf("=");
    if (cookie.slice(0, separator).trim() === "csrftoken") {
      try { return decodeURIComponent(cookie.slice(separator + 1)); } catch { return ""; }
    }
  }
  return "";
}

/** Build safe local dynamics for explicit preview; native fallback commands ignore these profile fields. */
function defaultProfile(expression = "neutral", intensity = 0.12) {
  return {
    expression, intensity, variation: 0.55, blink_min_ms: 3000, blink_max_ms: 6500,
    blink_duration_ms: 180, gaze_amplitude: 0.06, gaze_hold_min_ms: 1600,
    gaze_hold_max_ms: 3600, eye_contact: 0.88, warmth: 0.04,
    motion_min_ms: 4000, motion_max_ms: 9000,
    head_motion_strength: 0.5, head_motion_probability: 0.55, audio_emphasis_strength: 0.35,
  };
}

/** Explicitly select recorded face animation when Agent output is unavailable; never label this as successful Agent control. */
function fallbackPlan(request, reason = "plan_pending") {
  return {
    version: 2, presentation_id: request.presentation_id, generation: request.generation,
    question_id: request.question?.question_id ?? "session-idle", source: "fallback", reason,
    transition_ms: 350, valid_ms: VALID_MS,
    states: { idle: defaultProfile(), listening: defaultProfile("attentive"),
      thinking: defaultProfile("thoughtful"), speaking: defaultProfile("attentive") },
  };
}

/** Own bounded profiles and sparse request clocks, never business decisions, answer submission or audio playback. */
export class PresentationController {
  /** Initialize optional offline clocks and transports; bind native fetch to its global receiver. The independent 35-second profile deadline covers the backend's 30-second maximum without changing speech or interview clocks; diagnostics may disable implicit model calls. */
  constructor({ send, onStatus, fetchImpl = globalThis.fetch.bind(globalThis), presentationId = globalThis.crypto.randomUUID(),
    timeoutMs = 35000, bootstrapIdle = true, nowImpl = globalThis.performance.now.bind(globalThis.performance),
    setTimeoutImpl = globalThis.setTimeout.bind(globalThis), clearTimeoutImpl = globalThis.clearTimeout.bind(globalThis) }) {
    if (!UUID.test(presentationId)) throw new Error("Invalid presentation identity");
    this.presentationId = presentationId.toLowerCase();
    this.sendImpl = send;
    this.onStatus = onStatus;
    this.fetchImpl = fetchImpl;
    this.now = nowImpl;
    this.schedule = setTimeoutImpl;
    this.cancelTimer = clearTimeoutImpl;
    this.timeoutMs = Math.max(1, Math.min(Number.isFinite(timeoutMs) ? timeoutMs : 35000, 35000));
    this.bootstrapIdle = bootstrapIdle === true;
    this.generation = 0;
    this.question = null;
    this.plan = null;
    this.lastModel = null;
    this.validUntil = 0;
    this.refreshAt = 0;
    this.utteranceId = "";
    this.state = "idle";
    this.connected = false;
    this.hasConnected = false;
    this.active = false;
    this.idleAttempted = false;
    this.allowIdle = true;
    this.abort = null;
    this.timer = null;
    this.expiryTimer = null;
    this.refreshTimer = null;
    this.closed = false;
    this.listeningCapture = null;
    this.captureGeneration = 0;
    this.presenceTimer = null;
  }

  /** Keep optional diagnostic exceptions outside speech and interview behavior. */
  status(source, reason) {
    try { this.onStatus?.({ source, reason, generation: this.generation }); } catch { /* Optional presentation diagnostics cannot control the interview. */ }
  }

  /** Bound native commands to 4096 UTF-8 bytes and tolerate unavailable data channels. */
  send(command) {
    try {
      if (new TextEncoder().encode(JSON.stringify(command)).length > 4096) return false;
      return this.sendImpl?.(command) !== false;
    } catch { return false; }
  }

  /** Copy the plan with remaining validity; TTS identity is correlation only, and native speech keeps mouth ownership. */
  command() {
    if (!this.plan) return null;
    const remaining = this.plan.source === "fallback" ? VALID_MS : Math.ceil(this.validUntil - this.now());
    const current = remaining <= 0 ? fallbackPlan(this.request(), "expired") : this.plan;
    return JSON.parse(JSON.stringify({ type: "expression_plan", ...current,
      utterance_id: this.utteranceId, valid_ms: remaining <= 0 ? VALID_MS : Math.min(VALID_MS, remaining) }));
  }

  /** Republish without renewing Agent lifetime or reviving a cleared question generation. */
  publish() {
    if (this.closed || this.generation < 1) return;
    this.send(this.command() ?? { type: "expression_clear", presentation_id: this.presentationId, generation: this.generation });
  }

  /** Release only the current presentation HTTP timeout and abort signal, never TTS or interview requests. */
  cancelRequest() {
    this.cancelTimer(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
  }

  /** Clear local profile expiry and renewal clocks without changing business state. */
  cancelProfileTimers() {
    this.cancelTimer(this.expiryTimer);
    this.cancelTimer(this.refreshTimer);
    this.expiryTimer = null;
    this.refreshTimer = null;
  }

  /** Schedule bounded lifetime and a sparse renewal only for connected active model control. */
  armProfileTimers() {
    this.cancelProfileTimers();
    if (!this.plan || this.plan.source === "fallback") return;
    const current = this.plan;
    this.expiryTimer = this.schedule(/** Return expired control to recorded fallback even when speech deferred renewal. */ () => {
      if (this.closed || this.plan !== current) return;
      this.cancelProfileTimers();
      this.lastModel = null;
      this.plan = fallbackPlan(this.request(), "expired");
      this.publish();
      this.status("fallback", "expired");
    }, Math.max(1, this.validUntil - this.now()));
    if (current.source === "model" && this.active && this.connected) {
      this.refreshTimer = this.schedule(/** Renew once while the active interviewer is quiet. */ () => {
        this.refreshTimer = null;
        void this.refresh();
      }, Math.max(1, this.refreshAt - this.now()));
    }
  }

  /** Copy the service envelope without candidate answers, resumes, scores or existing interview request IDs. */
  request() {
    return { version: 2, presentation_id: this.presentationId, generation: this.generation,
      question: this.question ? { ...this.question } : null };
  }

  /** Request one nonblocking profile while retaining still-valid Agent control; recorded animation covers missing control or an actual failure. */
  setQuestion(question) {
    if (this.closed) return Promise.resolve();
    this.endListeningCapture();
    this.cancelRequest();
    this.cancelProfileTimers();
    const keepCurrent = this.plan && this.plan.source !== "fallback" && this.validUntil > this.now();
    ++this.generation;
    this.utteranceId = "";
    this.allowIdle = true;
    try { this.question = questionContext(question); } catch {
      this.question = null;
      this.plan = null;
      this.allowIdle = false;
      this.publish();
      this.status("fallback", "invalid_question");
      return Promise.resolve();
    }
    // A question profile attempt also satisfies the session's initial Agent attempt;
    // terminal reset must not pay for a new idle request solely because that attempt failed.
    this.idleAttempted = true;
    const request = this.request();
    if (!keepCurrent) this.plan = fallbackPlan(request);
    this.armProfileTimers();
    this.publish();
    this.status(this.plan.source, "plan_pending");
    return this.requestPlan(request);
  }

  /** Bootstrap question-free idle once per connection; terminal idle reuses valid profiles rather than paying for another request. */
  ensureIdlePlan() {
    if (this.closed || !this.connected || !this.allowIdle || this.question || this.plan || this.abort) return Promise.resolve();
    if (this.restoreIdle()) return Promise.resolve();
    if (!this.bootstrapIdle || this.idleAttempted) return Promise.resolve();
    this.idleAttempted = true;
    return this.setQuestion(null);
  }

  /** Rebind valid model control to question-free idle after session reset while preserving its original expiry. */
  restoreIdle() {
    if (!this.lastModel || this.lastModel.validUntil <= this.now()) return false;
    ++this.generation;
    this.plan = JSON.parse(JSON.stringify({ ...this.lastModel.plan, generation: this.generation,
      question_id: "session-idle", reason: null }));
    this.question = null;
    this.validUntil = this.lastModel.validUntil;
    this.refreshAt = Math.min(this.validUntil, this.now() + REFRESH_MS);
    this.utteranceId = "";
    this.armProfileTimers();
    this.publish();
    this.status("model", "cached_idle");
    return true;
  }

  /** Accept only the exact current validated request; failure immediately selects recorded fallback and cannot change speech. */
  async requestPlan(request) {
    const abort = new AbortController();
    this.abort = abort;
    let timedOut = false;
    this.timer = this.schedule(/** Abort this presentation request at its independent deadline. */ () => {
      timedOut = true;
      abort.abort();
    }, this.timeoutMs);
    try {
      const response = await this.fetchImpl("/api/presentation/plan/", {
        method: "POST", credentials: "same-origin", signal: abort.signal,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify(request),
      });
      if (!response.ok) throw new Error(Number.isInteger(response.status) && response.status >= 100 && response.status <= 599
        ? `http_${response.status}` : "request_failed");
      let value;
      try { value = await response.json(); } catch { throw new Error("invalid_json"); }
      if (this.closed || this.abort !== abort || request.generation !== this.generation || abort.signal.aborted) return;
      this.plan = validatePresentationPlan(value, request);
      this.validUntil = this.now() + this.plan.valid_ms;
      this.refreshAt = this.now() + REFRESH_MS;
      this.lastModel = this.plan.source === "model" ? { plan: structuredClone(this.plan), validUntil: this.validUntil } : null;
      this.armProfileTimers();
      this.publish();
      this.status(this.plan.source, this.plan.reason);
    } catch (error) {
      if (this.closed || this.abort !== abort || request.generation !== this.generation) return;
      const reason = timedOut ? "timeout"
        : ["invalid_plan", "invalid_json", "request_failed"].includes(error?.message) || /^http_[1-5]\d{2}$/.test(error?.message ?? "")
          ? error.message : "network_error";
      this.cancelProfileTimers();
      this.lastModel = null;
      this.plan = fallbackPlan(request, reason);
      this.publish();
      this.status("fallback", reason);
    } finally {
      if (this.abort === abort) {
        this.cancelTimer(this.timer);
        this.timer = null;
        this.abort = null;
      }
    }
  }

  /** Renew at most once per eight minutes during a connected active quiet state; speech defers the request. */
  refresh() {
    if (this.closed || !this.connected || !this.active || !QUIET_STATES.includes(this.state) || this.abort
        || this.plan?.source !== "model" || this.now() < this.refreshAt) return Promise.resolve();
    ++this.generation;
    return this.requestPlan(this.request());
  }

  /** Observe interview activity only for renewal eligibility; answer flow and interview timers remain unchanged. */
  setActive(active) {
    this.active = active === true;
    this.armProfileTimers();
  }

  /** Observe state and permit a due deferred renewal; native state commands remain owned by the existing voice coordinator. */
  setState(state) {
    if (![...STATES, "interrupted"].includes(state)) return;
    this.state = state;
    if (state === "idle" && !this.plan && !this.question) void this.ensureIdlePlan();
    if (QUIET_STATES.includes(state)) void this.refresh();
  }

  /** Retain TTS identity for correlation only; no Agent speech clock or timed lip-sync cues are authored here. */
  bindUtterance(utteranceId) {
    if (this.closed || !this.plan || (utteranceId !== "" && !UUID.test(utteranceId))) return false;
    this.utteranceId = utteranceId;
    this.publish();
    return true;
  }

  /** Start one independent microphone-observation identity; repeated startup returns the current identity and never calls a model. */
  beginListeningCapture() {
    if (this.closed) return null;
    if (this.listeningCapture) return this.listeningCapture.id;
    if (this.captureGeneration >= 2147483647) return null;
    this.listeningCapture = { id: globalThis.crypto.randomUUID().toLowerCase(),
      generation: ++this.captureGeneration, sequence: 0, active: false, lastSentAt: -Infinity };
    this.publishListeningActivity(false, false);
    return this.listeningCapture.id;
  }

  /** Send only a UUID, independent generation, monotonic sequence and booleans; transport failures never affect microphone or interview flow. */
  publishListeningActivity(active, ended) {
    const capture = this.listeningCapture;
    if (!capture || this.closed || capture.sequence >= 2147483647) return false;
    capture.lastSentAt = this.now();
    return this.send({ type: "listening_activity", presentation_id: this.presentationId,
      capture_id: capture.id, capture_generation: capture.generation,
      sequence: ++capture.sequence, active, ended });
  }

  /** Observe local PCM energy only: voiced frames immediately resume contact, pauses wait 450 ms, and continuous voice sends at most one heartbeat per second. */
  observeListeningActivity(active, ended = false) {
    if (typeof active !== "boolean" || typeof ended !== "boolean" || !this.listeningCapture || this.closed) return false;
    if (ended) return this.endListeningCapture();
    const capture = this.listeningCapture;
    if (active) {
      this.cancelTimer(this.presenceTimer);
      this.presenceTimer = null;
      const changed = !capture.active;
      capture.active = true;
      if (changed || this.now() - capture.lastSentAt >= 1000) this.publishListeningActivity(true, false);
    } else if (capture.active && this.presenceTimer === null) {
      this.presenceTimer = this.schedule(/** Publish a natural pause only for the same still-current microphone observation lifecycle. */ () => {
        this.presenceTimer = null;
        if (this.closed || this.listeningCapture !== capture || !capture.active) return;
        capture.active = false;
        this.publishListeningActivity(false, false);
      }, 450);
    }
    return true;
  }

  /** End and tombstone a capture locally; neither an old quiet timer nor stale voice callbacks can revive its identity. */
  endListeningCapture() {
    this.cancelTimer(this.presenceTimer);
    this.presenceTimer = null;
    if (!this.listeningCapture) return false;
    const result = this.publishListeningActivity(false, true);
    this.listeningCapture = null;
    return result;
  }

  /** Initialize idle, republish valid control, and retry unavailable profiles only on a genuinely new connection. */
  onAvatarEvent(event) {
    if (this.closed || !event) return;
    if (event.type === "avatar_ready" && !event.detail) {
      const reconnected = !this.connected;
      const retryConnection = reconnected && this.hasConnected;
      this.connected = true;
      this.hasConnected = true;
      if (this.plan && this.plan.source !== "fallback" && this.validUntil <= this.now()) {
        this.lastModel = null;
        this.plan = fallbackPlan(this.request(), "expired");
      }
      this.publish();
      this.armProfileTimers();
      if (retryConnection && this.plan?.source === "fallback" && this.allowIdle && this.bootstrapIdle && !this.abort) {
        void this.setQuestion(this.question);
      } else void this.ensureIdlePlan();
    }
    if (event.type === "avatar_disconnected") {
      this.endListeningCapture();
      this.connected = false;
      this.idleAttempted = false;
      this.cancelRequest();
      this.cancelProfileTimers();
      this.utteranceId = "";
    }
    if (this.utteranceId && event.utterance_id === this.utteranceId) {
      if (event.type === "playback_started") this.state = "speaking";
      if (["playback_finished", "interrupted", "playback_failed"].includes(event.type)) {
        this.bindUtterance("");
        this.setState("listening");
      }
    }
  }

  /** Preview complete local dynamics explicitly labeled preview; no model is called and recorded fallback is not mislabeled. */
  preview(expression, intensity = 0.2) {
    let selected;
    try { selected = profile(defaultProfile(expression, intensity)); } catch { return false; }
    if (this.closed) return false;
    this.cancelRequest();
    this.cancelProfileTimers();
    ++this.generation;
    this.question = null;
    this.allowIdle = false;
    this.plan = fallbackPlan(this.request(), "manual_preview");
    this.plan.source = "preview";
    for (const state of STATES) this.plan.states[state] = { ...selected };
    this.validUntil = this.now() + VALID_MS;
    this.utteranceId = "";
    this.armProfileTimers();
    this.publish();
    this.status("preview", "manual_preview");
    return true;
  }

  /** Fence pending requests and native plans; explicit clear holds recorded fallback, while session reset may restore cached idle. */
  clear({ resumeIdle = false } = {}) {
    if (this.closed) return;
    this.endListeningCapture();
    this.cancelRequest();
    this.cancelProfileTimers();
    ++this.generation;
    this.question = null;
    this.plan = null;
    this.utteranceId = "";
    this.allowIdle = resumeIdle === true;
    this.publish();
  }

  /** Release all ownership, requests, clocks and cached control permanently; repeated closure is harmless. */
  close() {
    if (this.closed) return;
    this.clear();
    this.closed = true;
    this.lastModel = null;
  }
}
