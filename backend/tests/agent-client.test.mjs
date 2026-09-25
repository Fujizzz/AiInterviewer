/**
 * @module agent-client-test
 * 功能：用确定性时钟、DOM 和 WebSocket 替身验证真实 agent.js，完全不访问网络。
 * 实现：从实际 HTML 建立元素集合，vm 执行客户端脚本；通过事件观察计时、缓存与终态。
 * 关联：frontend/agent.html、agent.js；node --test 运行，不能证明实际供应商性能。
 * 目录：
 * - Element：最小 DOM 事件与展示替身。
 * - Element.constructor：初始化可见状态与监听器。
 * - Element.addEventListener：保存命名事件处理器。
 * - Element.focus：记录焦点，无窗口副作用。
 * - Element.fire：向监听器派发当前目标与表单取消函数。
 * - Element.fire.object1.preventDefault：模拟阻止默认表单导航。
 * - Socket：记录发送消息且允许测试显式交付事件。
 * - Socket.constructor：初始化连接与实例记录。
 * - Socket.addEventListener：保存处理器。
 * - Socket.send：收集已编码命令，不请求模型。
 * - Socket.close：关闭连接，不隐式重连。
 * - Socket.emit：显式交付 JSON 消息或关闭事件。
 * - makePage：创建独立脚本上下文及可控时钟。
 * - makePage.getElement：只允许查询真实模板中的元素。
 * - makePage.now：返回确定性单调时钟。
 * - makePage.setTimer：登记计时回调，不产生真实 interval。
 * - makePage.clearTimer：移除显示计时器。
 * - makePage.uuid：生成测试唯一请求标识。
 * - makePage.ignoreEvent：接收 pagehide 注册但不操作窗口。
 * - makePage.tick：推进测试时间并执行已登记显示回调。
 * - hello：完成协议公告并返回已发送命令的 UUID。
 * - callback1：预解析期间岗位可编辑，计时只显示实际经过时间，完成后不重复请求。
 * - callback2：编辑简历失效预览，start 发送最新文本且不变更题目默认值。
 * - callback3：取消后旧事件不能覆盖新状态，显示计时器已清理。
 * - callback4：评分先可见，后续错误明确报告未完成且保留评分。
 * - callback5：正常题目与最终报告结束等待，恢复控件。
 * - callback6：超限输入在发请求前被拒绝，避免多余模型调用。
 * - callback7：报告模型失败回退时明确提示，不把确定性摘要标记为模型成功。
 * - callback8：错误请求 ID 的阶段事件被拒绝并停止计时。
 * 关键变量：
 * - SCRIPT：待验证的真实客户端源码。
 * - HTML：实际面试模板，用于核验客户端元素引用。
 * 关键状态说明：
 * Socket.OPEN 为连接就绪值，Socket.instances 供测试定位连接；每例创建独立页面。
 * makePage 的 timers/time 仅为测试时钟，未修改生产显示周期、模型超时或面试预算。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SCRIPT = readFileSync(new URL("../frontend/agent.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/agent.html", import.meta.url), "utf8");

/** 最小 DOM 替身，仅维护测试需要的文本、禁用状态和事件，不模拟真实浏览器布局。 */
class Element {
  /** 输入无；所有字段初始为空，hidden 为 true 以模拟尚未出现的结果区。 */
  constructor() { this.value = ""; this.textContent = ""; this.hidden = true; this.disabled = false; this.listeners = {}; }
  /** 输入事件名和处理器，保存引用，返回无。 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /** 记录客户端要求聚焦，不操作真实窗口。 */
  focus() { this.focused = true; }
  /** 输入事件名称，构造 currentTarget 并调用已注册处理器，缺失监听器立即失败。 */
  fire(name) {
    this.listeners[name]({ currentTarget: this,
      /** 仅作为表单事件接口，无导航副作用。 */
      preventDefault() {},
    });
  }
}

/** 可控 WebSocket 替身：无真实连接，测试必须显式发送 hello 与结果事件。 */
class Socket {
  static OPEN = 1;
  static instances = [];
  /** 输入 URL，仅记录连接地址；不自动触发协议事件。 */
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; this.listeners = {}; Socket.instances.push(this); }
  /** 输入事件名和回调，记录处理器供 emit 调用。 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /** 输入已编码 JSON，解析并保存以断言命令内容。 */
  send(text) { this.sent.push(JSON.parse(text)); }
  /** 将连接标记关闭，不隐式发其他事件。 */
  close() { this.readyState = 3; }
  /** 输入事件负载及可选事件名，默认 message；currentTarget 固定为此连接。 */
  emit(data, name = "message") { this.listeners[name]({ data: JSON.stringify(data), currentTarget: this }); }
}

/** 无外部输入；执行真实脚本，返回元素查询、计时推进和计时器集合，均无网络副作用。 */
function makePage() {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  const timers = new Map();
  let time = 0;
  let timerId = 0;
  let serial = 0;
  /** 输入模板 ID，返回对应元素；客户端引用不存在的元素即失败。 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /** 返回可控单调时钟，不读取真实时间。 */
  function now() { return time; }
  /** 输入计时回调，登记并返回 ID，不启动系统 interval。 */
  function setTimer(fn) { timers.set(++timerId, fn); return timerId; }
  /** 输入 ID，删除对应计时回调。 */
  function clearTimer(id) { timers.delete(id); }
  /** 返回页面内唯一测试请求标识；不模拟后端 UUID 校验。 */
  function uuid() { return `request-${++serial}`; }
  /** 输入页面事件注册参数；测试不创建真实页面生命周期。 */
  function ignoreEvent() {}
  /** 输入毫秒推进测试时钟，再执行当前显示回调。 */
  function tick(ms) { time += ms; for (const fn of timers.values()) fn(); }
  getElement("resume").value = "A synthetic resume.";
  getElement("job").value = "General AI / Software Engineer";
  getElement("limit").value = "5";
  getElement("duration").value = "30";
  getElement("probes").value = "2";
  vm.runInNewContext(SCRIPT, {
    document: { getElementById: getElement }, window: { addEventListener: ignoreEvent },
    performance: { now }, crypto: { randomUUID: uuid },
    location: { protocol: "http:", host: "localhost" }, WebSocket: Socket, TextEncoder,
    setInterval: setTimer, clearInterval: clearTimer,
  });
  return { el: getElement, tick, timers };
}

/** 输入连接及可选测试消息上限，模拟 hello，返回被客户端发送的命令 ID 或 undefined。 */
function hello(ws, limit = 262144) {
  ws.emit({ type: "hello", capabilities: ["prepare", "progress", "assessment"], max_message_bytes: limit });
  return ws.sent.at(-1)?.request_id;
}

/** 固定时钟下预解析完成前可设置岗位；相同文本重复点击不再次调用模型。 */
test("prepare exposes real timing and permits job setup without duplicate requests", () => {
  const page = makePage();
  page.el("prepare-resume").fire("click");
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  assert.equal(ws.sent[0].type, "prepare");
  assert.equal(page.el("job").disabled, false);
  ws.emit({ type: "progress", request_id: id, stage: "resume_parsing", state: "running" });
  page.tick(32000);
  assert.match(page.el("wait-time").textContent, /32 秒/);
  ws.emit({ type: "prepared", request_id: id, candidate_profile: { skills: ["Python"] } });
  assert.equal(page.timers.size, 0);
  assert.equal(page.el("profile-panel").hidden, false);
  page.el("prepare-resume").fire("click");
  assert.equal(ws.sent.length, 1);
});

/** 已准备后编辑文本，预览失效且 start 使用新文本，原题数和追问上限不变。 */
test("edited resume invalidates preview and start sends current content", () => {
  const page = makePage();
  page.el("prepare-resume").fire("click");
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "prepared", request_id: id, candidate_profile: {} });
  page.el("resume").value = "Changed synthetic resume.";
  page.el("resume").fire("input");
  assert.equal(page.el("profile-panel").hidden, true);
  page.el("start-form").fire("submit");
  assert.equal(ws.sent.at(-1).resume_text, "Changed synthetic resume.");
  assert.equal(ws.sent.at(-1).max_questions, 5);
  assert.equal(ws.sent.at(-1).duration_minutes, 30);
  assert.equal(page.el("duration").disabled, true);
  assert.equal(ws.sent.at(-1).max_follow_up_per_topic, 2);
});

/** 取消后迟到进度、结果和关闭事件不能覆盖终态或重新启动 interval。 */
test("cancel clears timers and ignores late events from old socket", () => {
  const page = makePage();
  page.el("prepare-resume").fire("click");
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  page.el("cancel-agent").fire("click");
  ws.emit({ type: "prepared", request_id: id, candidate_profile: {} });
  ws.emit({}, "close");
  assert.match(page.el("agent-status").textContent, /已取消/);
  assert.equal(page.el("profile-panel").hidden, true);
  assert.equal(page.timers.size, 0);
});

/** 先行评分尚无文字报告时断线，保留分数并明确未完成，停止计时。 */
test("early assessment stays visible if report connection fails", () => {
  const page = makePage();
  page.el("start-form").fire("submit");
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "assessment", request_id: id, assessment: { overall_score: 3, competencies: {} } });
  assert.equal(page.el("report-panel").hidden, false);
  assert.match(page.el("score").textContent, /3.00/);
  ws.emit({}, "close");
  assert.match(page.el("report-summary").textContent, /未完成/);
  assert.equal(page.timers.size, 0);
});

/** 首题允许回答并停止计时，最后一轮报告成功后保留真实总结并恢复开始按钮。 */
test("question and finished response release pending UI state", () => {
  const page = makePage();
  page.el("start-form").fire("submit");
  const ws = Socket.instances.at(-1);
  let id = hello(ws);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "q1", text: "What did you implement?", target_competency: "ownership", difficulty: 2 } });
  assert.equal(page.el("answer").disabled, false);
  assert.equal(page.timers.size, 0);
  page.el("answer").value = "Synthetic answer.";
  page.el("answer-form").fire("submit");
  id = ws.sent.at(-1).request_id;
  ws.emit({ type: "finished", request_id: id, result: { final_report: {
    overall_score: 3, competencies: {}, summary: "Fixture final report.",
  } } });
  assert.equal(page.el("report-summary").textContent, "Fixture final report.");
  assert.equal(page.el("start-agent").disabled, false);
  assert.equal(page.timers.size, 0);
});

/** 模拟服务端公告低上限，仅验证客户端边界；不得发送超限内容或遗留计时器。 */
test("oversized command is rejected before send", () => {
  const page = makePage();
  page.el("prepare-resume").fire("click");
  const ws = Socket.instances.at(-1);
  hello(ws, 5);
  assert.equal(ws.sent.length, 0);
  assert.match(page.el("agent-status").textContent, /大小限制/);
  assert.equal(page.timers.size, 0);
});

/** 原有报告回退被服务端标记时，前端必须明确告知来源，不改变有效分数。 */
test("report fallback is explicitly labeled", () => {
  const page = makePage();
  page.el("start-form").fire("submit");
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "finished", request_id: id, result: {
    report_narrative_status: "fallback",
    final_report: { overall_score: 3, competencies: {}, summary: "Deterministic summary." },
  } });
  assert.match(page.el("report-summary").textContent, /模型报告文字生成失败/);
  assert.match(page.el("score").textContent, /3.00/);
});

/** 同连接内错误 request_id 不得误改当前阶段，应明确失败并清理计时器。 */
test("foreign request progress is rejected", () => {
  const page = makePage();
  page.el("prepare-resume").fire("click");
  const ws = Socket.instances.at(-1);
  hello(ws);
  ws.emit({ type: "progress", request_id: "foreign", stage: "resume_parsing", state: "running" });
  assert.match(page.el("agent-status").textContent, /请求不匹配/);
  assert.equal(page.timers.size, 0);
});
