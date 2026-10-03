/**
 * @module interview-history-test
 * 职责：真实只读复盘模块的 DOM/HTTP 边界测试；所有内容为合成样例，无真实模型或用户数据。
 * 实现：真实模板提供 ID 集合，隔离 VM 运行真实 history/progress 源码，显式交付分页和详情。
 * 关联：interview-history.js、resumes.html；不证明真实数据库或浏览器布局，权限由 Django 测试覆盖。
 * 目录：
 * - HistoryElement：最小 DOM 替身。
 * - HistoryElement.constructor：初始化文本、节点、事件及属性。
 * - HistoryElement.append：保存子节点。
 * - HistoryElement.replaceChildren：替换子节点。
 * - HistoryElement.setAttribute：保存属性。
 * - HistoryElement.addEventListener：保存监听器。
 * - HistoryElement.focus：记录焦点。
 * - HistoryElement.showModal：设置打开标记。
 * - HistoryElement.close：模拟原生关闭通知。
 * - HistoryElement.fire：显式派发事件。
 * - historyTestPage：创建真实脚本隔离上下文。
 * - historyTestPage.node：只读取实际模板 ID。
 * - historyTestPage.create：创建测试节点。
 * - historyTestPage.fetch：登记尚未交付的请求。
 * - historyTestPage.silent：接收诊断日志，不输出正文。
 * - historyTestPage.selectors：返回空语言控件集合。
 * - settleHistory：等待合成 promise 链完成，不创建真实计时器。
 * - deliverHistory：明确交付 HTTP 成功或失败响应。
 * - deliverHistory.object1.json：返回合成正文。
 * - historyText：递归提取文本供断言，不执行 HTML。
 * - historyPaging：分页、关闭与迟到详情隔离测试。
 * - historyContent：不完整答案、难度与模型字符串纯文本呈现测试。
 * - historyFailure：HTTP 错误可见，无静默重试测试。
 * 关键变量：
 * - HISTORY_SOURCE：真实复盘源码。
 * - PRESENTATION_SOURCE：共享纯展示源码。
 * - HISTORY_HTML：真实个人中心模板。
 * 约束：
 * 无网络、数据库、供应商调用；只验证前端接口和生命周期，不改变生产默认值。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const HISTORY_SOURCE = readFileSync(new URL("../frontend/interview-history.js", import.meta.url), "utf8");
const PRESENTATION_SOURCE = readFileSync(new URL("../frontend/interview-progress.js", import.meta.url), "utf8");
const HISTORY_HTML = readFileSync(new URL("../frontend/resumes.html", import.meta.url), "utf8");
/** 最小 DOM 边界；只收集文本，不模拟布局或原生焦点陷阱。 */
class HistoryElement {
  /** 无外部参数；设置空节点、文本、属性与关闭状态。 */
  constructor() { this.textContent = ""; this.children = []; this.listeners = {}; this.dataset = {}; this.attributes = {}; this.open = false; this.id = ""; }
  /** 输入节点并追加，不解析字符串 HTML。 */
  append(...nodes) { this.children.push(...nodes); }
  /** 输入可选替代节点，替换整个子节点集合。 */
  replaceChildren(...nodes) { this.children = nodes; }
  /** 输入属性名/值，仅保存展示元数据。 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /** 输入事件名/处理器，登记后等待显式 fire。 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /** 无外部参数；记录焦点，不操作窗口。 */
  focus() { this.focused = true; }
  /** 无外部参数；仅模拟打开状态。 */
  showModal() { this.open = true; }
  /** 无外部参数；清除打开状态并发 close，无浏览器窗口。 */
  close() { this.open = false; this.fire("close"); }
  /** 输入事件名并将本节点交给已注册处理器。 */
  fire(name) { return this.listeners[name]({ currentTarget: this }); }
}
/** 无外部参数；输出真实脚本的隔离页面和待交付请求，不访问外部网络。 */
function historyTestPage() {
  const elements = new Map(); const requests = [];
  for (const match of HISTORY_HTML.matchAll(/id="([^"]+)"/g)) { const element = new HistoryElement(); element.id = match[1]; elements.set(match[1], element); }
  /** 输入真实模板 ID，返回节点；引用不存在的元素立即失败。 */
  function node(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /** 输入标签名但不模拟浏览器解析，输出最小节点。 */
  function create() { return new HistoryElement(); }
  /** 输入 URL 与选项，登记 promise 的完成接口；不自动产生正文。 */
  function fetch(url, options) { const item = { url, options }; requests.push(item); const result = Promise.withResolvers(); item.resolve = result.resolve; return result.promise; }
  /** 输入任意日志或页面事件参数；不输出候选人内容。 */
  function silent() {}
  /** 无外部参数；语言切换由单独显式调用验证，无真实控件。 */
  function selectors() { return []; }
  const context = vm.createContext({ document: { getElementById: node, createElement: create, querySelectorAll: selectors }, window: { addEventListener: silent }, fetch, console: { error: silent }, Date, encodeURIComponent });
  const script = PRESENTATION_SOURCE.replaceAll("export function", "function").replace("export class", "class") + HISTORY_SOURCE.replace('import { reviewText, reviewTopics } from "./interview-progress.js";', "");
  vm.runInContext(script, context); return { node, requests, context };
}
/** 无外部参数；推进已解决的 promise 链，使真实模块完成渲染。 */
async function settleHistory() { for (let i = 0; i < 10; i++) await Promise.resolve(); }
/** 输入待交付请求、合成 JSON 及可选 HTTP 状态；输出无，不模拟真实认证。 */
function deliverHistory(request, data, status = 200) { request.resolve({ ok: status === 200, status,
  /** 无外部参数，返回仅供本测试的合成正文。 */
  async json() { return data; },
}); }
/** 输入替身节点，输出所有后代文本；不调用 HTML 解析器。 */
function historyText(node) { let text = node.textContent; for (const child of node.children) text += historyText(child); return text; }
/** 合成分页与延迟详情：关闭弹窗拒绝迟到正文，分页仅请求当前页，不读取他人上下文。 */
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
/** 合成已检详情：模型 HTML 字符串仅文本，未评价回答和失败标志不冒充完整报告。 */
async function historyContent() {
  const page = historyTestPage(); deliverHistory(page.requests[0], { count: 1, next: null, results: [{ id: "mine", job_title: "Engineer", status: "failed", created_at: "2026-10-03" }] }); await settleHistory();
  page.node("history-list").children[0].children[1].fire("click");
  deliverHistory(page.requests[1], { job_title: "Engineer", status: "failed", created_at: "2026-10-03", security_output_available: true, decision_logs: [{ fallback_used: true, timeout_retrieval_sources: ["project"] }], request_issues: [{ error_code: "security_denied", status: "failed" }], questions: [{ ordinal: 1, question: { text: "<img onerror=evil>", difficulty: 4, probe_depth: 2, topic: "Project", dialogue_action: "probe", question_type: "design" }, answer: { text: "My answer", evaluation: null } }] }); await settleHistory();
  const text = historyText(page.node("history-detail-body")); assert.match(text, /难度 4\/5/); assert.match(text, /<img onerror=evil>/); assert.match(text, /My answer/); assert.match(text, /未获得可公开的评价/); assert.match(text, /security_denied/); assert.match(text, /既有备用路径/); assert.match(text, /project/);
  assert.equal(page.requests.length, 2);
  vm.runInContext("historyLanguage()", page.context); assert.equal(page.requests.length, 2);
}
test("history preserves difficulty and incomplete answers as safe text", historyContent);
/** HTTP 403 与真实前端处理器：显示错误、解锁显式刷新，不启动隐式重试。 */
async function historyFailure() {
  const page = historyTestPage(); deliverHistory(page.requests[0], {}, 403); await settleHistory();
  assert.match(page.node("history-status").textContent, /读取失败/); assert.equal(page.node("history-refresh").disabled, false); assert.equal(page.requests.length, 1);
}
test("history HTTP failures stay visible without retry", historyFailure);
