/**
 *
 * @module interview-history-test
 * Responsibilities: DOM/HTTP boundary testing for the real read-only replay module; all content is synthetic example, no real model or user data.
 * Implementation: real templates provide ID sets, isolated VM runs real history/progress source code, explicitly delivers pagination and details.
 * Related Modules: interview-history.js, interview-review.html; does not prove real database or browser layout, permissions covered by Django tests.
 * Declaration Index:
 * - HistoryElement: minimal DOM surrogate.
 * - HistoryElement.constructor: initialize text, nodes, events, and attributes.
 * - HistoryElement.append: save child nodes.
 * - HistoryElement.replaceChildren: replace entire child node collection.
 * - HistoryElement.setAttribute: save attribute.
 * - HistoryElement.addEventListener: save listener.
 * - HistoryElement.focus: record focus.
 * - HistoryElement.showModal: set open flag.
 * - HistoryElement.close: simulate native close notification.
 * - HistoryElement.fire: explicitly dispatch event.
 * - historyTestPage: create isolated context for real script.
 * - historyTestPage.node: read actual template ID only.
 * - historyTestPage.create: create test node.
 * - historyTestPage.fetch: register undelivered requests.
 * - historyTestPage.silent: receive diagnostic logs, do not output body.
 * - historyTestPage.selectors: return empty language control collection.
 * - settleHistory: wait for synthetic promise chain completion, do not create real timers.
 * - deliverHistory: explicitly deliver HTTP success or failure response.
 * - deliverHistory.object1.json: return synthetic payload.
 * - historyText: recursively extract text for assertions, do not execute HTML.
 * - historyPaging: pagination, close, and isolated late detail testing.
 * - historyContent: pure text presentation testing for incomplete answers, difficulty, and model strings.
 * - historyFailure: visible HTTP errors, no silent retry testing.
 * Variable Index:
 * - HISTORY_SOURCE: real replay source code.
 * - PRESENTATION_SOURCE: shared pure presentation source code.
 * - HISTORY_HTML: real independent replay template.
 * Constraints:
 * No network, database, or vendor calls; only verify frontend interfaces and lifecycle, do not alter production defaults.
 *
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const HISTORY_SOURCE = readFileSync(new URL("../frontend/interview-history.js", import.meta.url), "utf8");
const PRESENTATION_SOURCE = readFileSync(new URL("../frontend/interview-progress.js", import.meta.url), "utf8");
const HISTORY_HTML = readFileSync(new URL("../frontend/interview-review.html", import.meta.url), "utf8");
/**
 *  Minimal DOM boundary; collect only text, do not simulate layout or native focus traps.
 */
class HistoryElement {
  /**
 *  No external parameters; set empty node, text, attributes, and closed state.
 */
  constructor() { this.textContent = ""; this.children = []; this.listeners = {}; this.dataset = {}; this.attributes = {}; this.open = false; this.id = ""; }
  /**
 *  Input: node to append; append without parsing string HTML.
 */
  append(...nodes) { this.children.push(...nodes); }
  /**
 *  Input: optional replacement node; replace entire child node collection.
 */
  replaceChildren(...nodes) { this.children = nodes; }
  /**
 *  Input: attribute name/value; save only display metadata.
 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /**
 *  Input: event name/handler; register and wait for explicit fire.
 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /**
 *  No external parameters; record focus, do not manipulate window.
 */
  focus() { this.focused = true; }
  /**
 *  No external parameters; simulate open state only.
 */
  showModal() { this.open = true; }
  /**
 *  No external parameters; clear open state and emit close, no browser window involved.
 */
  close() { this.open = false; this.fire("close"); }
  /**
 *  Input: event name; pass this node to registered handler.
 */
  fire(name) { return this.listeners[name]({ currentTarget: this }); }
}
/**
 *  No external parameters; output real script's isolated page and pending delivery requests, do not access external network.
 */
function historyTestPage() {
  const elements = new Map(); const requests = [];
  for (const match of HISTORY_HTML.matchAll(/id="([^"]+)"/g)) { const element = new HistoryElement(); element.id = match[1]; elements.set(match[1], element); }
  /**
 *  Input: real template ID; return node; reference to non-existent element fails immediately.
 */
  function node(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /**
 *  Input: tag name but do not simulate browser parsing; output minimal node.
 */
  function create() { return new HistoryElement(); }
  /**
 *  Input: URL and options; register the promise completion interface; do not automatically generate body.
 */
  function fetch(url, options) { const item = { url, options }; requests.push(item); const result = Promise.withResolvers(); item.resolve = result.resolve; return result.promise; }
  /**
 *  Accept any log or page event parameters; do not output candidate content.
 */
  function silent() {}
  /**
 *  No external parameters; language switching is verified via explicit separate calls, with no real controls.
 */
  function selectors() { return []; }
  const context = vm.createContext({ document: { getElementById: node, createElement: create, querySelectorAll: selectors }, window: { addEventListener: silent }, fetch, console: { error: silent }, Date, encodeURIComponent });
  const script = PRESENTATION_SOURCE.replaceAll("export function", "function").replace("export class", "class") + HISTORY_SOURCE.replace('import { reviewText, reviewTopics } from "./interview-progress.js";', "");
  vm.runInContext(script, context); return { node, requests, context };
}
/**
 *  No external parameters; advance resolved promise chains to allow real module rendering to complete.
 */
async function settleHistory() { for (let i = 0; i < 10; i++) await Promise.resolve(); }
/**
 *  No external parameters; accept request to deliver, synthesized JSON, and optional HTTP status; output nothing, without simulating real authentication.
 */
function deliverHistory(request, data, status = 200) { request.resolve({ ok: status === 200, status,
  /**
 *  No external parameters; return synthesized body solely for internal testing use.
 */
  async json() { return data; },
}); }
/**
 *  Accept a stub node; output all descendant text; do not invoke HTML parser.
 */
function historyText(node) { let text = node.textContent; for (const child of node.children) text += historyText(child); return text; }
/**
 *  Synthesize paginated and delayed details: close popups rejecting late content, paginate only current page, do not read others' context.
 */
async function historyPaging() {
  const page = historyTestPage();
  assert.equal(page.requests[0].options.cache, "no-store");
  deliverHistory(page.requests[0], { count: 2, next: "next", results: [{ id: "own-id", job_title: "Engineer", status: "completed", created_at: "2026-10-03T08:00:00Z" }] }); await settleHistory();
  const button = page.node("history-list").children[0].children[1]; button.fire("click");
  assert.equal(page.requests[1].url, "/api/agent-interviews/own-id/");
  page.node("history-close").fire("click");
  deliverHistory(page.requests[1], { job_title: "Late private content" }); await settleHistory();
  assert.equal(page.node("history-dialog").open, false); assert.equal(page.node("history-detail-body").children.length, 0); assert.equal(button.focused, true);
  page.node("history-next").fire("click"); assert.equal(page.requests[2].url, "/api/agent-interviews/?page=2");
  deliverHistory(page.requests[2], { count: 2, next: null, results: [] }); await settleHistory(); assert.equal(page.node("history-next").disabled, true);
}
test("history paging and closed details reject late responses", historyPaging);
/**
 *  Synthesize checked details: model HTML string contains only text, unassessed responses and failure flags do not impersonate complete reports.
 */
async function historyContent() {
  const page = historyTestPage(); deliverHistory(page.requests[0], { count: 1, next: null, results: [{ id: "mine", job_title: "Engineer", status: "failed", created_at: "2026-10-03" }] }); await settleHistory();
  page.node("history-list").children[0].children[1].fire("click");
  deliverHistory(page.requests[1], { job_title: "Engineer", status: "failed", created_at: "2026-10-03", security_output_available: true, decision_logs: [{ fallback_used: true, timeout_retrieval_sources: ["project"] }], request_issues: [{ error_code: "security_denied", status: "failed" }], questions: [{ ordinal: 1, question: { text: "<img onerror=evil>", difficulty: 4, probe_depth: 2, topic: "Project", dialogue_action: "probe", question_type: "design" }, answer: { text: "My answer", evaluation: null } }] }); await settleHistory();
  const text = historyText(page.node("history-detail-body")); assert.match(text, /难度 4\/5/); assert.match(text, /<img onerror=evil>/); assert.match(text, /My answer/); assert.match(text, /未获得可公开的评价/); assert.match(text, /security_denied/); assert.match(text, /既有备用路径/); assert.match(text, /project/);
  assert.equal(page.requests.length, 2);
  vm.runInContext("historyLanguage()", page.context); assert.equal(page.requests.length, 2);
}
test("history preserves difficulty and incomplete answers as safe text", historyContent);
/**
 *  HTTP 403 with real frontend processor: display error, unlock explicit refresh, do not initiate implicit retry.
 */
async function historyFailure() {
  const page = historyTestPage(); deliverHistory(page.requests[0], {}, 403); await settleHistory();
  assert.match(page.node("history-status").textContent, /读取失败/); assert.equal(page.node("history-refresh").disabled, false); assert.equal(page.requests.length, 1);
}
test("history HTTP failures stay visible without retry", historyFailure);
