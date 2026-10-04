/**
 *
 * @module resumes-client-test
 * Responsibilities: Validate data flow and failure boundaries of the real resume management client, without requesting external services or production databases.
 * Implementation: Actual template ID, minimal DOM, real Response/ReadableStream/UTF-8 decoding, and explicit REST stubs.
 * Related Modules: frontend/resumes.js, resumes.html; cannot substitute for real browser layout, vendor, or permission testing.
 * Declaration Index:
 * - Element: Implements only DOM interfaces used by the client.
 * - Element.constructor: Initializes controls and observable child nodes.
 * - Element.constructor.toggle: Maintains simulated CSS class collection.
 * - Element.addEventListener: Registers events.
 * - Element.click: Records native selector triggers, does not read disk or create files.
 * - Element.focus: Records client-initiated focus targets, does not simulate browser layout.
 * - Element.scrollIntoView: Records client navigation targets, does not simulate scroll distance.
 * - Element.append: Appends pure DOM nodes.
 * - Element.replaceChildren: Clears old cards.
 * - Element.querySelectorAll: Recursively returns buttons.
 * - Element.setAttribute: Saves aria state.
 * - Element.closest: Tests button returns self.
 * - makePage: Executes actual client and returns isolated page and REST state.
 * - makePage.getElement: Rejects queries for elements outside the template.
 * - makePage.createElement: Creates simulated DOM nodes.
 * - makePage.fetch: Records requests, asserts CSRF, and executes mocked version of interface.
 * - makePage.fetch.byOriginal: Reads synthesized original by source.
 * - makePage.fetch.byId: Matches synthesized records by URL version ID.
 * - makePage.sectionNavigation: Returns synthesized sidebar node.
 * - makePage.selectors: Language selector is empty, avoiding dependency on internationalization module.
 * - makePage.t: Returns translatable key and parameters for assertion.
 * - makePage.confirm: Explicitly approves simulated delete confirmation, no real deletion.
 * - makePage.replaceState: Records explicit hash modifications, does not access browser.
 * - workspaceDraft: Validates partition switching preserves draft, no requests, and programmatic positioning displays panel first.
 * - makePage.ignore: Replaces window events and logs, does not access user device.
 * - makePage.run: Calls actual client functions.
 * - json: Generates JSON HTTP response.
 * - stream: Builds byte-by-byte NDJSON, validates cross-byte Chinese decoding.
 * - stream.start: Pushes bytes and ends stream.
 * - record: Generates fictional full version metadata.
 * - textUpload: Validates text save does not invoke parsing and safely displays body.
 * - textUpload.isParse: Filters parsing requests.
 * - pdfUpload: Validates PDF size limits, multipart upload, and no automatic parsing after save.
 * - pdfModes: Validates PDF save and two explicit parsing modes: default and advanced.
 * - pdfModes.isParse: Locates explicit extraction requests.
 * - failedStream: Validates failure does not adopt model intermediate content, does not auto-retry.
 * - failedStream.isParse: Counts extraction attempts.
 * - incompleteStream: Validates missing final state cannot declare success.
 * - currentAndDelete: Validates current selection, protects against conflicts, confirms deletion, and cleans preview.
 * - editionSave: Validates independence of unit saves and recommended value types.
 * - dirtyCancel.page.context.window.confirm: Rejects synthetic discard confirmation.
 * - dirtyCancel: Validates canceling departure preserves input and does not send current selection request.
 * - nearbyParse: Validates attachment near button parses only once after upload.
 * - singleUploadEntry: Validates select upload, cancel, busy lock, and parsing tools displayed by status.
 * - fileUploadFailure: Upload failure retains unsaved edits, does not implicitly retry or parse.
 * - fileUploadFailure.rejectUpload: Simulates POST failure only on original, other reads still go through REST stub.
 * - preventDefault: Replaces default behavior cancellation for file selection test events, does not manipulate browser.
 * - suggestedSlots: Suggests backfilling unsaved draft, confirmed values do not overwrite, manual clear does not re-extract.
 * - failedSave: Save failure retains unit input and leave protection, explicit empty array does not become unknown.
 * - failedSave.rejectEdition: Simulates save HTTP 400, does not retry other write operations.
 * - invalidSlots: Invalid values do not trigger save requests.
 * - profileSave: Personal profile uses only PATCH and CSRF for saving, backfills server-side results.
 * - savedRecommendations: Explicitly requests save version, safely displays position, does not auto-recommend; clears results after edit.
 * - recommendationFailure: Source failure does not generate alternative card, does not auto-retry, restores operation controls.
 * - recommendationReasons: Bilingual reasons safely displayed, language switch does not re-request, final order remains unchanged.
 * - makePage.language: Reads synthesized language state, does not call real browser language.
 * - recommendationSelection: Completes full selection across history pages for ready versions, does not auto-recommend or modify current.
 * - recommendationGuidance: Displays appropriate next action when no resume, pending parse, or missing recommendation field.
 * - recommendationUnsaved: Canceling version switch preserves input/selection, shows nearby save button.
 * - recommendationUnsaved.rejectSwitch: Simulates user rejecting discarding unsaved content.
 * Variable Index:
 * - SCRIPT: Actual client source code.
 * - HTML: Actual template, used to validate element contract.
 * Constraints:
 * makePage's items/requests/errors store only synthesized data; real service permissions are covered by Django tests.
 *
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const SCRIPT = readFileSync(new URL("../frontend/resumes.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/resumes.html", import.meta.url), "utf8");
/**
 *  No parameters, no return value; dummy for file selection test events with no side effects, does not simulate browser navigation.
 */
function preventDefault() {}

/**
 *  Function: Minimal DOM state; Logic: Implements only real script dependencies; Constraint: No layout or browser permission simulation.
 */
class Element {
  /**
 *  Input tag name, output empty node state; use real Set for classList to observe error markers.
 */
  constructor(tag = "div") {
    this.tagName = tag; this.value = ""; this.textContent = ""; this.disabled = false;
    this.files = []; this.dataset = {}; this.children = []; this.listeners = {}; this.attributes = {};
    this.classList = new Set();
    /**
 *  Input class name and boolean value, use caller's Set as state; no browser side effects.
 */
    function toggle(key, enabled) { if (enabled) this.add(key); else this.delete(key); }
    this.classList.toggle = toggle;
  }
  /**
 *  Input event and handler, save without executing automatically.
 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /**
 *  No input; record input.click selector request, does not simulate system file selection or trigger change.
 */
  click() { this.clicked = true; }
  /**
 *  Record active focus request; input optional browser parameters, no real page side effects.
 */
  focus(options) { this.focused = options; }
  /**
 *  Record scroll positioning; input optional browser parameters, does not simulate dimensions or animations.
 */
  scrollIntoView(options) { this.scrolled = options; }
  /**
 *  Input node list, save children, does not interpret strings as HTML.
 */
  append(...nodes) { this.children.push(...nodes); }
  /**
 *  Clear all child nodes; does not remove template elements.
 */
  replaceChildren() { this.children = []; }
  /**
 *  Input selector, returns recursive button set for internal script use only.
 */
  querySelectorAll(selector) {
    assert.equal(selector, "button");
    const result = [];
    for (const child of this.children) { if (child.tagName === "button") result.push(child); result.push(...child.querySelectorAll(selector)); }
    return result;
  }
  /**
 *  Save attribute string to check aria-busy.
 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /**
 *  Return this node for action delegation and suggestion panel observation; does not simulate real ancestor selection or layout.
 */
  closest() { return this; }
}
/**
 *  Input data and status code, output real Response; simulates interface JSON encoding.
 */
function json(data, status = 200) { return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } }); }
/**
 *  Input event array, output real byte-by-byte UTF-8 stream; does not access network.
 */
function stream(events) {
  const bytes = new TextEncoder().encode(events.map(JSON.stringify).join("\n") + "\n");
  /**
 *  Input stream controller, enqueues bytes one by one to verify TextDecoder boundary handling.
 */
  function start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); }
  return new Response(new ReadableStream({ start }));
}
/**
 *  Input ID/status, output test-only metadata and body; does not use real candidate data.
 */
function record(id, status = "ready") {
  return { id, status, label: "<script>label</script>", original_name: status === "uploaded" ? "sample.pdf" : "", extraction_mode: "", error_code: "", is_current: false, created_at: "2026-10-01T00:00:00Z", text: "中文 <img src=x> 简历" };
}
/**
 *  Input initial record, stream events, and optional page size, output real script VM and request; pagination is only for explicit interface stub.
 * When pageSize is null, retain old single-page fixture; positive number simulates server-side segmentation, does not alter production pagination parameters.
 *
 */
async function makePage(initial = [], events = [{ type: "result", text: "中文提取", pages: [] }], pageSize = null) {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  elements.get("csrf-token").content = "synthetic-csrf";
  elements.get("parse-mode").value = "traditional"; elements.get("upload-kind").value = "pdf";
  const items = initial; const requests = []; const errors = [];
  /**
 *  Input ID, only allows real template elements, fails explicitly if missing.
 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /**
 *  Input tag, output DOM node without window.
 */
  function createElement(tag) { return new Element(tag); }
  /**
 *  Input path/parameters, validate write operations include CSRF; simulate version CRUD, extraction, and explicit personal recommendations, does not run model.
 */
  async function fetch(url, options) {
    requests.push({ url, ...options });
    const method = options.method || "GET";
    if (method !== "GET") assert.equal(options.headers.get("X-CSRFToken"), "synthetic-csrf");
    assert.equal(options.credentials, "same-origin");
    if (url === "/api/profile/") {
      assert.equal(method, "PATCH");
      return json({ username: "synthetic", ...JSON.parse(options.body) });
    }
    const path = url.replace("/api/resume-versions/", "");
    if (method === "GET" && path.startsWith("?")) {
      const page = Number(new URLSearchParams(path).get("page"));
      const size = pageSize || items.length || 1;
      return json({ count: items.length, results: items.slice((page - 1) * size, page * size), next: page * size < items.length ? "?page=" + (page + 1) : null });
    }
    if (method === "POST" && path === "") {
      const item = record("new", options.body instanceof FormData ? "uploaded" : "ready");
      if (!(options.body instanceof FormData)) item.text = JSON.parse(options.body).text;
      items.unshift(item); return json(item, 201);
    }
    const item = items.find(byId);
    /**
 *  Input metadata, match exactly by first segment ID of request.
 */
    function byId(value) { return value.id === path.split("/")[0]; }
    assert.ok(item, path);
    if (method === "POST" && path.endsWith("recommendations/")) return json(item.recommendation_response, item.recommendation_status || 200);
    if (method === "GET" && path.endsWith("editor/")) {
      const original = items.find(byOriginal) || item;
      /**
 *  Input synthesized record, match only by edit draft source.
 */
      function byOriginal(value) { return value.id === item.source_version; }
      return json({ ...item, units: item.units || { other: item.text }, slots: item.slots || {}, slot_suggestions: item.slot_suggestions || null, slot_units: {skills:"skills",gpa:"education",commit_to_summer:"preferences",months_experience:"experience",num_publications:"publications"}, source_version: original.id, original_name: original.original_name, original_text: original.text });
    }
    if (method === "GET") return json(item);
    if (path.endsWith("editions/")) {
      const input = JSON.parse(options.body);
      const edition = { ...record("edition-" + items.length), ...input, source_version: item.source_version || item.id, edited_from: item.id };
      items.unshift(edition); return json(edition, 201);
    }
    if (path.endsWith("parse/")) {
      item.status = events.at(-1)?.type === "result" ? "ready" : "failed";
      if (item.status === "ready") item.text = events.at(-1).text;
      return stream(events);
    }
    if (path.endsWith("current/")) { for (const value of items) value.is_current = value.id === item.id; return json(item); }
    if (method === "DELETE") {
      if (item.protected) return json({ code: "resume_in_use" }, 409);
      items.splice(items.indexOf(item), 1); return new Response(null, { status: 204 });
    }
    assert.fail(path);
  }
  /**
 *  Input key/parameters, return assertable plain text; does not validate English resource quality.
 */
  function t(key, values = {}) { return key + JSON.stringify(values); }
  /**
 *  Approve simulated page delete dialog box; does not send request to real service.
 */
  function confirm() { return true; }
  /**
 *  Receive simulated logs/listeners registration without manipulating real windows.
 */
  function ignore(...args) { errors.push(args); }
  /**
 *  No language module present; return empty selector set; do not simulate HTML layout.
 */
  function selectors() { return []; }
  /**
 *  Input sidebar selector; return a single synthetic node to validate event registration.
 */
  function sectionNavigation(selector) { assert.equal(selector, ".section-navigation"); return new Element(); }
  /**
 *  No external parameters; read synthetic page language for validating bilingual rationale, no network side effects.
 */
  function language() { return context.document.documentElement.lang.startsWith("en") ? "en" : "zh"; }
  const pageLocation = { hash: "" };
  /**
 *  Input native history parameter; only record test hash, do not simulate page load or trigger events.
 */
  function replaceState(state, title, url) { assert.equal(state, null); assert.equal(title, ""); pageLocation.hash = url; }
  const context = vm.createContext({ document: { getElementById: getElement, createElement, querySelectorAll: selectors, querySelector: sectionNavigation, documentElement: { lang: "zh-CN" } }, window: { location: pageLocation, history: { replaceState }, AppI18n: { t, language }, confirm, addEventListener: ignore }, console: { error: ignore }, fetch, Headers, FormData, Response, ReadableStream, TextEncoder, TextDecoder, AbortController, DOMException });
  await vm.runInContext(SCRIPT, context);
  /**
 *  Input native function call expression; output its return value or Promise; use only test-fixed source code.
 */
  function run(code) { return vm.runInContext(code, context); }
  return { elements, requests, items, run, errors, context };
}
/**
 *  Text saved in real JSON format; afterward preview persistent details only; HTML characters preserved as textarea text, no extraction calls.
 */
async function textUpload() {
  const page = await makePage();
  page.elements.get("upload-kind").value = "text";
  page.elements.get("resume-text").value = "中文 <img src=x> 简历";
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.items[0].status, "ready");
  assert.equal(page.elements.get("preview-text").value, "中文 <img src=x> 简历");
  assert.equal(page.requests.filter(isParse).length, 0);
  /**
 *  Input request; determine whether explicit extraction call is required.
 */
  function isParse(request) { return request.url.endsWith("parse/"); }
  assert.equal(page.elements.get("upload-submit").disabled, false);
}
/**
 *  Empty file does not send request; valid PDF creates only uploaded version, no implicit parsing invocation.
 */
async function pdfUpload() {
  const page = await makePage();
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.requests.length, 1);
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"], { type: "application/pdf" })];
  await page.run("rmUpload({preventDefault(){}})");
  const request = page.requests[1];
  assert.ok(request.body instanceof FormData);
  assert.equal(await request.body.get("file").text(), "%PDF-synthetic");
  assert.equal(page.items[0].status, "uploaded");
  assert.equal(page.elements.get("preview-text").value, "");
}
/**
 *  Both PDF modes submit mode only when explicitly parsed; cross-byte Chinese results require complete final state.
 */
async function pdfModes() {
  for (const mode of ["traditional", "advanced"]) {
    const page = await makePage([record("pdf", "uploaded")]);
    assert.equal(page.elements.get("parse-mode").value, "traditional");
    page.elements.get("parse-mode").value = mode;
    await page.run("rmRun(function parse(){return rmParse('pdf')})");
    const request = page.requests.find(isParse);
    /**
 *  Input recorded request; locate unique parsing write operation.
 */
    function isParse(value) { return value.url.endsWith("parse/"); }
    assert.equal(JSON.parse(request.body).mode, mode);
    assert.equal(page.elements.get("preview-text").value, "中文提取");
    assert.match(page.elements.get("operation-status").textContent, /^rm_parsed/);
  }
}
/**
 *  Explicit server error does not adopt any text, does not automatically request second parse, and restores button.
 */
async function failedStream() {
  const page = await makePage([record("pdf", "uploaded")], [{ type: "progress", detail: "处理中" }, { type: "error", detail: "视觉校对失败" }]);
  await page.run("rmRun(function parse(){return rmParse('pdf')})");
  assert.equal(page.elements.get("preview-text").value, "");
  assert.equal(page.elements.get("operation-status").textContent, "视觉校对失败");
  assert.equal(page.elements.get("refresh-versions").disabled, false);
  assert.equal(page.requests.filter(isParse).length, 1);
  /**
 *  Input request; locate write extraction action, excluding subsequent GET refreshes.
 */
  function isParse(value) { return value.url.endsWith("parse/"); }
}
/**
 *  HTTP 200 with missing result stream cannot become success message.
 */
async function incompleteStream() {
  const page = await makePage([record("pdf", "uploaded")], [{ type: "progress", detail: "处理中" }]);
  await page.run("rmRun(function parse(){return rmParse('pdf')})");
  assert.match(page.elements.get("operation-status").textContent, /^rm_incomplete/);
  assert.equal(page.elements.get("preview-text").value, "");
}
/**
 *  Current selection sends real POST; 409 does not clear data, confirm successful deletion before clearing selected content.
 */
async function currentAndDelete() {
  const item = record("text"); const page = await makePage([item]);
  await page.run("rmAction({target:{closest(){return {dataset:{action:'current',id:'text'}}}}})");
  assert.equal(item.is_current, true);
  assert.equal(page.elements.get("preview-text").value, item.text);
  item.protected = true;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'delete',id:'text'}}}}})");
  assert.equal(page.items.length, 1);
  assert.match(page.elements.get("operation-status").textContent, /^rm_in_use/);
  item.protected = false;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'delete',id:'text'}}}}})");
  assert.equal(page.items.length, 0);
  assert.equal(page.elements.get("preview-text").value, "");
}
/**
 *  Name and email saved in profile center; do not modify username, do not auto-create or parse resume.
 */
async function profileSave() {
  const page = await makePage();
  page.elements.get("profile-name").value = " 姓名 ";
  page.elements.get("profile-email").value = " person@example.test ";
  await page.run("rmSaveProfile({preventDefault(){}})");
  assert.equal(page.requests[1].url, "/api/profile/");
  assert.equal(page.requests[1].method, "PATCH");
  assert.deepEqual(JSON.parse(page.requests[1].body), { first_name: "姓名", email: "person@example.test" });
  assert.equal(page.elements.get("profile-name").value, "姓名");
  assert.match(page.elements.get("operation-status").textContent, /^rm_profile_saved/);
  assert.equal(page.items.length, 0);
}
test("text upload saves and previews without implicit parsing", textUpload);
test("PDF upload validates size and saves without implicit extraction", pdfUpload);
test("PDF parsing preserves default and explicit advanced modes", pdfModes);
test("parse error never adopts text or retries", failedStream);
test("incomplete stream never announces success", incompleteStream);
test("current selection and protected deletion preserve version state", currentAndDelete);
test("profile information is maintained through the unified personal center", profileSave);

/**
 *  Save real client input; REST acts as proxy, validate unit, strict slot, source, original, and download path, do not validate service database write.
 */
async function editionSave() {
  const original = record("original");
  original.original_name = "original.pdf";
  const page = await makePage([original]);
  await page.run("rmPreview('original')");
  page.elements.get("unit-projects").value = "缓存项目\n改进 API <script>";
  page.elements.get("slot-skills").value = "Python, Django";
  page.elements.get("slot-months_experience").value = "0";
  page.elements.get("slot-num_publications").value = "0";
  page.elements.get("slot-commit_to_summer").value = "false";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  assert.equal(page.elements.get("edition-current").disabled, true);
  await page.run("rmSaveEdition({preventDefault(){}})");
  const saved = page.items[0];
  assert.equal(saved.units.projects, "缓存项目\n改进 API <script>");
  assert.deepEqual(saved.slots.skills, ["Python", "Django"]);
  assert.equal(saved.slots.months_experience, 0);
  assert.equal(saved.slots.commit_to_summer, false);
  assert.equal(saved.slots.academic_level, null);
  assert.equal(saved.source_version, "original");
  assert.equal(original.text, "中文 <img src=x> 简历");
  assert.equal(page.elements.get("preview-text").value, original.text);
  assert.equal(page.elements.get("unit-projects").value, saved.units.projects);
  assert.equal(page.elements.get("edition-download").href, "/api/resume-versions/" + saved.id + "/export/");
  assert.equal(page.run("rmAttachment.id"), original.id);
  assert.match(page.elements.get("uploaded-file-name").textContent, /^original.pdf/);
}
/**
 *  When user refuses to discard changes, do not switch current version or refresh; retain edit content and dirty state.
 */
async function dirtyCancel() {
  const page = await makePage([record("one"), record("two")]);
  await page.run("rmPreview('one')");
  page.elements.get("unit-projects").value = "unsaved";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  /**
 *  Reject synthetic dialog box; do not send requests to real window.
 */
  page.context.window.confirm = function reject() { return false; };
  const count = page.requests.length;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'current',id:'two'}}}}})");
  await page.run("rmRefresh()");
  assert.equal(page.requests.length, count);
  assert.equal(page.elements.get("unit-projects").value, "unsaved");
  assert.equal(page.run("rmEditorDirty"), true);
}
/**
 *  Enable adjacent parsing after attachment upload; disable after success; do not re-parse already completed attachments.
 */
async function nearbyParse() {
  const page = await makePage();
  assert.equal(page.elements.get("uploaded-parse").disabled, true);
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"])];
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.elements.get("uploaded-parse").disabled, false);
  await page.run("rmParseUploaded()");
  assert.equal(page.elements.get("uploaded-parse").disabled, true);
  const count = page.requests.length;
  await page.run("rmParseUploaded()");
  assert.equal(page.requests.length, count);
}
/**
 *  Simulate explicit change and cancel selection; real client must save once, explicitly parse, and display actions based on attachment and stream status.
 */
async function singleUploadEntry() {
  const page = await makePage();
  assert.equal(page.elements.get("upload-submit").hidden, true);
  assert.equal(page.elements.get("attachment-toolbar").hidden, true);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  const file = page.elements.get("resume-file");
  await page.run("rmChooseFile()");
  assert.equal(file.clicked, true);
  const count = page.requests.length;
  await file.listeners.change({ preventDefault });
  assert.equal(page.requests.length, count);
  file.files = [new Blob(["%PDF-synthetic"])];
  await file.listeners.change({ preventDefault });
  assert.equal(page.items.length, 1);
  assert.equal(page.items[0].status, "uploaded");
  assert.equal(page.elements.get("attachment-toolbar").hidden, false);
  assert.equal(page.elements.get("uploaded-file-name").hidden, false);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  assert.equal(page.elements.get("parse-mode-hint").hidden, true);
  page.elements.get("parse-mode").value = "advanced";
  await page.run("rmControls()");
  assert.equal(page.elements.get("parse-mode-hint").hidden, false);
  await page.run("rmBusy = true; rmControls()");
  await file.listeners.change({ preventDefault });
  assert.equal(page.items.length, 1);
  assert.equal(page.elements.get("choose-file").disabled, true);
  await page.run("rmBusy = false; rmController = new AbortController(); rmControls()");
  assert.equal(page.elements.get("cancel-parse").hidden, false);
  await page.run("rmController = null; rmControls(); rmParseUploaded()");
  assert.equal(page.elements.get("attachment-toolbar").hidden, true);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  page.elements.get("upload-kind").value = "text";
  await page.run("rmSource()");
  assert.equal(page.elements.get("upload-submit").hidden, false);
  assert.equal(page.elements.get("pdf-input").hidden, true);
}
/**
 *  Simulate original save failure; after validating selected file, request only once, retain current draft and dirty state, do not send request to model.
 */
async function fileUploadFailure() {
  const page = await makePage([record("one")]);
  await page.run("rmPreview('one')");
  page.elements.get("unit-projects").value = "keep my draft";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  const originalFetch = page.context.fetch;
  let attempts = 0;
  /**
 *  Only reject new original POST; retain existing authorization read; do not request actual local service or production database.
 */
  async function rejectUpload(url, options) {
    if (url === "/api/resume-versions/" && options.method === "POST") { attempts += 1; return json({ code: "invalid" }, 400); }
    return originalFetch(url, options);
  }
  page.context.fetch = rejectUpload;
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"])];
  await page.elements.get("resume-file").listeners.change({ preventDefault });
  assert.equal(attempts, 1);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.elements.get("unit-projects").value, "keep my draft");
  assert.equal(page.items.length, 1);
  assert.equal(page.elements.get("choose-file").disabled, false);
  assert.match(page.elements.get("operation-status").textContent, /^rm_http_error/);
}
/**
 *  Use invalid number from proxy DOM; real client must reject before sending request, retain current input.
 */
async function invalidSlots() {
  const page = await makePage([record("one")]);
  await page.run("rmPreview('one')");
  page.elements.get("slot-num_publications").value = "1.5";
  const count = page.requests.length;
  await page.run("rmSaveEdition({preventDefault(){}})");
  assert.equal(page.requests.length, count);
  assert.match(page.elements.get("operation-status").textContent, /^re_number_error/);
}
test("edition save retains originals and typed recommendation fields", editionSave);
test("cancelled navigation preserves unsaved edits", dirtyCancel);
test("uploaded attachment has a one-shot nearby extract action", nearbyParse);
test("one upload entry saves selected files and exposes only available actions", singleUploadEntry);
test("failed file selection upload preserves the unsaved online draft", fileUploadFailure);
test("invalid recommendation count is rejected before saving", invalidSlots);

/**
 *  REST proxy rejects new draft; real client still retains input/dirty and maintains untouched explicit empty array semantics.
 */
async function failedSave() {
  const item = record("one"); item.slots = { interests: [] };
  const page = await makePage([item]);
  await page.run("rmPreview('one')");
  assert.equal(page.run("rmReadSlots().interests.length"), 0);
  page.elements.get("unit-projects").value = "keep draft";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  const fetchOriginal = page.context.fetch;
  /**
 *  Reject editions POST; other requests pass through existing synthetic REST, no access to external services.
 */
  async function rejectEdition(url, options) {
    if (url.endsWith("editions/")) return json({ code: "invalid" }, 400);
    return fetchOriginal(url, options);
  }
  page.context.fetch = rejectEdition;
  await page.run("rmSaveEdition({preventDefault(){}})");
  assert.equal(page.elements.get("unit-projects").value, "keep draft");
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.items.length, 1);
  assert.match(page.elements.get("operation-status").textContent, /^rm_http_error/);
}
test("failed save retains unsaved text and explicit empty keywords", failedSave);

/**
 *  REST proxy provides suggestions; real backfill/save code must distinguish confirmed from pending confirmation, maintain 0/false and allow saving after clearing.
 */
async function suggestedSlots() {
  const original = record("suggested");
  original.slots = {gpa: 0};
  original.slot_suggestions = { values: {skills:["Python"],gpa:3.8,months_experience:0,num_publications:0,commit_to_summer:false}, evidence: {skills:[{text:"Python"}]} };
  const page = await makePage([original]);
  await page.run("rmPreview('suggested')");
  assert.equal(page.elements.get("slot-skills").value, "Python");
  assert.equal(page.elements.get("slot-gpa").value, "0");
  assert.equal(page.elements.get("slot-months_experience").value, "0");
  assert.equal(page.elements.get("slot-commit_to_summer").value, "false");
  assert.equal(page.elements.get("slot-num_publications").value, "0");
  assert.match(page.elements.get("slot-skills").attributes.title, /^re_suggestion_source/);
  assert.equal(page.run("rmSuggestedCount"), 4);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.elements.get("edition-current").disabled, true);
  page.elements.get("slot-skills").value = "";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')}); rmSaveEdition({preventDefault(){}})");
  assert.equal(page.items[0].slots.skills, null);
  assert.equal(page.items[0].slots.commit_to_summer, false);
  assert.equal(page.elements.get("slot-skills").value, "");
  assert.equal(page.run("rmSuggestedCount"), 0);
  assert.equal(page.run("rmEditorDirty"), false);
  assert.equal(original.slot_suggestions.values.skills[0], "Python");
}
test("extracted recommendation suggestions require saving and preserve manual confirmation", suggestedSlots);

/**
 *  Synthetic REST provides job; real client only requests save ID upon click; raw text not executed, editing clears result and disables recommendation.
 */
async function savedRecommendations() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  item.recommendation_response = {source_name: "Synthetic jobs", source_kind: "experience", results: [
    {job_id: "test", status: "scored", rank: 1, recommendation_reason: {zh:"已保存 Python 技能。", en:"Python is recorded."}, available_feature_count: 1, matched_skills: ["Python"],
      job: {title: "<img src=x> Backend", company: "Synthetic", location: "Test city", description: "Test only",
        requirements: {required_skills: ["Python"], job_in_person_commitment: "Online"}}},
  ]};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')");
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.equal(page.requests.length, 3);
  await page.run("rmRecommend()");
  const request = page.requests.at(-1);
  assert.equal(request.url, "/api/resume-versions/saved/recommendations/");
  assert.equal(request.method, "POST");
  assert.equal(request.body, undefined);
  const cards = page.elements.get("recommendation-results").children;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].children[0].children[1].textContent, "<img src=x> Backend");
  assert.match(page.elements.get("recommendation-state").textContent, /rj_experience/);
  page.elements.get("slot-skills").value = "Java";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')}); rmRecommend()");
  assert.equal(page.requests.at(-1), request);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
  await page.run("rmPreview('saved')");
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
}
test("recommendations use only saved features and clear results on edits", savedRecommendations);

/**
 *  Source error clearly displayed with no fake jobs or automatic retry; single operation lock released allows user to explicitly request again.
 */
async function recommendationFailure() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  item.recommendation_status = 502; item.recommendation_response = {code: "recommendation_llm_invalid_output"};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')");
  await page.run("rmRecommend()");
  assert.equal(page.requests.length, 4);
  assert.equal(page.elements.get("recommendation-state").textContent, "rj_llm_output{}");
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.match(page.elements.get("operation-status").textContent, /^rj_llm_output/);
}
test("recommendation LLM failure stays explicit without fabricated jobs", recommendationFailure);

/**
 *  Simulate refined ranking returning five jobs; HTML in reason and title treated as plain text only; switching language does not request API or re-rank.
 */
async function recommendationReasons() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  const results = [];
  for (let rank = 1; rank <= 5; rank += 1) results.push({status: "scored", rank, available_feature_count: 1, matched_skills: [], recommendation_reason: {zh:"<script>理由 " + rank, en:"<img src=x> Reason " + rank}, job: {title: "Job " + rank, requirements: {required_skills: ["Python"]}}});
  item.recommendation_response = {source_name: "Test", source_kind: "experience", results};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')"); await page.run("rmRecommend()");
  const list = page.elements.get("recommendation-results");
  assert.equal(list.children.length, 5);
  assert.equal(list.hidden, false);
  assert.equal(list.children[0].children[1].children[1].textContent, "<script>理由 1");
  page.context.document.documentElement.lang = "en";
  await page.run("rmLanguage()");
  assert.equal(list.children[4].children[1].children[1].textContent, "<img src=x> Reason 5");
  assert.equal(list.children[4].children[0].children[1].textContent, "Job 5");
  assert.equal(page.requests.length, 4);
  assert.match(HTML, /role="region"[^>]*tabindex="0"/);
}
test("LLM reasons stay safe and switch language without another API call", recommendationReasons);

/**
 *  Synthetic API splits into three pages; actual client selector lists all versions; explicit selection shows read-only details, retains historical pagination and current.
 */
async function recommendationSelection() {
  const ready = record("older"); ready.slots = { skills: ["Python"] };
  const page = await makePage([record("pdf", "uploaded"), record("recent"), ready], [], 1);
  const picker = page.elements.get("recommendation-resume");
  assert.equal(picker.children.length, 4);
  assert.match(picker.children[1].textContent, /rm_status_uploaded/);
  assert.match(picker.children[3].textContent, /<script>label<\/script>/);
  assert.equal(page.elements.get("versions-list").children.length, 1);
  picker.value = "older";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.run("rmEditorBase"), "older");
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.equal(page.requests.at(-1).url, "/api/resume-versions/older/editor/");
  assert.equal(page.elements.get("job-recommendations").scrolled.block, "start");
  assert.equal(picker.focused.preventScroll, true);
  for (const request of page.requests) assert.equal(request.method || "GET", "GET");
  await page.run("rmNext()");
  assert.equal(page.run("rmPage"), 2);
  assert.equal(picker.value, "older");
  assert.equal(picker.children.length, 4);
}
test("recommendation picker includes older pages and selects without automatic writes", recommendationSelection);

/**
 *  Real state branch displays upload/parsing/verification actions; parsing remains explicit; empty slots cannot request recommendation; verification only navigates, does not write.
 */
async function recommendationGuidance() {
  const empty = await makePage();
  assert.equal(empty.elements.get("recommendation-resume").disabled, true);
  assert.equal(empty.elements.get("recommendation-upload").hidden, false);
  assert.match(empty.elements.get("recommendation-state").textContent, /^rj_upload_first/);
  const page = await makePage([record("pdf", "uploaded")]);
  page.elements.get("recommendation-resume").value = "pdf";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.elements.get("recommendation-parse").hidden, false);
  assert.match(page.elements.get("recommendation-state").textContent, /^rj_parse_first/);
  const parseEvent = { currentTarget: { id: "recommendation-parse" } };
  const parsing = page.elements.get("recommendation-parse").listeners.click(parseEvent);
  parseEvent.currentTarget = null; // Simulate native event clearing currentTarget after asynchronous processing returns.
  await parsing;
  assert.equal(page.elements.get("job-recommendations").scrolled.block, "start");
  assert.equal(page.run("rmEditorBase"), "pdf");
  assert.equal(page.elements.get("recommendation-parse").hidden, true);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  assert.match(page.elements.get("recommendation-state").textContent, /^rj_empty_profile/);
  const before = page.requests.length;
  await page.run("rmReviewRecommendation(); rmRecommend()");
  assert.equal(page.elements.get("section-skills").open, true);
  assert.equal(page.elements.get("slot-skills").open, true);
  assert.equal(page.elements.get("slot-skills").focused.preventScroll, true);
  assert.equal(page.requests.length, before);
}
test("recommendation empty and unparsed states expose actionable next steps", recommendationGuidance);

/**
 *  Cross-version switching cancellation does not read/write server; selector restores loaded version; unsaved values and nearby save prompt remain.
 */
async function recommendationUnsaved() {
  const original = record("saved"); original.slots = { skills: ["Python"] };
  const page = await makePage([original, record("other")]);
  await page.run("rmPreview('saved')");
  page.elements.get("slot-skills").value = "Java";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')})");
  assert.equal(page.elements.get("recommendation-save").hidden, false);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  /**
 *  Simulate cancel discard; effect applies only to this isolated window.
 */
  function rejectSwitch() { return false; }
  page.context.window.confirm = rejectSwitch;
  const before = page.requests.length;
  page.elements.get("recommendation-resume").value = "other";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.elements.get("recommendation-resume").value, "saved");
  assert.equal(page.elements.get("slot-skills").value, "Java");
  assert.equal(page.requests.length, before);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.match(HTML, /id="recommendation-save"[^>]*form="edition-form"/);
}
test("recommendation switching protects pending details and offers nearby saving", recommendationUnsaved);

/**
 *  Simulate only REST and DOM: tab panels must not discard unsaved content or trigger save; actual scroll dimensions must be verified by the browser.
 */
async function workspaceDraft() {
  const item = record("resume"); item.is_current = true; item.slots = { skills: ["Python"] };
  const page = await makePage([item]);
  await page.run("rmPreview('resume')");
  page.elements.get("unit-skills").value = "Unsaved Python experience";
  await page.run("rmDirty({currentTarget: document.getElementById('unit-skills')}); rmReveal('job-recommendations', true)");
  const count = page.requests.length;
  assert.equal(page.elements.get("resume-editor").hidden, true);
  assert.equal(page.elements.get("job-recommendations").hidden, false);
  await page.run("rmReviewRecommendation()");
  assert.equal(page.elements.get("resume-editor").hidden, false);
  assert.equal(page.elements.get("job-recommendations").hidden, true);
  assert.equal(page.elements.get("unit-skills").value, "Unsaved Python experience");
  assert.equal(await page.run("rmEditorDirty"), true);
  assert.equal(page.context.window.location.hash, "#section-skills");
  assert.ok(page.elements.get("section-skills").scrolled);
  assert.equal(page.requests.length, count);
  page.context.window.location.hash = "#profile-panel";
  await page.run("rmWorkspaceHash()");
  assert.equal(page.elements.get("profile-panel").hidden, false);
  page.context.window.location.hash = "#section-preferences";
  await page.run("rmWorkspaceHash()");
  assert.equal(page.elements.get("resume-editor").hidden, false);
  assert.equal(page.elements.get("section-preferences").open, true);
  page.context.window.location.hash = "#version-history";
  await page.run("rmWorkspaceHash()");
  assert.equal(page.elements.get("resume-files").hidden, false);
  assert.equal(page.elements.get("version-history").hidden, false);
  assert.equal(page.requests.length, count);
}
test("workspace navigation preserves unsaved draft and reveals recommendation fields", workspaceDraft);
