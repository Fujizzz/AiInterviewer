/**
 * @module agent-client-test
 * Responsibilities: Validate real agent.js using deterministic clock, DOM, and WebSocket stubs, with no network access.
 * Implementation: Build element collection from actual HTML and shared navigation; execute client scripts in vm; observe timing, cache, and final state via events.
 * Related Modules: frontend/agent.html and agent.js; run with node --test, cannot prove actual vendor performance.
 * Declaration Index:
 * - Element: Minimal DOM event and display stub.
 * - Element.constructor: Initialize visible state and listeners.
 * - Element.addEventListener: Save named event handlers.
 * - Element.focus: Record focus, no window side effects.
 * - Element.showModal: Simulate native open state, do not simulate focus trap or background inert.
 * - Element.close: Clear open and dispatch close, to validate confirmation and cancellation state transitions.
 * - startPrepared: Open dialog via start entry, then explicitly submit prepared form.
 * - preparationInteraction: Dialog open/close does not establish connection; input preserved, confirmed close disables editing during active phase.
 * - preparationBoundaries: Hidden form, invalid role, and source failure prevent interview start.
 * - Element.fire: Dispatch current target and form cancel function to listeners.
 * - Element.fire.object1.preventDefault: Simulate preventing default form navigation.
 * - Socket: Record sent messages and allow test to explicitly deliver events.
 * - Socket.constructor: Initialize connection and instance recording.
 * - Socket.addEventListener: Save handler.
 * - Socket.send: Collect encoded commands, do not request model.
 * - Socket.close: Close connection, no implicit reconnection.
 * - Socket.emit: Explicitly deliver JSON message or close event.
 * - makePage: Create isolated script context and controllable clock.
 * - makePage.getElement: Allow querying only real template elements.
 * - makePage.now: Return deterministic monotonic clock.
 * - makePage.setTimer: Register timer callback, do not generate real interval.
 * - makePage.clearTimer: Remove displayed timer.
 * - makePage.uuid: Generate test-unique request identifier.
 * - makePage.ignoreEvent: Receive pagehide registration but do not operate window.
 * - makePage.addPageListener: Save page event handler, connect interview and voice controls.
 * - makePage.dispatchPageEvent: Pass interview control state to real voice coordinator.
 * - PageEvent: Provide only type and detail fields of CustomEvent.
 * - PageEvent.constructor: Create test page event, do not touch browser.
 *
 * - Element.append: Append option nodes.
 * - Element.replaceChildren: Clear old options.
 * - makePage.createElement: Create test option.
 * - makePage.fetch.object1.json: Return combined paginated JSON.
 * - makePage.fetch: Simulate personal pagination version interface, record calls without accessing model.
 * - makePage.ignoreError: Receive diagnostic logs, do not expose user content.
 * - makePage.tick: Advance test time and execute registered display callbacks.
 * - hello: Complete protocol announcement and return UUID of sent command.
 * - callback1: start sends version ID, maintains original budget, and displays real timing.
 * - callback2: cross-page load current, reject unavailable specified version.
 * - callback3: canceled old event cannot override state, version retained and timer cleaned.
 * - callback4: scoring becomes visible first, subsequent errors clearly report incomplete and retain score.
 * - callback5: normal question and final report end wait, restore controls.
 * - callback6: over-limit input rejected before request sent, avoid unnecessary model calls.
 * - callback7: when report model fails, provide clear prompt, do not mark deterministic summary as model success.
 * - callback8: stage event with invalid request ID is rejected and timing stops.
 * - callback9: current question enables voice control, recording prohibited during playback, allowed after playback ends.
 * - callback10: cancel releases recording, rejects late final text, restores interview entry.
 * - answeringPage: Use real client processor to enter synthesized current question.
 * - startCapture: Replace only device/vendor boundaries, start real voice coordinator.
 * - startCapture.page.captureType.prototype.start: Offline authorization/handshake makes capture ready.
 * - startCapture.page.captureType.prototype.end: Offline end capture, final text delivered separately.
 * - speechAnswerLifecycle: Verify captions, explicit end, single final transcription submission, and next question cleanup.
 * - speechAnswerBoundaries: Time limit must be confirmed; blank/failure/late transcriptions do not submit.
 * - answeringMCPPage: Use real handshake processor to enable auto-complete and enter current question.
 * - automaticSpeechCompletion: Detect event trigger flush, final credential generated only after MCP call.
 * - revokedSpeechCompletion: When credential missing due to revocation, retain final text and manually submit after explicit confirmation.
 *
 * - interviewProgressLifecycle: Snapshot budget advances during answer/wait, re-planning deducts already asked quota, disconnection freezes and deduplicates alerts.
 * - blockedResumeSelection: Cannot create interview connection without ready, unselected current, or unauthorized.
 * Variable Index:
 * - SCRIPT: Real client source code to be validated.
 * - HTML: Actual interview and shared navigation templates, used to verify client element references.
 * - VOICE_SCRIPT: Real voice coordinator source code, executed in isolated VM.
 * - PROGRESS_SCRIPT: Real budget/topic presentation source code, shares controllable clock, does not access backend.
 * - CAPTURE_SCRIPT: Real recording manager source code, does not open device or vendor connection.
 * Key State Notes:
 * Socket.OPEN is the connection-ready value; Socket.instances allows test to locate connections; each instance creates independent page.
 * makePage's timers/time are only for test clock; do not modify production display cycle, model timeout, or interview budget.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SCRIPT = readFileSync(new URL("../frontend/agent.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/agent.html", import.meta.url), "utf8") + readFileSync(new URL("../frontend/workspace-nav.html", import.meta.url), "utf8");
const VOICE_SCRIPT = readFileSync(new URL("../frontend/interview-voice.js", import.meta.url), "utf8");
const PROGRESS_SCRIPT = readFileSync(new URL("../frontend/interview-progress.js", import.meta.url), "utf8");
const CAPTURE_SCRIPT = readFileSync(new URL("../frontend/speech-capture.js", import.meta.url), "utf8");

/**
 * Provides CustomEvent data fields required by real client; does not simulate native event permissions.
 */
class PageEvent {
  /**
 * Stores event name and detail for dispatch by page state coordinator.
 */
  constructor(type, options) { this.type = type; this.detail = options.detail; }
}

/**
 * Minimal DOM stub maintaining only test-required text, disabled state, and events; does not simulate real browser layout.
 */
class Element {
  /**
 * Input: None; all fields initially empty, hidden set to true to simulate result area not yet appeared.
 */
  constructor() { this.value = ""; this.textContent = ""; this.hidden = true; this.disabled = false; this.listeners = {}; this.children = []; this.open = false; }
  /**
 * Input: Event name and handler; save reference, return nothing.
 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /**
 * Input: Node list; save version options, no layout or HTML parsing side effects.
 */
  append(...nodes) { this.children.push(...nodes); }
  /**
 * Clear previous options, preserve DOM object identity.
 */
  replaceChildren() { this.children = []; }
  /**
 * Record client request to focus, no operation on real window.
 */
  focus() { this.focused = true; }
  /**
 * No parameters; only simulate dialog.open, do not prove real browser focus range or native form validation.
 */
  showModal() { this.open = true; }
  /**
 * No parameters; simulate native close event, do not execute model or device requests.
 */
  close() { if (!this.open) return; this.open = false; this.fire("close"); }
  /**
 * Input: Event name; construct currentTarget and invoke registered handler; missing listener fails immediately.
 */
  fire(name) {
    this.listeners[name]({ currentTarget: this,
      /**
 * Serves only as form event interface, no navigation side effects.
 */
      preventDefault() {},
    });
  }
}

/**
 * Controllable WebSocket stub: no real connection; test must explicitly send hello and result events.
 */
class Socket {
  static OPEN = 1;
  static instances = [];
  /**
 * Input: URL; only record connection address; do not automatically trigger protocol events.
 */
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; this.listeners = {}; Socket.instances.push(this); }
  /**
 * Input: Event name and callback; record handler for emit invocation.
 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /**
 * Input: Encoded JSON; parse and save to assert command content.
 */
  send(text) { this.sent.push(JSON.parse(text)); }
  /**
 * Mark connection as closed; no implicit emission of other events.
 */
  close() { this.readyState = 3; }
  /**
 * Input: Event payload and optional event name (default: message); currentTarget fixed to this connection.
 */
  emit(data, name = "message") { this.listeners[name]({ data: JSON.stringify(data), currentTarget: this }); }
}

/**
 * Input: Synthesized versions/search/failure options; execute real script and wait for initial load; output test page and isolated capture class, enabling device boundary replacement, no real network.
 */
async function makePage(options = {}) {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  const timers = new Map();
  let time = 0;
  let timerId = 0;
  let serial = 0;
  const pageListeners = new Map();
  /**
 * Save event name and callback, allowing real voice coordinator to receive client eligibility.
 */
  function addPageListener(name, handler) { pageListeners.set(name, handler); }
  /**
 * Deliver test CustomEvent; do not send network or capture media.
 */
  function dispatchPageEvent(event) { pageListeners.get(event.type)?.(event); }
  /**
 * Input: Template ID; return corresponding element; client referencing non-existent element fails.
 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /**
 * Return controllable monotonic clock; do not read real time.
 */
  function now() { return time; }
  /**
 * Input: Timer callback; register and return ID; do not start system interval.
 */
  function setTimer(fn) { timers.set(++timerId, fn); return timerId; }
  /**
 * Input: ID; remove corresponding timer callback.
 */
  function clearTimer(id) { timers.delete(id); }
  /**
 * Return unique test request identifier within page; do not simulate backend UUID validation.
 */
  function uuid() { return `request-${++serial}`; }
  /**
 * Input: Page event registration parameters; test does not create real page lifecycle.
 */
  function ignoreEvent() {}
  /**
 * Input: Milliseconds to advance test clock; then execute current display callbacks.
 */
  function tick(ms) { time += ms; for (const fn of timers.values()) fn(); }

  getElement("job").value = "General AI / Software Engineer";
  getElement("limit").value = "5";
  getElement("duration").value = "30";
  getElement("probes").value = "2";
  const requests = [];
  const versions = options.versions || [{id:"version-a",status:"ready",label:"Current resume",is_current:true}];
  /**
 * Input: Request URL; output combined paginated JSON; fail with real HTTP status, no implicit downgrade.
 */
  async function fetch(url) {
    requests.push(url);
    const page = Number(new URL(url, "http://localhost").searchParams.get("page"));
    return { ok: !options.failure, status: options.failure || 200,
      /**
 * Return corresponding synthesized page; one record per page ensures test coverage of current on subsequent pages.
 */
      async json() { return {results: versions.slice(page-1,page),next: page < versions.length ? "next" : null}; },
    };
  }
  /**
 * Input: Tag name; output option node; no real window side effects.
 */
  function createElement() { return new Element(); }
  /**
 *  Receive diagnostic logs without printing synthesized version or voice content.
 */
  function ignoreError() {}
  const clientScript = PROGRESS_SCRIPT.replaceAll("export function", "function").replace("export class InterviewProgress", "class InterviewProgress")
    + CAPTURE_SCRIPT.replace("export class SpeechCapture", "class SpeechCapture")
    + VOICE_SCRIPT.replace('import { SpeechCapture } from "./speech-capture.js";', "").replace("export class InterviewVoice", "class InterviewVoice")
    + SCRIPT.replace('import { InterviewVoice } from "./interview-voice.js";', "").replace('import { InterviewProgress } from "./interview-progress.js";', "");
  const context = vm.createContext({
    document: { getElementById: getElement, createElement },
    window: { addEventListener: addPageListener, dispatchEvent: dispatchPageEvent },
    performance: { now }, crypto: { randomUUID: uuid },
    location: { protocol: "http:", host: "localhost", search: options.search || "" },
    WebSocket: Socket, TextEncoder, URLSearchParams, fetch, console: {error:ignoreError, info:ignoreError},
    setInterval: setTimer, clearInterval: clearTimer, clearTimeout, CustomEvent: PageEvent,
  });
  await vm.runInContext(clientScript, context);
  const voice = vm.runInContext("voice", context);
  const captureType = vm.runInContext("SpeechCapture", context);
  return { el: getElement, tick, timers, requests, voice, captureType };
}

/**
 *  Input connection and optional test message limit, simulate hello, return command ID sent by client or undefined.
 */
function hello(ws, limit = 262144) {
  ws.emit({ type: "hello", capabilities: ["prepare", "progress", "assessment"], max_message_bytes: limit });
  return ws.sent.at(-1)?.request_id;
}

/**
 *  Input loaded page, explicitly open preparation and confirm; do not override business handlers or create additional connections.
 */
function startPrepared(page) {
  page.el("open-preparation").fire("click");
  page.el("start-form").fire("submit");
}

/**
 *  Real script paired with native dialog stub; preserve settings toggle, no network, confirm once then close and disable all preparation entry points.
 */
async function preparationInteraction() {
  const page = await makePage();
  const count = Socket.instances.length;
  assert.equal(page.el("preparation-dialog").open, false);
  page.el("open-preparation").fire("click");
  assert.equal(page.el("preparation-dialog").open, true);
  assert.equal(page.el("resume-select").focused, true);
  page.el("job").value = "Edited target role";
  page.el("cancel-preparation").fire("click");
  assert.equal(page.el("preparation-dialog").open, false);
  assert.equal(page.el("open-preparation").focused, true);
  assert.equal(Socket.instances.length, count);
  page.el("interview-settings").fire("click");
  assert.equal(page.el("job").value, "Edited target role");
  page.el("preparation-dialog").close(); // Simulate native Esc close; keyboard trap validation handled separately by browser.
  assert.equal(page.el("interview-settings").focused, true);
  startPrepared(page);
  const ws = Socket.instances.at(-1); hello(ws);
  assert.equal(page.el("preparation-dialog").open, false);
  assert.equal(page.el("agent-status").focused, true);
  assert.equal(ws.sent[0].job_title, "Edited target role");
  assert.equal(page.el("open-preparation").disabled, true);
  assert.equal(page.el("interview-settings").disabled, true);
  startPrepared(page);
  assert.equal(Socket.instances.length, count + 1);
  assert.equal(ws.sent.length, 1);
  page.el("cancel-agent").fire("click");
  assert.equal(page.el("open-preparation").disabled, false);
  assert.equal(page.el("resume-select").value, "version-a");
}
test("preparation opens before start, preserves edits on dismiss, and locks during interview", preparationInteraction);

/**
 *  Submissions outside popups cannot connect; invalid inputs remain in popup; source failure still allows opening and shows clear guidance.
 */
async function preparationBoundaries() {
  const page = await makePage();
  const count = Socket.instances.length;
  page.el("start-form").fire("submit");
  assert.equal(Socket.instances.length, count);
  page.el("open-preparation").fire("click");
  page.el("job").value = "  ";
  page.el("start-form").fire("submit");
  assert.equal(page.el("preparation-dialog").open, true);
  assert.match(page.el("preparation-error").textContent, /target role/);
  assert.equal(Socket.instances.length, count);
  const empty = await makePage({failure:403});
  assert.equal(empty.el("open-preparation").disabled, false);
  empty.el("open-preparation").fire("click");
  assert.equal(empty.el("preparation-dialog").open, true);
  assert.match(empty.el("resume-selection-status").textContent, /Sign in again/);
  assert.equal(empty.el("start-agent").disabled, true);
  assert.equal(Socket.instances.length, count);
}
test("preparation rejects hidden submissions and keeps invalid or unavailable input visible", preparationBoundaries);

/**
 *  After metadata readiness, start only sends version ID; maintain phase timing and original budget, do not send name/email/content.
 */
test("selected ready version starts once with original budget and real timing", async () => {
  const page = await makePage();
  assert.equal(page.el("resume-select").value, "version-a");
  startPrepared(page);
  const ws = Socket.instances.at(-1); const id = hello(ws);
  assert.equal(ws.sent[0].type, "start");
  assert.equal(ws.sent[0].resume_version_id, "version-a");
  assert.equal("resume_text" in ws.sent[0], false);
  assert.equal(ws.sent[0].duration_minutes, 30);
  assert.equal(ws.sent[0].max_questions, 5);
  assert.equal(ws.sent[0].max_follow_up_per_topic, 2);
  assert.equal(page.el("resume-select").disabled, true);
  ws.emit({type:"progress", request_id:id,stage:"resume_parsing",state:"running"});
  page.tick(32000); assert.match(page.el("wait-time").textContent,/32 seconds/);
  startPrepared(page); assert.equal(ws.sent.length,1);
});

/**
 *  current may be located on subsequent pages; unready versions do not enter options, explicitly specified unavailable versions do not auto-select others.
 */
test("selection includes later pages and rejects an unavailable explicit version", async () => {
  const versions=[{id:"pending",status:"uploaded"},{id:"version-b",status:"ready",is_current:true}];
  const page=await makePage({versions});
  assert.equal(page.el("resume-select").value,"version-b");assert.equal(page.requests.length,2);
  const missing=await makePage({versions,search:"?resume_version_id=pending"});
  assert.equal(missing.el("resume-select").value,"");assert.equal(missing.el("start-agent").disabled,true);
  assert.match(missing.el("resume-selection-status").textContent,/unavailable/);
});

/**
 *  Cancelled late events do not resume interview; selected version remains, timer cleared.
 */
test("cancel clears timers and ignores late events while retaining the selected version", async () => {
  const page = await makePage();startPrepared(page);
  const ws = Socket.instances.at(-1);const id=hello(ws);
  page.el("cancel-agent").fire("click");
  ws.emit({type:"progress",request_id:id,stage:"resume_parsing",state:"running"});ws.emit({},"close");
  assert.match(page.el("agent-status").textContent,/Cancelled/);
  assert.equal(page.el("resume-select").value,"version-a");assert.equal(page.timers.size,0);
});

/**
 *  Disconnection during initial scoring without text report retains score and clearly indicates incomplete, stops timer.
 */
test("early assessment stays visible if report connection fails", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "assessment", request_id: id, assessment: { overall_score: 3, competencies: {} } });
  assert.equal(page.el("report-panel").hidden, false);
  assert.match(page.el("score").textContent, /3.00/);
  ws.emit({}, "close");
  assert.match(page.el("report-summary").textContent, /not complete/);
  assert.equal(page.timers.size, 0);
});

/**
 *  First question allows answer and stops timer; after final round report success, retain real summary and restore start button.
 */
test("question and finished response release pending UI state", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  let id = hello(ws);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "q1", text: "What did you implement?", target_competency: "ownership", difficulty: 2 } });
  assert.equal(page.el("start-recording").disabled, false);
  assert.equal(page.timers.size, 0);
  const capture = await startCapture(page);
  assert.equal(page.el("voice-enabled").disabled, true);
  await page.voice.finishAnswer();
  capture.onFinal("Synthetic answer.", 10);
  id = ws.sent.at(-1).request_id;
  ws.emit({ type: "finished", request_id: id, result: { final_report: {
    overall_score: 3, competencies: {}, summary: "Fixture final report.",
  } } });
  assert.equal(page.el("report-summary").textContent, "Fixture final report.");
  assert.equal(page.el("start-agent").disabled, false);
  assert.equal(page.timers.size, 0);
});

/**
 *  Simulate server-side announcement with low upper limit, verify only client-side boundaries; must not send over-limited content or leave timers running.
 */
test("oversized command is rejected before send", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  hello(ws, 5);
  assert.equal(ws.sent.length, 0);
  assert.match(page.el("agent-status").textContent, /server limit/);
  assert.equal(page.timers.size, 0);
});

/**
 *  When original report rollback is marked by server, frontend must explicitly inform origin, without changing valid score.
 */
test("report fallback is explicitly labeled", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "finished", request_id: id, result: {
    report_narrative_status: "fallback",
    final_report: { overall_score: 3, competencies: {}, summary: "Deterministic summary." },
  } });
  assert.match(page.el("report-summary").textContent, /model report failed/);
  assert.match(page.el("score").textContent, /3.00/);
});

/**
 *  Within same connection, error request_id must not alter current phase; should clearly fail and clear timer.
 */
test("foreign request progress is rejected", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  hello(ws);
  ws.emit({ type: "progress", request_id: "foreign", stage: "resume_parsing", state: "running" });
  assert.match(page.el("agent-status").textContent, /does not match the current request/);
  assert.equal(page.timers.size, 0);
});

/**
 *  New client shares current question and answer boundary with real voice coordinator; playback must not trigger recording start.
 */
test("voice playback blocks recording and releases it on completion", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  assert.equal(page.el("start-recording").disabled, true);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "voice-q", text: "Describe one contribution.", difficulty: 1, dialogue_action: "project" } });
  page.voice.busy = true;
  page.voice.utteranceId = "voice-u";
  page.voice.updateControls();
  await page.voice.record();
  assert.equal(page.voice.capture, null);
  assert.equal(ws.sent.length, 1);
  assert.equal(page.el("start-recording").disabled, true);
  page.voice.avatarEvent({ type: "playback_finished", utterance_id: "voice-u" });
  assert.equal(page.el("start-recording").disabled, false);
  assert.match(page.el("voice-status").textContent, /Speech has stopped/);
  const capture = await startCapture(page);
  await page.voice.finishAnswer();
  capture.onFinal("Public fixture answer.", 10);
  assert.equal(ws.sent.at(-1).type, "answer");
  assert.equal(page.el("start-recording").disabled, true);
});

/**
 *  Cancellation during collection/ending renders old callbacks invalid; real resource release path executed, no device or external service opened.
 */
test("cancellation releases capture and ignores a late final transcript", async () => {
  const page = await answeringPage();
  const capture = await startCapture(page);
  capture.onPartial("Unconfirmed fixture draft.");
  await page.voice.finishAnswer();
  page.el("cancel-agent").fire("click");
  assert.equal(capture.closed, true);
  capture.onFinal("Late cancelled answer.", 10);
  assert.equal(page.ws.sent.length, 1);
  assert.equal(page.el("start-agent").disabled, false);
  assert.equal(page.el("start-recording").disabled, true);
  assert.equal(page.voice.capture, null);
  assert.equal(page.el("answer-subtitles").hidden, true);
  assert.equal(page.el("voice-enabled").disabled, false);
});

/**
 *  Generate real client current question; only WebSocket/version interface uses offline stubs, questions enter real business processor.
 */
async function answeringPage() {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "speech-q", text: "Describe one contribution.", difficulty: 1, dialogue_action: "project" } });
  return { ...page, ws };
}

/**
 *  Input independent VM page; replace only device/supplier startup and flush, preserve real coordinator and close resource logic.
 * Return collection object to let test explicitly deliver partial/final/error callbacks, without proving actual ASR performance.
 */
async function startCapture(page) {
  /**
 *  Offline authorization and handshake complete without creating microphone track.
 */
  page.captureType.prototype.start = async function syntheticStart() { this.recording = true; };
  /**
 *  Offline end only sets collection flag; final text must be delivered separately by test, do not fabricate synchronous success.
 */
  page.captureType.prototype.end = async function syntheticEnd() { this.recording = false; };
  await page.voice.record();
  return page.voice.capture;
}

/**
 *  Validate two-phase boundary between explicit end and final text, plain text captions, duplicate prevention, and next-question cleanup.
 */
async function speechAnswerLifecycle() {
  const page = await answeringPage();
  assert.doesNotMatch(HTML, /id="(?:answer|answer-form|submit-answer|transcript-draft)"/);
  const capture = await startCapture(page);
  capture.onPartial("<img src=x onerror=alert(1)> public fixture");
  assert.equal(page.el("answer-subtitle").textContent, "<img src=x onerror=alert(1)> public fixture");
  assert.equal(page.el("answer-subtitles").hidden, false);
  assert.equal(page.ws.sent.length, 1);
  await page.el("stop-recording").onclick();
  await page.voice.finishAnswer();
  assert.equal(page.el("stop-recording").disabled, true);
  assert.equal(page.ws.sent.length, 1);
  capture.onFinal("  Final spoken answer.  ", 10);
  assert.equal(page.ws.sent.length, 2);
  assert.equal(page.ws.sent[1].question_id, "speech-q");
  assert.equal(page.ws.sent[1].answer_text, "Final spoken answer.");
  assert.equal(page.el("answer-subtitle").textContent, "Final spoken answer.");
  capture.onFinal("Duplicate final.", 10);
  await page.voice.record();
  assert.equal(page.ws.sent.length, 2);
  assert.equal(page.voice.capture, null);
  page.ws.emit({ type: "question", request_id: page.ws.sent[1].request_id, question_index: 2,
    question: { question_id: "next-q", text: "Next question", difficulty: 1, dialogue_action: "project" } });
  assert.equal(page.el("answer-subtitles").hidden, true);
}
test("finish answer waits for final speech and submits once while subtitles remain visible", speechAnswerLifecycle);

/**
 *  Final from original collection timeout does not represent user confirmation of end; button still required after final text, blank/failure does not submit.
 */
async function speechAnswerBoundaries() {
  const page = await answeringPage();
  let capture = await startCapture(page);
  capture.onFinal("Timed-limit speech.", 10);
  assert.equal(page.ws.sent.length, 1);
  assert.equal(page.el("stop-recording").disabled, false);
  await page.voice.finishAnswer();
  assert.equal(page.ws.sent[1].answer_text, "Timed-limit speech.");
  const empty = await answeringPage();
  capture = await startCapture(empty);
  await empty.voice.finishAnswer();
  capture.onFinal("  ", 10);
  assert.equal(empty.ws.sent.length, 1);
  assert.equal(empty.el("start-recording").disabled, false);
  assert.equal(empty.el("answer-subtitles").hidden, true);
  capture = await startCapture(empty);
  capture.onPartial("Unconfirmed words");
  await empty.voice.finishAnswer();
  capture.onError("speech_timeout: public fixture failure");
  capture.onFinal("Late after failed capture", 10);
  assert.equal(empty.ws.sent.length, 1);
  assert.match(empty.el("voice-status").textContent, /speech_timeout/);
  assert.equal(empty.el("start-recording").disabled, false);
}
test("capture limit requires end confirmation and empty or failed transcripts never submit", speechAnswerBoundaries);

/**
 *  Prohibit start when no input available; failure does not select alternative version; these are simulated permission responses, not replacing real service permissions.
 */
async function blockedResumeSelection() {
  const before = Socket.instances.length;
  for (const options of [
    { versions: [] },
    { versions: [{ id: "waiting", status: "uploaded", is_current: true }] },
    { versions: [{ id: "ready", status: "ready", is_current: false }] },
    { failure: 403 },
  ]) {
    const page = await makePage(options);
    assert.equal(page.el("start-agent").disabled, true);
    startPrepared(page);
    assert.equal(Socket.instances.length, before);
  }
}
test("missing ready selection or authorization cannot start an interview", blockedResumeSelection);

/**
 *  Real page script with deterministic clock: progress will not automatically end interview; disconnection freezes, existing exceptions deduplicated and cleared without removing backend.
 */
async function interviewProgressLifecycle() {
  const page = await makePage(); startPrepared(page);
  const ws = Socket.instances.at(-1); const request = hello(ws);
  const data = { type: "question", request_id: request, question: { question_id: "q1", text: "Explain your project", difficulty: 3 }, question_index: 3,
    interview_state: { question_index: 3, stage: "project_deep_dive", status: "active", remaining_seconds: 125, clock_started_at: 9999999 },
    interview_plan: { max_questions: 40, version: 2, topics: [{ topic_key: "a", objective: "Explain ownership", expected_questions: 4 }, { topic_key: "b", objective: "Discuss design", expected_questions: 3 }] },
    topic_progress: { a: { questions_asked: 2, status: "active" }, b: { questions_asked: 1, status: "completed" } },
    decision_logs: [{ decision_id: "d1", fallback_used: true, failed_retrieval_sources: ["project"] }] };
  ws.emit(data);
  assert.equal(page.el("interview-remaining").textContent, "2:05");
  assert.equal(page.el("interview-question-progress").textContent, "第 3 题 / 预计约 5 题");
  assert.equal(page.el("interview-stage").textContent, "项目深入");
  assert.equal(page.el("interview-alerts").children.length, 2);
  page.tick(10000); assert.equal(page.el("interview-remaining").textContent, "1:55");
  assert.equal(ws.sent.length, 1);
  ws.emit({}, "close"); const frozen = page.el("interview-remaining").textContent;
  page.tick(300000); assert.equal(page.el("interview-remaining").textContent, frozen);
  assert.equal(page.timers.size, 0);
  assert.equal(page.el("interview-alerts").children.length, 3);
  page.el("clear-agent").fire("click"); assert.equal(page.el("interview-progress").hidden, true);
  assert.equal(page.el("interview-alerts").children.length, 0);
}
test("interview progress follows budget snapshots and freezes on disconnect without ending automatically", interviewProgressLifecycle);

/**
 *  Input none; execute real MCP handshake and current question processor, only WebSocket/device use isolated stubs.
 */
async function answeringMCPPage() {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  ws.emit({ type: "hello", capabilities: ["progress", "answer_completion_mcp"], max_message_bytes: 262144 });
  const initialize = ws.sent.at(-1);
  assert.equal(initialize.method, "initialize");
  ws.emit({ jsonrpc: "2.0", id: initialize.id, result: { protocolVersion: "2025-06-18", capabilities: { tools: {} } } });
  assert.equal(ws.sent.at(-2).method, "notifications/initialized");
  const start = ws.sent.at(-1);
  assert.equal(start.type, "start");
  ws.emit({ type: "question", request_id: start.request_id, question_index: 1,
    question: { question_id: "speech-q", text: "Describe one contribution.", difficulty: 1, dialogue_action: "project" } });
  assert.equal(page.voice.completionEnabled, true);
  return { ...page, ws };
}

/**
 *  After verification, backend detection event only concludes; complete final text and credentials trigger MCP call; duplicate/old question events cannot be resubmitted.
 */
async function automaticSpeechCompletion() {
  const page = await answeringMCPPage();
  const capture = await startCapture(page);
  assert.equal(capture.options.questionId, "speech-q");
  const count = page.ws.sent.length;
  capture.options.onCompletion();
  assert.equal(capture.recording, false);
  assert.equal(page.ws.sent.length, count);
  capture.onFinal("Complete answer. That's all.", 10, "server-signed-fixture");
  const call = page.ws.sent.at(-1);
  assert.equal(call.method, "tools/call");
  assert.equal(call.params.name, "finish_current_answer");
  assert.equal(call.params.arguments.answer_text, "Complete answer. That's all.");
  assert.equal(call.params.arguments.completion_receipt, "server-signed-fixture");
  capture.options.onCompletion();
  capture.onFinal("Late duplicate", 10, "fixture");
  assert.equal(page.ws.sent.length, count + 1);
  page.ws.emit({ jsonrpc: "2.0", id: call.id, result: { isError: false, structuredContent: {
    type: "question", request_id: call.id, question_index: 2,
    question: { question_id: "next-q", text: "Next question", difficulty: 1, dialogue_action: "project" }
  } } });
  capture.options.onCompletion();
  assert.equal(page.voice.question.question_id, "next-q");
  assert.equal(page.voice.finishRequested, false);
  assert.equal(page.ws.sent.length, count + 1);
}
test("automatic answer completion flushes then submits one MCP tool call", automaticSpeechCompletion);

/**
 *  Verification supplement invalidates final credentials: automatic branching cancelled, full caption preserved, manual answer used after user confirmation.
 */
async function revokedSpeechCompletion() {
  const page = await answeringMCPPage();
  const capture = await startCapture(page);
  const count = page.ws.sent.length;
  capture.options.onCompletion();
  capture.onFinal("That's all. One more detail.", 10);
  assert.equal(page.ws.sent.length, count);
  assert.equal(page.voice.finishRequested, false);
  assert.match(page.el("voice-status").textContent, /Additional speech/);
  await page.voice.finishAnswer();
  assert.equal(page.ws.sent.length, count + 1);
  assert.equal(page.ws.sent.at(-1).type, "answer");
  assert.equal(page.ws.sent.at(-1).answer_text, "That's all. One more detail.");
}
test("supplemented final transcript cancels automatic submission", revokedSpeechCompletion);
