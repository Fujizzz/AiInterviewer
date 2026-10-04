/**
 *
 * @module interview-camera-test
 * Responsibilities: Verify lifecycle of local camera resources using DOM, media tracks, and permission Promises.
 * Implementation: vm executes actual client; overrides explicit start/stop, rejection, late authorization, page exit, and device termination.
 * Related Modules: frontend/interview-camera.js; tests do not access camera or network, do not prove real hardware availability.
 * Declaration Index:
 * - Element: Minimal DOM and media playback stub.
 * - Element.constructor: Initialize display properties and event collection.
 * - Element.addEventListener: Record element events.
 * - Element.setAttribute: Record accessible attributes.
 * - Element.play: Simulate successful or failed play Promise.
 * - Track: Observable media track.
 * - Track.constructor: Initialize stop count and listeners.
 * - Track.addEventListener: Register device end handler.
 * - Track.removeEventListener: Remove device end handler.
 * - Track.stop: Increment stop count, do not simulate external ended event.
 * - Stream: Media stub containing one video track.
 * - Stream.constructor: Create observable track.
 * - Stream.getTracks: Return all tracks.
 * - Stream.getVideoTracks: Return video track.
 * - makePage: Establish independent script context and controllable authorization.
 * - makePage.authorize: Record requested constraints and return test-specified Promise.
 * - makePage.getElement: Strictly find actual template element.
 * - makePage.listen: Register page lifecycle events.
 * - makeDeferred: Construct explicitly fulfillable permission Promise.
 * - makeDeferred.executor: Capture resolve method for test-controlled authorization sequence.
 * - explicitPreview: Validate loading does not request device, only requests video and closes release.
 * - rejectedPermission: Validate error display on permission rejection and no retry.
 * - cancelledPermission: Validate late authorization after cancellation does not open preview.
 * - pageExit: Validate stopping active tracks on page exit.
 * - pendingPageExit: Validate late authorization after exit only releases.
 * - endedDevice: Validate cleanup and explicit notification after device termination.
 * - failedPlayback: Validate releasing acquired media after playback failure.
 * Variable Index:
 * - SCRIPT: Real camera client source code.
 * - HTML: Actual template, used for strict element reference validation.
 * Constraints:
 * Do not invoke models, do not use real devices; Stream, Element state are deterministic test stubs only.
 *
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { Console } from "node:console";
import { PassThrough } from "node:stream";

const SCRIPT = readFileSync(new URL("../frontend/interview-camera.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/agent.html", import.meta.url), "utf8");

/**
 *  Function: Replace page elements. Logic: Record script state, do not simulate browser layout or device.
 */
class Element {
  /**
 *  Input none; initialize DOM properties, event collection, and injectable play exception.
 */
  constructor() { this.hidden = false; this.srcObject = null; this.textContent = ""; this.events = {}; this.attributes = {}; this.playError = null; }
  /**
 *  Input event name and handler; save reference, output none.
 */
  addEventListener(name, handler) { this.events[name] = handler; }
  /**
 *  Input attribute name and value; record state for assertion, output none.
 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /**
 *  Read test-injected playError; return fulfilled Promise on success, otherwise throw simulated playback exception.
 */
  async play() { if (this.playError) throw this.playError; }
}

/**
 *  Function: Track stub. Logic: Record release count, device ended triggered explicitly by test.
 */
class Track {
  /**
 *  Input none; initialize stop count and listeners.
 */
  constructor() { this.stops = 0; this.events = {}; }
  /**
 *  Input: event name and handler; save listener, output: none.
 */
  addEventListener(name, handler) { this.events[name] = handler; }
  /**
 *  Input: event name and handler; remove only matching listener, output: none.
 */
  removeEventListener(name, handler) { if (this.events[name] === handler) delete this.events[name]; }
  /**
 *  Input: none; increment stop count, do not dispatch ended, following the interface behavior that active stop does not trigger this event.
 */
  stop() { this.stops += 1; }
}

/**
 *  Function: simulate a single video track stream; contains no actual audio or video content.
 */
class Stream {
  /**
 *  Input: none; construct a unique Track instance.
 */
  constructor() { this.track = new Track(); }
  /**
 *  Input: none; return all simulated tracks for resource release logic to call.
 */
  getTracks() { return [this.track]; }
  /**
 *  Input: none; return the video track for ended listener registration.
 */
  getVideoTracks() { return [this.track]; }
}

/**
 *  Input: none; create an isolated client context, return element, authorization control, and lifecycle events; no network or real device side effects.
 */
function makePage() {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) {
    assert.ok(!elements.has(match[1]), `Duplicate DOM ID: ${match[1]}`);
    elements.set(match[1], new Element());
  }
  const lifecycle = {};
  const media = { calls: [], result: null };
  /**
 *  Input: getUserMedia constraints; record the call and return a test-injected Promise without requesting real permissions.
 */
  function authorize(options) { media.calls.push(options); return media.result; }
  /**
 *  Input: ID; return the surrogate corresponding to the real template; fail immediately if missing.
 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /**
 *  Input: page event and handler; save for test triggering, do not register real window events.
 */
  function listen(name, handler) { lifecycle[name] = handler; }
  vm.runInNewContext(SCRIPT, {
    document: { getElementById: getElement }, window: { addEventListener: listen },
    navigator: { mediaDevices: { getUserMedia: authorize } },
    console: new Console(new PassThrough()),
  });
  return { elements, media, lifecycle, click: getElement("camera-toggle").events.click };
}

/**
 *  Input: none; return a Promise and its resolve method, simulating delayed user grant of camera permission.
 */
function makeDeferred() {
  let resolve;
  /**
 *  Input: resolve method of a Promise; save to outer scope, output: none.
 */
  function executor(accept) { resolve = accept; }
  const promise = new Promise(executor);
  return { promise, resolve };
}

/**
 *  Prerequisite: simulated device available; verify explicit request, audio=false, playback, and release on close, not representing real camera validation.
 */
async function explicitPreview() {
  const page = makePage();
  const stream = new Stream();
  assert.equal(page.media.calls.length, 0);
  page.media.result = Promise.resolve(stream);
  await page.click();
  assert.equal(page.media.calls.length, 1);
  assert.equal(page.media.calls[0].audio, false);
  assert.equal(page.media.calls[0].video, true);
  assert.equal(page.elements.get("self-video").srcObject, stream);
  assert.equal(page.elements.get("camera-toggle").attributes["aria-pressed"], "true");
  await page.click();
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.equal(page.elements.get("self-video").hidden, true);
}

/**
 *  Prerequisite: permission Promise rejected; verify error is clearly displayed and no repeated request occurs.
 */
async function rejectedPermission() {
  const page = makePage();
  page.media.result = Promise.reject(Object.assign(new Error("denied"), { name: "NotAllowedError" }));
  await page.click();
  assert.match(page.elements.get("camera-status").textContent, /Camera permission was denied/);
  assert.equal(page.media.calls.length, 1);
  assert.equal(page.elements.get("camera-toggle").textContent, "Enable camera");
}

/**
 *  Prerequisite: cancellation before authorization returns; verify late stream is stopped and preview remains closed.
 */
async function cancelledPermission() {
  const page = makePage();
  const deferred = makeDeferred();
  const stream = new Stream();
  page.media.result = deferred.promise;
  const opening = page.click();
  await page.click();
  deferred.resolve(stream);
  await opening;
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.equal(page.elements.get("camera-toggle").attributes["aria-pressed"], "false");
}

/**
 *  Prerequisite: actively playing locally; verify pagehide releases track and removes device event listeners.
 */
async function pageExit() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  await page.click();
  page.lifecycle.pagehide();
  assert.equal(stream.track.stops, 1);
  assert.equal(stream.track.events.ended, undefined);
  assert.equal(page.elements.get("self-video").srcObject, null);
}

/**
 *  Prerequisite: authorization returns after page leaves; verify late media is released and page cannot be updated to playing state.
 */
async function pendingPageExit() {
  const page = makePage();
  const deferred = makeDeferred();
  const stream = new Stream();
  page.media.result = deferred.promise;
  const opening = page.click();
  page.lifecycle.pagehide();
  deferred.resolve(stream);
  await opening;
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
}

/**
 *  Prerequisite: active device emits ended; verify end notification, resource release, and no automatic restart.
 */
async function endedDevice() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  await page.click();
  stream.track.events.ended();
  assert.equal(stream.track.stops, 1);
  assert.match(page.elements.get("camera-status").textContent, /Camera connection ended/);
  assert.equal(page.media.calls.length, 1);
}

/**
 *  Prerequisite: stream obtained but video.play rejected; verify no active track remains after error.
 */
async function failedPlayback() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  page.elements.get("self-video").playError = new Error("play failed");
  await page.click();
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.match(page.elements.get("camera-status").textContent, /Could not enable camera/);
}

test("preview is explicit, video-only, and releases on close", explicitPreview);
test("permission denial is visible without automatic retry", rejectedPermission);
test("cancel releases a late permission result", cancelledPermission);
test("pagehide releases the active camera", pageExit);
test("pagehide invalidates pending permission", pendingPageExit);
test("device ended clears preview without restarting", endedDevice);
test("playback failure releases acquired tracks", failedPlayback);
