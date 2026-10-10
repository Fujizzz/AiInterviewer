/**
 * @module agent-client-test
 * Responsibilities: Validate real agent.js using deterministic clock, DOM, and WebSocket stubs, with no network access.
 * Implementation: Build element collection from actual HTML and shared navigation; execute client scripts in vm; observe timing, cache, and final state via events.
 * Related Modules: frontend/agent.html and agent.js; run with node --test, cannot prove actual vendor performance.
 * Declaration Index:
 * - PageEvent: Provides CustomEvent data fields required by real client; does not simulate native event permissions.
 * - PageEvent.constructor: Stores event name and detail for dispatch by page state coordinator.
 * - Element: Minimal DOM stub maintaining only test-required text, disabled state, and events; does not simulate real browser layout.
 * - Element.constructor: Input: None; all fields initially empty, hidden set to true to simulate result area not yet appeared.
 * - Element.addEventListener: Input: Event name and handler; save reference, return nothing.
 * - Element.append: Input: Node list; save version options, no layout or HTML parsing side effects.
 * - Element.replaceChildren: Clear previous options, preserve DOM object identity.
 * - Element.focus: Record client request to focus, no operation on real window.
 * - Element.showModal: No parameters; only simulate dialog.open, do not prove real browser focus range or native form validation.
 * - Element.close: No parameters; simulate native close event, do not execute model or device requests.
 * - Element.fire: Input: Event name; construct currentTarget and invoke registered handler; missing listener fails immediately.
 * - Element.fire.object1.preventDefault: Serves only as form event interface, no navigation side effects.
 * - Socket: Controllable WebSocket stub: no real connection; test must explicitly send hello and result events.
 * - Socket.constructor: Input: URL; only record connection address; do not automatically trigger protocol events.
 * - Socket.addEventListener: Input: Event name and callback; record handler for emit invocation.
 * - Socket.send: Input: Encoded JSON; parse and save to assert command content.
 * - Socket.close: Mark connection as closed; no implicit emission of other events.
 * - Socket.emit: Input: Event payload and optional event name (default: message); currentTarget fixed to this connection.
 * - makePage: Input: Synthesized versions/search/failure options; execute real script and wait for initial load; output test page and isolated capture class, enabling device boundary replacement, no real network.
 * - makePage.addPageListener: Save event name and callback, allowing real voice coordinator to receive client eligibility.
 * - makePage.dispatchPageEvent: Deliver test CustomEvent; do not send network or capture media.
 * - makePage.getElement: Input: Template ID; return corresponding element; client referencing non-existent element fails.
 * - makePage.now: Return controllable monotonic clock; do not read real time.
 * - makePage.setTimer: Input: Timer callback; register and return ID; do not start system interval.
 * - makePage.clearTimer: Input: ID; remove corresponding timer callback.
 * - makePage.uuid: Return unique test request identifier within page; do not simulate backend UUID validation.
 * - makePage.ignoreEvent: Input: Page event registration parameters; test does not create real page lifecycle.
 * - makePage.tick: Input: Milliseconds to advance test clock; then execute current display callbacks.
 * - makePage.fetch: Input: Request URL; output combined paginated JSON; fail with real HTTP status, no implicit downgrade.
 * - makePage.fetch.object1.json: Return corresponding synthesized page; one record per page ensures test coverage of current on subsequent pages.
 * - makePage.createElement: Input: Tag name; output option node; no real window side effects.
 * - makePage.ignoreError: Receive diagnostic logs without printing synthesized version or voice content.
 * - makePage.loadAvatarModule: Supply disabled offline avatar configuration at the dynamic module boundary; no renderer or network is created.
 * - hello: Input connection and optional test message limit, simulate hello, return command ID sent by client or undefined.
 * - startPrepared: Input loaded page, explicitly open preparation and confirm; do not override business handlers or create additional connections.
 * - preparationInteraction: Real script paired with native dialog stub; preserve settings toggle, no network, confirm once then close and disable all preparation entry points.
 * - preparationBoundaries: Submissions outside popups cannot connect; invalid inputs remain in popup; source failure still allows opening and shows clear guidance.
 * - callback1: After metadata readiness, start only sends version ID; maintain phase timing and original budget, do not send name/email/content.
 * - callback2: current may be located on subsequent pages; unready versions do not enter options, explicitly specified unavailable versions do not auto-select others.
 * - callback3: Cancelled late events do not resume interview; selected version remains, timer cleared.
 * - callback4: Disconnection during initial scoring without text report retains score and clearly indicates incomplete, stops timer.
 * - callback5: First question allows answer and stops timer; after final round report success, retain real summary and restore start button.
 * - callback6: Simulate server-side announcement with low upper limit, verify only client-side boundaries; must not send over-limited content or leave timers running.
 * - callback7: When original report rollback is marked by server, frontend must explicitly inform origin, without changing valid score.
 * - callback8: Within same connection, error request_id must not alter current phase; should clearly fail and clear timer.
 * - callback9: New client shares current question and answer boundary with real voice coordinator; playback must not trigger recording start.
 * - callback10: Cancellation during collection/ending renders old callbacks invalid; real resource release path executed, no device or external service opened.
 * - answeringPage: Generate real client current question; only WebSocket/version interface uses offline stubs, questions enter real business processor.
 * - startCapture: Input independent VM page; replace only device/supplier startup and flush, preserve real coordinator and close resource logic.
 * - startCapture.page.captureType.prototype.start: Offline authorization and handshake complete without creating microphone track.
 * - startCapture.page.captureType.prototype.end: Offline end only sets collection flag; final text must be delivered separately by test, do not fabricate synchronous success.
 * - speechAnswerLifecycle: Validate two-phase boundary between explicit end and final text, plain text captions, duplicate prevention, and next-question cleanup.
 * - speechAnswerBoundaries: Verify full final capture-limit text auto-submits, empty final speech records Skip, and recognition failures cannot submit late partial/final text.
 * - blockedResumeSelection: Prohibit start when no input available; failure does not select alternative version; these are simulated permission responses, not replacing real service permissions.
 * - interviewProgressLifecycle: Real page script with deterministic clock: progress will not automatically end interview; disconnection freezes, existing exceptions deduplicated and cleared without removing backend.
 * - answeringMCPPage: Input none; execute real MCP handshake and current question processor, only WebSocket/device use isolated stubs.
 * - automaticSpeechCompletion: After verification, backend detection event only concludes; complete final text and credentials trigger MCP call; duplicate/old question events cannot be resubmitted.
 * - revokedSpeechCompletion: Verify supplemented complete final text uses ordinary reviewed answer submission; no invalid semantic receipt is sent and repeated closure cannot duplicate it.
 * - discardPage: Complete explicit discard only on the matching server acknowledgement; no real database is used.
 * - automaticClocks: Verify preparation starts automatically, voice refreshes the silence clock, and one full final answer is sent.
 * - evaluatedEarlyEnd: Verify an early-end report uses final words and never submits a normal answer or generates another question.
 * - queuedEarlyEnd: Verify saving during a pending request queues finish until its result and does not start its new microphone.
 * - discardDuringWait: Verify no-save end may preempt pending work, ignores its obsolete result, and keeps connection until acknowledgement.
 * - continueAfterChoice: Verify dismissal resumes existing preparation without sending a backend end request or duplicate capture.
 * - completedDuringChoice: Verify a final result arriving while choosing end remains discardable or savable without another model command.
 * - observeAvatarLifecycle: Replace only the renderer connection boundary with offline resources; retain the real disconnect and interview event handlers.
 * - avatarStartBoundary: Open or dismiss preparation without connecting, then connect once after a valid confirmed start.
 * - avatarTerminalCleanup: Release renderer resources after terminal backend events and transport failure, without changing report or answer behavior.
 * - avatarEndChoice: Keep a live renderer while the end choice is resumable, and release it when a completed result arrives during the dialog.
 * - avatarSessionRestart: Start a second interview after renderer cleanup without permanently closing the voice coordinator.
 * - installSyntheticAudio: Functionality: Attach synthetic device/ASR boundaries to a page running all real capture code. Inputs: VM page, final provider text and optional semantic receipt. Outputs: PCM/track/worklet observations. Logic: Execute the real worklet/resampler; fake only browser hardware and provider messages. Constraints: No SpeechCapture.start/end override, microphone, network, or assertion of ASR accuracy.
 * - installSyntheticAudio.SyntheticContext: Functionality: Provide a connected browser audio graph for the actual capture implementation. Logic: State/stream connections are simulated; the real worklet processes every supplied frame. Constraints: This class never opens hardware, plays audio or changes production sample rates.
 * - installSyntheticAudio.SyntheticContext.constructor: Initialize a suspended 48kHz audio graph and validated module loader without hardware.
 * - installSyntheticAudio.SyntheticContext.constructor.this.audioWorklet.addModule: Validate the actual worklet module path without fetching external code.
 * - installSyntheticAudio.SyntheticContext.resume: Complete the browser context resume boundary without emitting audio.
 * - installSyntheticAudio.SyntheticContext.createMediaStreamSource: Return a source whose connection matches the actual capture graph interface.
 * - installSyntheticAudio.SyntheticContext.createMediaStreamSource.object1.connect: Accept the real worklet node as the capture input connection.
 * - installSyntheticAudio.SyntheticContext.close: Mark context release for microphone cleanup assertions.
 * - installSyntheticAudio.SyntheticNode: Functionality: Couple main-thread ports to the actual processor in the VM. Logic: Every float input is resampled by the real implementation; flush acknowledgements synchronously follow transferred PCM, matching ordered browser message delivery. Constraints: Only node/port plumbing is simulated; samples and finalization are not replaced.
 * - installSyntheticAudio.SyntheticNode.constructor: Construct the actual registered processor and connect ordered flush/PCM ports.
 * - installSyntheticAudio.SyntheticNode.constructor.this.port.postMessage: Deliver the capture's flush control to the real worklet.
 * - installSyntheticAudio.SyntheticNode.constructor.this.processor.port.postMessage: Deliver real PCM and flushed messages to the current capture callback.
 * - installSyntheticAudio.SyntheticNode.connect: Accept destination connection; no output device is created.
 * - installSyntheticAudio.SyntheticNode.disconnect: Release the synthetic graph connection after the real capture ends.
 * - installSyntheticAudio.SyntheticNode.feed: Process actual float samples and assert the real worklet never echoes them to output.
 * - installSyntheticAudio.SyntheticNode.feed.callback1: Each audio output sample must remain silent.
 * - installSyntheticAudio.SyntheticSpeechSocket: Functionality: Simulate only STT transport/provider output, retaining actual capture encoding. Logic: Handshake and final messages arrive asynchronously; all PCM frames are preserved. Constraints: Recognized text is explicitly scripted, never inferred from sine waves.
 * - installSyntheticAudio.SyntheticSpeechSocket.constructor: Initialize the transport double and enqueue its asynchronous hello after handlers install.
 * - installSyntheticAudio.SyntheticSpeechSocket.constructor.callback1: Emit the real protocol's initial speech envelope.
 * - installSyntheticAudio.SyntheticSpeechSocket.deliver: Deliver one provider event to the actual SpeechCapture message parser.
 * - installSyntheticAudio.SyntheticSpeechSocket.send: Record real PCM and respond only to validated start/stop controls.
 * - installSyntheticAudio.SyntheticSpeechSocket.send.callback1: Complete only this speech handshake.
 * - installSyntheticAudio.SyntheticSpeechSocket.send.callback2: Return the scripted complete final ASR text and optional receipt.
 * - installSyntheticAudio.SyntheticProcessorBase: Provide the real processor's base port without accessing the browser audio engine.
 * - installSyntheticAudio.SyntheticProcessorBase.constructor: Initialize the outbound worklet port for later connection to actual SpeechCapture.
 * - installSyntheticAudio.SyntheticProcessorBase.constructor.this.port.postMessage: Ignore preconnection messages; no input exists yet.
 * - installSyntheticAudio.object1.navigator.mediaDevices.getUserMedia: Return a synthetic microphone track; the actual capture must release it.
 * - installSyntheticAudio.object1.navigator.mediaDevices.getUserMedia.object1.getTracks: Expose one verifiable track at the browser cleanup boundary.
 * - installSyntheticAudio.object1.navigator.mediaDevices.getUserMedia.object1.getTracks.object1.stop: Record real capture cleanup without accessing hardware.
 * - installSyntheticAudio.object1.registerProcessor: Preserve exactly the processor registered by the real worklet source.
 * - syntheticSamples: Functionality: Build known PCM-producing float frames with controlled amplitude. Inputs: Sample count and amplitude. Outputs: Float32Array of 440Hz samples at 48kHz. Logic: A deterministic sine wave distinguishes voice-level energy from zero silence. Constraints: This signal is synthetic audio, not a linguistic ASR ground truth.
 * - syntheticSamples.callback1: Generate deterministic bounded synthetic input.
 * - syntheticPCMLifecycle: Verify real capture/worklet/resampling/PCM and five-second silence closure across voiced, quiet and empty audio.
 * - syntheticPCMSemanticEnd: Verify a semantic notification with real PCM capture flushes the full transcript before one MCP call.
 * - syntheticPCMSemanticEnd.callback1: Sum original and flushed tail PCM bytes.
 * - syntheticPCMEarlyEnd: Verify saving during real PCM capture preserves final ASR text, while discard sends no answer or report.
 * Variable Index:
 * - SCRIPT: Real client source code to be validated.
 * - HTML: Actual interview and shared navigation templates, used to verify client element references.
 * - VOICE_SCRIPT: Real voice coordinator source code, executed in isolated VM.
 * - PROGRESS_SCRIPT: Real budget/topic presentation source code, shares controllable clock, does not access backend.
 * - CAPTURE_SCRIPT: Actual recording manager, exercised with synthetic device/provider boundaries.
 * - RESAMPLER_SCRIPT: Actual float-to-16kHz PCM16 resampler used by synthetic audio tests.
 * - WORKLET_SCRIPT: Actual worklet and flush protocol executed with synthetic audio frames.
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
const RESAMPLER_SCRIPT = readFileSync(new URL("../frontend/pcm-resampler.js", import.meta.url), "utf8");
const WORKLET_SCRIPT = readFileSync(new URL("../frontend/speech-worklet.js", import.meta.url), "utf8");

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
  close() { if (!this.open) return; this.open = false; if (this.listeners.close) this.fire("close"); }
  /**
 * Input: Event name; construct currentTarget and invoke registered handler; missing listener fails immediately.
 */
  fire(name) {
    return this.listeners[name]({ currentTarget: this,
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
  function tick(ms) { time += ms; for (const fn of [...timers.values()]) fn(); }

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
  /** Return an explicitly unavailable renderer without importing the browser-only vendor player. */
  async function loadAvatarModule() {
    return { async loadAvatarConfiguration() { return { enabled: false }; } };
  }
  const clientScript = PROGRESS_SCRIPT.replaceAll("export function", "function").replace("export class InterviewProgress", "class InterviewProgress")
    + CAPTURE_SCRIPT.replace("export class SpeechCapture", "class SpeechCapture")
    + VOICE_SCRIPT.replace('import { SpeechCapture } from "./speech-capture.js";', "").replace("export class InterviewVoice", "class InterviewVoice")
      .replace('import("/stream-demo/pixel-player.js")', "loadAvatarModule()")
    + SCRIPT.replace('import { InterviewVoice } from "./interview-voice.js";', "").replace('import { InterviewProgress, reviewScore } from "./interview-progress.js";', "");
  const context = vm.createContext({
    document: { getElementById: getElement, createElement },
    window: { addEventListener: addPageListener, dispatchEvent: dispatchPageEvent },
    performance: { now }, crypto: { randomUUID: uuid },
    location: { protocol: "http:", host: "localhost", search: options.search || "" },
    WebSocket: Socket, TextEncoder, URLSearchParams, fetch, loadAvatarModule, AbortController,
    console: {error:ignoreError, info:ignoreError},
    setInterval: setTimer, clearInterval: clearTimer, setTimeout, clearTimeout, CustomEvent: PageEvent,
  });
  await vm.runInContext(clientScript, context);
  const voice = vm.runInContext("voice", context);
  const captureType = vm.runInContext("SpeechCapture", context);
  return { el: getElement, tick, timers, requests, voice, captureType, context };
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
  discardPage(page, ws);
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
  discardPage(page, ws);
  ws.emit({type:"progress",request_id:id,stage:"resume_parsing",state:"running"});ws.emit({},"close");
  assert.match(page.el("agent-status").textContent,/removed from history/);
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
  assert.equal(page.voice.phase, "preparing");
  assert.equal(page.timers.size, 1);
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
  assert.equal(page.voice.capture, null);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "voice-q", text: "Describe one contribution.", difficulty: 1, dialogue_action: "project" } });
  page.voice.busy = true;
  page.voice.utteranceId = "voice-u";
  page.voice.updateControls();
  await page.voice.record();
  assert.equal(page.voice.capture, null);
  assert.equal(ws.sent.length, 1);
  assert.equal(page.voice.capture, null);
  page.voice.avatarEvent({ type: "playback_finished", utterance_id: "voice-u" });
  assert.equal(page.voice.busy, false);
  assert.match(page.el("voice-status").textContent, /automatically/);
  const capture = await startCapture(page);
  await page.voice.finishAnswer();
  capture.onFinal("Public fixture answer.", 10);
  assert.equal(ws.sent.at(-1).type, "answer");
  assert.equal(page.voice.capture, null);
});

/**
 *  Cancellation during collection/ending renders old callbacks invalid; real resource release path executed, no device or external service opened.
 */
test("cancellation releases capture and ignores a late final transcript", async () => {
  const page = await answeringPage();
  const capture = await startCapture(page);
  capture.onPartial("Unconfirmed fixture draft.");
  await page.voice.finishAnswer();
  discardPage(page, page.ws);
  assert.equal(capture.closed, true);
  capture.onFinal("Late cancelled answer.", 10);
  assert.equal(page.ws.sent.length, 2);
  assert.equal(page.el("start-agent").disabled, false);
  assert.equal(page.voice.capture, null);
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
  page.tick(10000);
  await new Promise(setImmediate); // Drain cross-realm startup promises before advancing inactivity.
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
  page.tick(5000);
  await page.voice.finishAnswer();
  assert.equal(page.voice.finishRequested, true);
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
 * Verify full final capture-limit text auto-submits, empty final speech records Skip, and recognition failures cannot submit late partial/final text.
 */
async function speechAnswerBoundaries() {
  const page = await answeringPage();
  let capture = await startCapture(page);
  capture.onFinal("Timed-limit speech.", 10);
  assert.equal(page.ws.sent.length, 2);
  assert.equal(page.ws.sent[1].answer_text, "Timed-limit speech.");
  const empty = await answeringPage();
  capture = await startCapture(empty);
  empty.tick(5000);
  capture.onFinal("  ", 10);
  assert.equal(empty.ws.sent.length, 2);
  assert.equal(empty.ws.sent.at(-1).type, "skip");
  assert.equal(empty.el("answer-subtitles").hidden, true);
  const failed = await answeringPage();
  capture = await startCapture(failed);
  capture.onPartial("Unconfirmed words");
  failed.tick(5000);
  capture.onError("speech_timeout: public fixture failure");
  capture.onFinal("Late after failed capture", 10);
  assert.equal(failed.ws.sent.length, 1);
  assert.match(failed.el("voice-status").textContent, /speech_timeout/);
  assert.equal(failed.voice.phase, "error");
}
test("capture limit auto-submits, empty speech records unanswered and failures remain explicit", speechAnswerBoundaries);

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
 * Verify supplemented complete final text uses ordinary reviewed answer submission; no invalid semantic receipt is sent and repeated closure cannot duplicate it.
 */
async function revokedSpeechCompletion() {
  const page = await answeringMCPPage();
  const capture = await startCapture(page);
  const count = page.ws.sent.length;
  capture.options.onCompletion();
  capture.onFinal("That's all. One more detail.", 10);
  assert.equal(page.voice.finishRequested, false);
  await page.voice.finishAnswer();
  assert.equal(page.ws.sent.length, count + 1);
  assert.equal(page.ws.sent.at(-1).type, "answer");
  assert.equal(page.ws.sent.at(-1).answer_text, "That's all. One more detail.");
}
test("supplemented final transcript uses the complete ordinary answer without an invalid receipt", revokedSpeechCompletion);

/** Complete explicit discard only on the matching server acknowledgement; no real database is used. */
function discardPage(page, ws) {
  page.el("cancel-agent").fire("click");
  page.el("end-without-save").fire("click");
  const command = ws.sent.at(-1);
  assert.equal(command.type, "discard");
  ws.emit({ type: "discarded", request_id: command.request_id });
}

/** Verify preparation starts automatically, voice refreshes the silence clock, and one full final answer is sent. */
async function automaticClocks() {
  const page = await answeringPage();
  assert.doesNotMatch(HTML, /id="(?:start-recording|stop-recording)"/);
  assert.match(page.el("answer-countdown").textContent, /10s/);
  const capture = await startCapture(page);
  assert.equal(capture.recording, true);
  page.tick(4000);
  capture.options.onActivity();
  page.tick(4000);
  assert.equal(capture.recording, true);
  page.tick(1000);
  assert.equal(capture.recording, false, JSON.stringify({phase:page.voice.phase,deadline:page.voice.deadline,eligible:page.voice.eligible,timers:page.timers.size,subtitle:page.el("answer-countdown").textContent}));
  capture.onFinal("Complete spoken answer.", 10);
  assert.equal(page.ws.sent.at(-1).type, "answer");
  assert.equal(page.ws.sent.at(-1).answer_text, "Complete spoken answer.");
  capture.onFinal("Late repeated answer", 10);
  assert.equal(page.ws.sent.length, 2);
}
test("automatic preparation and silence timers replace answer buttons", automaticClocks);

/** Verify an early-end report uses final words and never submits a normal answer or generates another question. */
async function evaluatedEarlyEnd() {
  const page = await answeringPage();
  const capture = await startCapture(page);
  capture.onPartial("Partial words");
  page.el("cancel-agent").fire("click");
  page.tick(20000);
  assert.equal(capture.recording, true);
  const finishing = page.el("end-and-save").fire("click");
  assert.equal(capture.recording, false);
  assert.equal(page.ws.sent.length, 1);
  capture.onFinal("Final words", 10);
  await finishing;
  const command = page.ws.sent.at(-1);
  assert.equal(command.type, "finish");
  assert.equal(command.question_id, "speech-q");
  assert.equal(command.answer_text, "Final words");
  assert.equal(page.ws.sent.length, 2);
  assert.equal(page.voice.capture, null);
}
test("early end evaluates final current speech and bypasses another answer round", evaluatedEarlyEnd);

/** Verify saving during a pending request queues finish until its result and does not start its new microphone. */
async function queuedEarlyEnd() {
  const page = await makePage(); startPrepared(page);
  const ws = Socket.instances.at(-1); const request = hello(ws);
  page.el("cancel-agent").fire("click");
  await page.el("end-and-save").fire("click");
  assert.equal(ws.sent.length, 1);
  ws.emit({ type: "question", request_id: request, question_index: 1,
    question: { question_id: "new-q", text: "Question", difficulty: 1 } });
  assert.equal(ws.sent.at(-1).type, "finish");
  assert.equal("answer_text" in ws.sent.at(-1), false);
  assert.equal(page.voice.question, null);
  assert.equal(page.voice.capture, null);
}
test("save during model wait queues one finish and skips new capture", queuedEarlyEnd);

/** Verify no-save end may preempt pending work, ignores its obsolete result, and keeps connection until acknowledgement. */
async function discardDuringWait() {
  const page = await makePage(); startPrepared(page);
  const ws = Socket.instances.at(-1); const request = hello(ws);
  page.el("cancel-agent").fire("click");
  page.el("end-without-save").fire("click");
  const discard = ws.sent.at(-1);
  assert.equal(discard.type, "discard");
  assert.equal(ws.readyState, 1);
  ws.emit({ type: "question", request_id: request, question_index: 1,
    question: { question_id: "late-q", text: "Late question", difficulty: 1 } });
  assert.equal(page.voice.question, null);
  ws.emit({ type: "discarded", request_id: discard.request_id });
  assert.equal(ws.readyState, 3);
  assert.equal(page.timers.size, 0);
  assert.equal(page.el("report-panel").hidden, true);
}
test("discard preempts pending requests and awaits confirmed deletion", discardDuringWait);

/** Verify dismissal resumes existing preparation without sending a backend end request or duplicate capture. */
async function continueAfterChoice() {
  const page = await answeringPage();
  page.el("cancel-agent").fire("click");
  assert.equal(page.el("end-interview-dialog").open, true);
  page.tick(1000);
  page.el("continue-interview").fire("click");
  assert.equal(page.el("end-interview-dialog").open, false);
  assert.equal(page.voice.suspended, false);
  assert.equal(page.voice.phase, "preparing");
  assert.equal(page.ws.sent.length, 1);
}
test("end choice can be dismissed without changing interview state", continueAfterChoice);

/** Verify a final result arriving while choosing end remains discardable or savable without another model command. */
async function completedDuringChoice() {
  const page = await makePage(); startPrepared(page);
  const ws = Socket.instances.at(-1); const request = hello(ws);
  page.el("cancel-agent").fire("click");
  ws.emit({ type: "finished", request_id: request, result: { final_report: { overall_score: null, competencies: {}, summary: "Completed while choosing" } } });
  assert.equal(page.el("end-interview-dialog").open, true);
  assert.equal(ws.readyState, 1);
  await page.el("end-and-save").fire("click");
  assert.equal(page.el("report-summary").textContent, "Completed while choosing");
  assert.equal(ws.sent.length, 1);
  assert.equal(ws.readyState, 3);
}
test("final report race preserves the user's end choice", completedDuringChoice);

/** Replace only the renderer transport with observable offline resources; keep real voice cleanup.
 * No module import, device, TTS or expression request is needed for interview lifecycle assertions.
 */
function observeAvatarLifecycle(page) {
  const observations = { connections: 0, resources: [] };
  page.voice.connectAvatar = async function () {
    observations.connections += 1;
    const resource = { playerCloses: 0, presentationCloses: 0, sent: [] };
    observations.resources.push(resource);
    this.player = {
      ready: true,
      send(message) { resource.sent.push(message); return true; },
      close() { this.ready = false; resource.playerCloses += 1; },
    };
    this.presentation = {
      setActive() {}, setState() {}, setQuestion() {}, clear() {}, endListeningCapture() {},
      close() { resource.presentationCloses += 1; },
    };
  };
  return observations;
}

/** Verify that preparation and rejected inputs do not reserve a renderer or start an interview. */
async function avatarStartBoundary() {
  const page = await makePage();
  const observation = observeAvatarLifecycle(page);
  const initialSockets = Socket.instances.length;
  page.el("start-form").fire("submit");
  page.el("open-preparation").fire("click");
  page.el("cancel-preparation").fire("click");
  page.el("interview-settings").fire("click");
  page.el("job").value = "   ";
  page.el("start-form").fire("submit");
  assert.equal(observation.connections, 0);
  assert.equal(Socket.instances.length, initialSockets);
  assert.equal(page.el("preparation-dialog").open, true);
  page.el("job").value = "Software Engineer";
  page.el("start-form").fire("submit");
  assert.equal(observation.connections, 1);
  const ws = Socket.instances.at(-1);
  const request = hello(ws);
  startPrepared(page);
  ws.emit({ type: "question", request_id: request, question_index: 1,
    question: { question_id: "avatar-q", text: "Describe your contribution.", difficulty: 1 } });
  assert.equal(observation.connections, 1);
  assert.equal(ws.sent.length, 1);
  assert.ok(page.requests.every((url) => url.startsWith("/api/resume-versions/")));
  discardPage(page, ws);
}
test("avatar connects once only after confirmed valid interview start", avatarStartBoundary);

/** Check the actual terminal cleanup for completion, discard, cancellation and agent transport errors. */
async function avatarTerminalCleanup(t) {
  for (const cause of ["finished", "discarded", "cancelled", "error", "close"]) {
    await t.test(cause, async () => {
      const page = await makePage();
      const observation = observeAvatarLifecycle(page);
      startPrepared(page);
      const ws = Socket.instances.at(-1);
      const request = hello(ws);
      const epoch = page.voice.avatarConnectionEpoch;
      if (cause === "finished") {
        ws.emit({ type: cause, request_id: request,
          result: { final_report: { overall_score: 3, competencies: {}, summary: "Complete." } } });
        assert.equal(page.el("report-summary").textContent, "Complete.");
      } else if (cause === "discarded") discardPage(page, ws);
      else if (cause === "error") ws.emit({ type: cause, request_id: request, code: "offline_failure", detail: "Fixture failure." });
      else if (cause === "close") ws.emit({}, "close");
      else ws.emit({ type: cause, request_id: request });
      assert.equal(observation.connections, 1);
      assert.equal(observation.resources[0].playerCloses, 1);
      assert.equal(observation.resources[0].presentationCloses, 1);
      assert.equal(page.voice.player, null);
      assert.equal(page.voice.presentation, null);
      assert.equal(page.voice.avatarConnecting, false);
      assert.ok(page.voice.avatarConnectionEpoch > epoch);
      assert.equal(page.voice.closed, false);
      assert.equal(page.timers.size, 0);
      assert.ok(page.requests.every((url) => url.startsWith("/api/resume-versions/")));
    });
  }
}
test("terminal interview paths release the avatar without model calls", avatarTerminalCleanup);

/** A resumable end choice keeps the renderer; a final result releases it even before choosing save. */
async function avatarEndChoice() {
  const page = await makePage();
  const observation = observeAvatarLifecycle(page);
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const request = hello(ws);
  page.el("cancel-agent").fire("click");
  assert.equal(observation.resources[0].playerCloses, 0);
  page.el("continue-interview").fire("click");
  assert.equal(observation.resources[0].playerCloses, 0);
  assert.equal(observation.connections, 1);
  page.el("cancel-agent").fire("click");
  ws.emit({ type: "finished", request_id: request,
    result: { final_report: { overall_score: null, competencies: {}, summary: "Complete during choice." } } });
  assert.equal(page.el("end-interview-dialog").open, true);
  assert.equal(ws.readyState, Socket.OPEN);
  assert.equal(observation.resources[0].playerCloses, 1);
  assert.equal(observation.resources[0].presentationCloses, 1);
  assert.equal(page.voice.player, null);
  await page.el("end-and-save").fire("click");
  assert.equal(page.el("report-summary").textContent, "Complete during choice.");
  assert.equal(observation.resources[0].playerCloses, 1);
  assert.equal(ws.sent.length, 1);
}
test("end choice keeps the avatar until the interview actually completes", avatarEndChoice);

/** A terminal session cleanup permits a new confirmed interview and a fresh renderer connection. */
async function avatarSessionRestart() {
  const page = await makePage();
  const observation = observeAvatarLifecycle(page);
  startPrepared(page);
  const first = Socket.instances.at(-1);
  const request = hello(first);
  first.emit({ type: "finished", request_id: request,
    result: { final_report: { overall_score: null, competencies: {}, summary: "First complete." } } });
  assert.equal(page.voice.closed, false);
  assert.equal(observation.resources[0].playerCloses, 1);
  startPrepared(page);
  const second = Socket.instances.at(-1);
  hello(second);
  assert.notEqual(second, first);
  assert.equal(observation.connections, 2);
  assert.equal(observation.resources[1].playerCloses, 0);
  assert.equal(page.voice.closed, false);
  assert.equal(second.sent[0].type, "start");
  discardPage(page, second);
  assert.equal(observation.resources[1].playerCloses, 1);
}
test("a new interview reconnects the avatar after the previous one ends", avatarSessionRestart);

/** Functionality: Attach synthetic device/ASR boundaries to a page running all real capture code.
 * Inputs: VM page, final provider text and optional semantic receipt. Outputs: PCM/track/worklet observations.
 * Logic: Execute the real worklet/resampler; fake only browser hardware and provider messages.
 * Constraints: No SpeechCapture.start/end override, microphone, network, or assertion of ASR accuracy.
 */
function installSyntheticAudio(page, finalText, receipt = null) {
  let Processor;
  const observations = { frames: [], node: null, trackStopped: false, speechSocket: null };
  /** Functionality: Provide a connected browser audio graph for the actual capture implementation.
   * Logic: State/stream connections are simulated; the real worklet processes every supplied frame.
   * Constraints: This class never opens hardware, plays audio or changes production sample rates.
   */
  class SyntheticContext {
    /** Initialize a 48kHz browser-like input state and module-loading stub. */
    constructor() { this.state = "suspended"; this.destination = {}; this.audioWorklet = {
      /** Validate the actual worklet module path without fetching external code. */
      addModule: async (path) => assert.equal(path, "/stream-demo/speech-worklet.js"),
    }; }
    /** Complete the browser context resume boundary without emitting audio. */
    async resume() { this.state = "running"; }
    /** Return a source whose connection matches the actual capture graph interface. */
    createMediaStreamSource() { return {
      /** Accept the real worklet node as the capture input connection. */
      connect: (node) => { observations.node = node; },
    }; }
    /** Mark context release for microphone cleanup assertions. */
    async close() { this.state = "closed"; }
  }
  /** Functionality: Couple main-thread ports to the actual processor in the VM.
   * Logic: Every float input is resampled by the real implementation; flush acknowledgements
   * synchronously follow transferred PCM, matching ordered browser message delivery.
   * Constraints: Only node/port plumbing is simulated; samples and finalization are not replaced.
   */
  class SyntheticNode {
    /** Instantiate the registered worklet and connect both message-port directions. */
    constructor() {
      this.processor = new Processor();
      this.port = {
        onmessage: null,
        /** Deliver the capture's flush control to the real worklet. */
        postMessage: (data) => this.processor.port.onmessage({ data }),
      };
      /** Deliver real PCM and flushed messages to the current capture callback. */
      this.processor.port.postMessage = (data) => this.port.onmessage?.({ data });
      observations.node = this;
    }
    /** Accept destination connection; no output device is created. */
    connect() {}
    /** Release the synthetic graph connection after the real capture ends. */
    disconnect() {}
    /** Process actual float samples and assert the real worklet never echoes them to output. */
    feed(samples) {
      const output = new Float32Array(samples.length).fill(1);
      this.processor.process([[samples]], [[output]]);
      assert.ok(output.every(/** Each audio output sample must remain silent. */ (sample) => sample === 0));
    }
  }
  /** Functionality: Simulate only STT transport/provider output, retaining actual capture encoding.
   * Logic: Handshake and final messages arrive asynchronously; all PCM frames are preserved.
   * Constraints: Recognized text is explicitly scripted, never inferred from sine waves.
   */
  class SyntheticSpeechSocket extends Socket {
    /** Announce the speech handshake after the capture installs its handlers. */
    constructor(url) {
      super(url);
      observations.speechSocket = this;
      queueMicrotask(/** Emit the real protocol's initial speech envelope. */ () => this.deliver({ type: "hello" }));
    }
    /** Deliver one provider event to the actual SpeechCapture message parser. */
    deliver(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
    /** Record real PCM and respond only to validated start/stop controls. */
    send(data) {
      if (typeof data !== "string") { observations.frames.push(new Uint8Array(data).slice()); return; }
      const command = JSON.parse(data);
      if (command.type === "start") queueMicrotask(/** Complete only this speech handshake. */ () => this.deliver({ type: "started" }));
      else {
        assert.equal(command.type, "stop");
        queueMicrotask(/** Return the scripted complete final ASR text and optional receipt. */ () => this.deliver({ type: "final", text: finalText, finalization_ms: 1, completion_receipt: receipt }));
      }
    }
  }
  /** Provide the real processor's base port without accessing the browser audio engine. */
  class SyntheticProcessorBase {
    /** Initialize the outbound port that SyntheticNode will connect to the actual capture. */
    constructor() { this.port = { /** Ignore preconnection messages; no input exists yet. */ postMessage() {} }; }
  }
  Object.assign(page.context, {
    navigator: { mediaDevices: {
      /** Return a synthetic microphone track; the actual capture must release it. */
      getUserMedia: async () => ({
        /** Expose one verifiable track at the browser cleanup boundary. */
        getTracks: () => [{
          /** Record real capture cleanup without accessing hardware. */
          stop: () => { observations.trackStopped = true; },
        }],
      }),
    } },
    AudioContext: SyntheticContext, AudioWorkletNode: SyntheticNode,
    AudioWorkletProcessor: SyntheticProcessorBase, sampleRate: 48000,
    /** Preserve exactly the processor registered by the real worklet source. */
    registerProcessor: (_name, type) => { Processor = type; },
    WebSocket: SyntheticSpeechSocket,
  });
  vm.runInContext(RESAMPLER_SCRIPT.replace("export class PCM16Resampler", "class PCM16Resampler")
    + WORKLET_SCRIPT.replace('import { PCM16Resampler } from "./pcm-resampler.js";', ""), page.context);
  return observations;
}

/** Functionality: Build known PCM-producing float frames with controlled amplitude.
 * Inputs: Sample count and amplitude. Outputs: Float32Array of 440Hz samples at 48kHz.
 * Logic: A deterministic sine wave distinguishes voice-level energy from zero silence.
 * Constraints: This signal is synthetic audio, not a linguistic ASR ground truth.
 */
function syntheticSamples(count, amplitude) {
  return Float32Array.from({ length: count }, /** Generate deterministic bounded synthetic input. */ (_, index) => amplitude * Math.sin(2 * Math.PI * 440 * index / 48000));
}

/** Verify real capture/worklet/resampling/PCM and five-second silence closure across voiced, quiet and empty audio. */
async function syntheticPCMLifecycle() {
  for (const scenario of [{ amplitude: 0.2, text: "Scripted complete ASR answer." }, { amplitude: 0, text: "" }]) {
    const page = await answeringPage();
    const audio = installSyntheticAudio(page, scenario.text);
    page.tick(9999);
    assert.equal(page.voice.capture, null);
    page.tick(1);
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    const capture = page.voice.capture;
    assert.equal(capture.recording, true);
    page.tick(4000);
    audio.node.feed(syntheticSamples(4800, scenario.amplitude));
    assert.equal(audio.frames.length, 1);
    assert.equal(audio.frames[0].byteLength, 3200);
    if (scenario.amplitude) {
      const frame = new DataView(audio.frames[0].buffer);
      assert.ok(Math.abs(frame.getInt16(100, true)) > 100);
      page.tick(4999);
      assert.equal(capture.recording, true);
      audio.node.feed(syntheticSamples(4800, 0));
      page.tick(1);
    } else page.tick(1000);
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    assert.equal(capture.closed, true);
    assert.equal(audio.trackStopped, true);
    assert.equal(page.voice.capture, null);
    assert.equal(page.ws.sent.length, 2);
    assert.equal(page.ws.sent.at(-1).type, scenario.text ? "answer" : "skip");
    if (scenario.text) assert.equal(page.ws.sent.at(-1).answer_text, scenario.text);
    audio.speechSocket.deliver({ type: "final", text: "Obsolete final words" });
    assert.equal(page.ws.sent.length, 2);
  }
}
test("synthetic 48kHz audio traverses real worklet, PCM capture and silence closure", syntheticPCMLifecycle);

/** Verify a semantic notification with real PCM capture flushes the full transcript before one MCP call. */
async function syntheticPCMSemanticEnd() {
  const page = await answeringMCPPage();
  const audio = installSyntheticAudio(page, "Scripted full answer. I am done.", "fixture-receipt");
  page.tick(10000);
  await new Promise(setImmediate); await new Promise(setImmediate);
  audio.node.feed(syntheticSamples(4900, 0.2));
  const before = page.ws.sent.length;
  audio.speechSocket.deliver({ type: "answer_completion", question_id: "speech-q" });
  audio.speechSocket.deliver({ type: "answer_completion", question_id: "speech-q" });
  await new Promise(setImmediate); await new Promise(setImmediate);
  assert.equal(page.ws.sent.length, before + 1);
  assert.equal(page.ws.sent.at(-1).params.name, "finish_current_answer");
  assert.equal(page.ws.sent.at(-1).params.arguments.answer_text, "Scripted full answer. I am done.");
  assert.equal(audio.frames.reduce(/** Sum original and flushed tail PCM bytes. */ (sum, frame) => sum + frame.byteLength, 0), Math.floor(4900 / 3) * 2);
  assert.equal(audio.trackStopped, true);
}
test("synthetic audio semantic completion flushes PCM tail before one MCP submission", syntheticPCMSemanticEnd);

/** Verify saving during real PCM capture preserves final ASR text, while discard sends no answer or report. */
async function syntheticPCMEarlyEnd() {
  for (const save of [true, false]) {
    const page = await answeringPage();
    const audio = installSyntheticAudio(page, "Scripted final early-end speech.");
    page.tick(10000);
    await new Promise(setImmediate); await new Promise(setImmediate);
    audio.node.feed(syntheticSamples(5000, 0.2));
    page.el("cancel-agent").fire("click");
    if (save) {
      await page.el("end-and-save").fire("click");
      assert.equal(page.ws.sent.at(-1).type, "finish");
      assert.equal(page.ws.sent.at(-1).answer_text, "Scripted final early-end speech.");
    } else {
      page.el("end-without-save").fire("click");
      const command = page.ws.sent.at(-1);
      assert.equal(command.type, "discard");
      assert.equal("answer_text" in command, false);
      page.ws.emit({ type: "discarded", request_id: command.request_id });
      await new Promise(setImmediate);
    }
    assert.equal(page.ws.sent.length, 2);
    assert.equal(audio.trackStopped, true);
    assert.equal(page.voice.capture, null);
  }
}
test("synthetic PCM supports both evaluated early end and discard without extra answers", syntheticPCMEarlyEnd);
