/**
 * @module resume-pdf-client-test
 * 职责：执行真实 PDF 客户端，验证分片、断流、取消及人工采用边界。
 * 实现：最小 DOM 和 fetch 替身，真实 TextDecoder/ReadableStream 解析，不访问模型。
 * 关联：frontend/resume-pdf.js、agent.html；不能替代真实 PDF 或供应商测试。
 * 目录：
 * - makePage：构造脚本上下文和实际模板 ID 集合。
 * - makePage.getElement：返回模板元素，不容忍未知 ID。
 * - makePage.Element：DOM 数据与事件的替身。
 * - makePage.Element.constructor：初始化字段与监听器。
 * - makePage.Element.addEventListener：保存事件处理函数。
 * - makePage.Element.dispatchEvent：记录人工采用触发的 input 事件。
 * - makePage.Element.focus：无窗口副作用的焦点替身。
 * - makePage.Observer：禁用属性观察器替身。
 * - makePage.Observer.observe：记录目标，测试通过显式控件更新模拟变化。
 * - makePage.noop：替代窗口事件及计时操作。
 * - makePage.now：返回固定时钟。
 * - makePage.run：在隔离上下文执行客户端公开的本地函数。
 * - response：构造逐字节 UTF-8 NDJSON 响应。
 * - response.start：向读取流推入事件字节。
 * - callback1：校对成功后才能采用，人工采用才更新简历并派发 input。
 * - callback1.fetch：返回进度和校对完成事件流，不发网络请求。
 * - callback2：视觉失败不显示未校对结果，不覆盖手工简历。
 * - callback2.fetch：返回显式失败事件。
 * - callback3：缺成功终态的断流不得标记完成。
 * - callback3.fetch：返回缺少 result 的流。
 * - callback4：取消后迟到结果不能恢复旧内容或覆盖新状态。
 * - callback4.fetch：悬挂请求直到测试手动释放。
 * - callback4.fetch.callback1：保存模拟网络响应完成函数。
 * - callback5：面试锁定和上传超限均不会发起请求或替换简历。
 * - callback5.fetch：记录任何非预期请求。
 * - callback6：只展示最终文本和必要疑点，不显示修改建议。
 * - callback6.fetch：断言上传无模式字段并返回最终结果。
 * 关键变量：
 * - SCRIPT：真实客户端源码。
 * - HTML：真实模板，用于元素引用验证。
 * - PROGRESS：不含文本中间结果的进度事件。
 * - RESULT_TEXT：虚构的最终校对文本。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SCRIPT = readFileSync(new URL("../frontend/resume-pdf.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/agent.html", import.meta.url), "utf8");
const PROGRESS = { type: "progress", detail: "视觉校对：已完成 0/1 页" };
const RESULT_TEXT = "李 Test";

/** 输入 fetch 替身，输出 DOM 与代码调用器；不读取任何真实候选人文件。 */
function makePage(fetch) {
  /** 替身元素只提供真实脚本使用的字段与事件接口。 */
  class Element {
    /** 无参数，初始化空内容及可控文件列表。 */
    constructor() { this.value = ""; this.textContent = ""; this.disabled = false; this.files = []; this.listeners = {}; this.events = []; }
    /** 输入事件名和处理器，保存用于检查绑定。 */
    addEventListener(name, handler) { this.listeners[name] = handler; }
    /** 输入事件，记录而不触发外部代码，验证采用时发出了 input。 */
    dispatchEvent(event) { this.events.push(event.type); }
    /** 无外部窗口，不执行焦点变化。 */
    focus() {}
  }
  /** 观察器替身，不自行调度微任务；控件状态由测试显式刷新。 */
  class Observer {
    /** 输入目标，保存引用，无 DOM 副作用。 */
    observe(target) { this.target = target; }
  }
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  /** 输入实际模板 ID，返回元素；未知 ID 立即失败。 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /** 替代不影响断言的计时和窗口注册，不创建真实计时器。 */
  function noop() { return 1; }
  /** 返回固定单调时间，只验证计时调用不会影响业务。 */
  function now() { return 100; }
  const context = vm.createContext({ document: { getElementById: getElement }, fetch,
    MutationObserver: Observer, window: { addEventListener: noop }, performance: { now },
    setInterval: noop, clearInterval: noop, AbortController, FormData, TextDecoder, Event, console });
  vm.runInContext(SCRIPT, context);
  getElement("pdf-file").files = [new File(["%PDF-test"], "test.pdf", { type: "application/pdf" })];
  /** 输入测试表达式，在该页面上下文执行；不影响其他测试页面。 */
  function run(code) { return vm.runInContext(code, context); }
  return { el: getElement, run };
}

/** 输入 JSON 事件列表，逐字节分片以检验跨片中文解码与行拼接。 */
function response(events) {
  let text = "";
  for (const event of events) text += JSON.stringify(event) + "\n";
  const bytes = new TextEncoder().encode(text);
  /** 输入流控制器，推入单字节分片后关闭，没有额外网络请求。 */
  function start(controller) {
    for (const value of bytes) controller.enqueue(new Uint8Array([value]));
    controller.close();
  }
  return { ok: true, body: new ReadableStream({ start }) };
}

/** 校对结果不能在用户采用之前覆盖既有简历。 */
test("reviewed results require explicit adoption and invalidate prepared input", async () => {
  /** 返回进度及校对成功终态，不访问真实后端。 */
  async function fetch() { return response([PROGRESS, { type: "result", changed_pages: 0, text: RESULT_TEXT, pages: [{ number: 1, uncertainties: [] }] }]); }
  const page = makePage(fetch);
  page.el("resume").value = "manual text";
  await page.run("pdfRun()");
  assert.equal(page.el("resume").value, "manual text");
  assert.equal(page.el("pdf-preview").value, "李 Test");
  assert.match(page.el("pdf-status").textContent, /校对完成/);
  page.run("pdfUse()");
  assert.equal(page.el("resume").value, "李 Test");
  assert.deepEqual(page.el("resume").events, ["input"]);
});

/** 视觉失败不显示规则中间结果，也不允许采用。 */
test("vision error does not expose intermediate text or allow adoption", async () => {
  /** 返回明确 error 终态，模拟模型不可用。 */
  async function fetch() { return response([PROGRESS, { type: "error", detail: "模型失败" }]); }
  const page = makePage(fetch);
  page.el("resume").value = "manual";
  await page.run("pdfRun()");
  assert.match(page.el("pdf-status").textContent, /解析失败/);
  assert.equal(page.el("pdf-preview").value, "");
  assert.equal(page.el("resume").value, "manual");
  assert.equal(page.el("pdf-use").disabled, true);
  page.run("pdfUse()");
  assert.equal(page.el("resume").value, "manual");
});

/** 只收到进度事件但未收到成功 result 属于断流，不能误报成功。 */
test("truncated stream is reported as incomplete", async () => {
  /** 返回不含成功终态的有限字节流。 */
  async function fetch() { return response([PROGRESS]); }
  const page = makePage(fetch);
  await page.run("pdfRun()");
  assert.match(page.el("pdf-status").textContent, /提前结束/);
});

/** 请求身份约束应屏蔽取消后网络迟到的旧文本。 */
test("cancelled request cannot repopulate cleared results", async () => {
  let resolve;
  /** 模拟不响应 abort 的迟到网络，使客户端本地身份校验承担隔离。 */
  function fetch() {
    /** 保存测试控制的响应完成函数，不执行异步 I/O。 */
    return new Promise(/** 保存模拟网络完成函数，由测试显式交付响应。 */ (done) => { resolve = done; });
  }
  const page = makePage(fetch);
  const pending = page.run("pdfRun()");
  page.run("pdfClear()");
  resolve(response([PROGRESS, { type: "result", changed_pages: 0, text: RESULT_TEXT, pages: [{ number: 1, uncertainties: [] }] }]));
  await pending;
  assert.equal(page.el("pdf-preview").value, "");
  assert.equal(page.el("resume").value, "");
});

/** 前端状态检查应在事件处理函数中再次执行，不能仅依赖 disabled 外观。 */
test("interview lock and size limit block requests and adoption", async () => {
  let calls = 0;
  /** 任何调用都计数，断言这些入口不应访问网络。 */
  async function fetch() { calls += 1; return response([]); }
  const page = makePage(fetch);
  page.el("resume").disabled = true;
  page.el("pdf-preview").value = "replace";
  await page.run("pdfRun()");
  page.run("pdfUse()");
  assert.equal(page.el("resume").value, "");
  page.el("resume").disabled = false;
  page.el("pdf-file").files = [{ size: 10 * 1024 * 1024 + 1 }];
  await page.run("pdfRun()");
  assert.equal(calls, 0);
  assert.match(page.el("pdf-status").textContent, /10 MiB/);
});

/** 输入提取基线及单处修订，前端只呈现最终文字与疑点，不显示逐条修改建议。 */
test("single pipeline only displays final text and uncertainties", async () => {
  /** 检查实际上传字段，返回逐页完成信息与完整终态，不发送网络请求。 */
  async function fetch(url, options) {
    assert.equal(url, "/api/resume/parse/");
    assert.deepEqual([...options.body.keys()], ["file"]);
    return response([PROGRESS, { type: "page", number: 1, changed: true,
      text: "李 Text", uncertainties: [] },
      { type: "result", changed_pages: 1, text: "李 Text", pages: [{ number: 1, uncertainties: ["日期不清晰"] }] }]);
  }
  const page = makePage(fetch);
  await page.run("pdfRun()");
  assert.doesNotMatch(page.el("pdf-notes").textContent, /修改前|修改后|图中为 x/);
  assert.match(page.el("pdf-notes").textContent, /日期不清晰/);
  assert.match(page.el("pdf-status").textContent, /校对完成/);
  assert.equal(page.el("pdf-use").disabled, false);
  page.run("pdfUse()");
  assert.equal(page.el("resume").value, "李 Text");
});
