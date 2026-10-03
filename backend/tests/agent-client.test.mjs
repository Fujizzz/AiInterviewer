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
 * - Element.showModal：模拟原生 open 状态，不模拟焦点陷阱或背景 inert。
 * - Element.close：清除 open 并派发 close，以验证确认和取消的状态转换。
 * - startPrepared：通过开始入口打开弹窗，再显式提交准备表单。
 * - preparationInteraction：弹窗打开/关闭不建立连接；输入保留、确认后关闭且进行中禁改。
 * - preparationBoundaries：隐藏表单、无效岗位和来源失败不能开始面试。
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
 * - makePage.addPageListener：保存页面事件处理器，连接面试和语音控件。
 * - makePage.dispatchPageEvent：将面试控件状态交给真实语音协调器。
 * - PageEvent：仅提供 CustomEvent 的 type 与 detail 字段。
 * - PageEvent.constructor：创建测试页面事件，不接触浏览器。

 * - Element.append：追加选项节点。
 * - Element.replaceChildren：清空旧选项。
 * - makePage.createElement：创建测试选项。
 * - makePage.fetch.object1.json：返回合成分页 JSON。
 * - makePage.fetch：模拟本人分页版本接口，记录调用而不访问模型。
 * - makePage.ignoreError：接收诊断日志，不暴露用户正文。
 * - makePage.tick：推进测试时间并执行已登记显示回调。
 * - hello：完成协议公告并返回已发送命令的 UUID。
 * - callback1：start 发送版本 ID、保持原预算并展示真实计时。
 * - callback2：跨页加载 current，并拒绝不可用指定版本。
 * - callback3：取消后旧事件不能覆盖状态，版本保留且计时器清理。
 * - callback4：评分先可见，后续错误明确报告未完成且保留评分。
 * - callback5：正常题目与最终报告结束等待，恢复控件。
 * - callback6：超限输入在发请求前被拒绝，避免多余模型调用。
 * - callback7：报告模型失败回退时明确提示，不把确定性摘要标记为模型成功。
 * - callback8：错误请求 ID 的阶段事件被拒绝并停止计时。
 * - callback9：当前问题启用语音控件，播放期间禁止录音，结束后可语音提交。
 * - callback10：取消时释放录音，拒绝迟到最终文本并恢复面试入口。
 * - answeringPage：使用真实客户端处理器进入合成当前题。
 * - startCapture：只替换设备/供应商边界，启动真实语音协调器。
 * - startCapture.page.captureType.prototype.start：离线授权/握手使采集就绪。
 * - startCapture.page.captureType.prototype.end：离线结束采集，最终文本单独交付。
 * - speechAnswerLifecycle：验证字幕、显式结束、最终转写一次提交及下一题清理。
 * - speechAnswerBoundaries：时限需确认，空白/失败/迟到转写不提交。

 * - blockedResumeSelection：缺少 ready、未选 current 或未授权时不能创建面试连接。
 * 关键变量：
 * - SCRIPT：待验证的真实客户端源码。
 * - HTML：实际面试模板，用于核验客户端元素引用。
 * - VOICE_SCRIPT：真实语音协调器源码，在隔离 VM 中执行。
 * - CAPTURE_SCRIPT：真实录音管理器源码，不打开设备或供应商连接。
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
const VOICE_SCRIPT = readFileSync(new URL("../frontend/interview-voice.js", import.meta.url), "utf8");
const CAPTURE_SCRIPT = readFileSync(new URL("../frontend/speech-capture.js", import.meta.url), "utf8");

/** 提供真实客户端所需的 CustomEvent 数据字段；不模拟原生事件权限。 */
class PageEvent {
  /** 保存事件名和 detail，供页面状态协调器分派。 */
  constructor(type, options) { this.type = type; this.detail = options.detail; }
}

/** 最小 DOM 替身，仅维护测试需要的文本、禁用状态和事件，不模拟真实浏览器布局。 */
class Element {
  /** 输入无；所有字段初始为空，hidden 为 true 以模拟尚未出现的结果区。 */
  constructor() { this.value = ""; this.textContent = ""; this.hidden = true; this.disabled = false; this.listeners = {}; this.children = []; this.open = false; }
  /** 输入事件名和处理器，保存引用，返回无。 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /** 输入节点列表；保存版本选项，无布局或解析 HTML 副作用。 */
  append(...nodes) { this.children.push(...nodes); }
  /** 清除此前选项，保持 DOM 对象身份。 */
  replaceChildren() { this.children = []; }
  /** 记录客户端要求聚焦，不操作真实窗口。 */
  focus() { this.focused = true; }
  /** 无参数；只模拟 dialog.open，不证明真实浏览器焦点范围或原生表单校验。 */
  showModal() { this.open = true; }
  /** 无参数；模拟原生 close 事件，不执行模型或设备请求。 */
  close() { if (!this.open) return; this.open = false; this.fire("close"); }
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

/** 输入合成 versions/search/failure 选项，执行真实脚本并等待初次加载；输出测试页面和隔离采集类，便于替换设备边界，无真实网络。 */
async function makePage(options = {}) {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  const timers = new Map();
  let time = 0;
  let timerId = 0;
  let serial = 0;
  const pageListeners = new Map();
  /** 保存事件名与回调，让真实语音协调器接收客户端 eligibility。 */
  function addPageListener(name, handler) { pageListeners.set(name, handler); }
  /** 交付测试 CustomEvent；不发送网络或采集媒体。 */
  function dispatchPageEvent(event) { pageListeners.get(event.type)?.(event); }
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

  getElement("job").value = "General AI / Software Engineer";
  getElement("limit").value = "5";
  getElement("duration").value = "30";
  getElement("probes").value = "2";
  const requests = [];
  const versions = options.versions || [{id:"version-a",status:"ready",label:"Current resume",is_current:true}];
  /** 输入请求 URL，输出合成分页 JSON；失败用真实 HTTP 状态，不隐式降级。 */
  async function fetch(url) {
    requests.push(url);
    const page = Number(new URL(url, "http://localhost").searchParams.get("page"));
    return { ok: !options.failure, status: options.failure || 200,
      /** 返回对应合成页；每页 1 个记录使测试覆盖 current 在后续页。 */
      async json() { return {results: versions.slice(page-1,page),next: page < versions.length ? "next" : null}; },
    };
  }
  /** 输入标签名，输出选项节点，无真实窗口副作用。 */
  function createElement() { return new Element(); }
  /** 接收诊断日志，不打印合成版本或语音正文。 */
  function ignoreError() {}
  const clientScript = CAPTURE_SCRIPT.replace("export class SpeechCapture", "class SpeechCapture")
    + VOICE_SCRIPT.replace('import { SpeechCapture } from "./speech-capture.js";', "").replace("export class InterviewVoice", "class InterviewVoice")
    + SCRIPT.replace('import { InterviewVoice } from "./interview-voice.js";', "");
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

/** 输入连接及可选测试消息上限，模拟 hello，返回被客户端发送的命令 ID 或 undefined。 */
function hello(ws, limit = 262144) {
  ws.emit({ type: "hello", capabilities: ["prepare", "progress", "assessment"], max_message_bytes: limit });
  return ws.sent.at(-1)?.request_id;
}

/** 输入已加载页面，显式打开准备并确认；不替代业务处理器或创建额外连接。 */
function startPrepared(page) {
  page.el("open-preparation").fire("click");
  page.el("start-form").fire("submit");
}

/** 真实脚本配合原生 dialog 替身；开关保留设置，无网络，确认一次后关闭并禁用所有准备入口。 */
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
  page.el("preparation-dialog").close(); // 模拟原生 Esc 的 close，键盘陷阱另由浏览器验证。
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

/** 不在弹窗内的提交不能连接；无效输入留在弹窗；来源故障仍允许打开并看到明确指引。 */
async function preparationBoundaries() {
  const page = await makePage();
  const count = Socket.instances.length;
  page.el("start-form").fire("submit");
  assert.equal(Socket.instances.length, count);
  page.el("open-preparation").fire("click");
  page.el("job").value = "  ";
  page.el("start-form").fire("submit");
  assert.equal(page.el("preparation-dialog").open, true);
  assert.match(page.el("preparation-error").textContent, /岗位/);
  assert.equal(Socket.instances.length, count);
  const empty = await makePage({failure:403});
  assert.equal(empty.el("open-preparation").disabled, false);
  empty.el("open-preparation").fire("click");
  assert.equal(empty.el("preparation-dialog").open, true);
  assert.match(empty.el("resume-selection-status").textContent, /登录/);
  assert.equal(empty.el("start-agent").disabled, true);
  assert.equal(Socket.instances.length, count);
}
test("preparation rejects hidden submissions and keeps invalid or unavailable input visible", preparationBoundaries);

/** 元数据就绪后 start 只发送版本 ID；保持阶段计时与原有预算，不发送姓名/邮箱/正文。 */
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
  page.tick(32000); assert.match(page.el("wait-time").textContent,/32 秒/);
  startPrepared(page); assert.equal(ws.sent.length,1);
});

/** current 可以位于后续页；未 ready 版本不进入选项，明确指定不可用版本不自动选择其他版本。 */
test("selection includes later pages and rejects an unavailable explicit version", async () => {
  const versions=[{id:"pending",status:"uploaded"},{id:"version-b",status:"ready",is_current:true}];
  const page=await makePage({versions});
  assert.equal(page.el("resume-select").value,"version-b");assert.equal(page.requests.length,2);
  const missing=await makePage({versions,search:"?resume_version_id=pending"});
  assert.equal(missing.el("resume-select").value,"");assert.equal(missing.el("start-agent").disabled,true);
  assert.match(missing.el("resume-selection-status").textContent,/不可用/);
});

/** 取消后迟到事件不恢复面试，已选版本仍保留，计时清理。 */
test("cancel clears timers and ignores late events while retaining the selected version", async () => {
  const page = await makePage();startPrepared(page);
  const ws = Socket.instances.at(-1);const id=hello(ws);
  page.el("cancel-agent").fire("click");
  ws.emit({type:"progress",request_id:id,stage:"resume_parsing",state:"running"});ws.emit({},"close");
  assert.match(page.el("agent-status").textContent,/已取消/);
  assert.equal(page.el("resume-select").value,"version-a");assert.equal(page.timers.size,0);
});

/** 先行评分尚无文字报告时断线，保留分数并明确未完成，停止计时。 */
test("early assessment stays visible if report connection fails", async () => {
  const page = await makePage();
  startPrepared(page);
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

/** 模拟服务端公告低上限，仅验证客户端边界；不得发送超限内容或遗留计时器。 */
test("oversized command is rejected before send", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  hello(ws, 5);
  assert.equal(ws.sent.length, 0);
  assert.match(page.el("agent-status").textContent, /大小限制/);
  assert.equal(page.timers.size, 0);
});

/** 原有报告回退被服务端标记时，前端必须明确告知来源，不改变有效分数。 */
test("report fallback is explicitly labeled", async () => {
  const page = await makePage();
  startPrepared(page);
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
test("foreign request progress is rejected", async () => {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  hello(ws);
  ws.emit({ type: "progress", request_id: "foreign", stage: "resume_parsing", state: "running" });
  assert.match(page.el("agent-status").textContent, /请求不匹配/);
  assert.equal(page.timers.size, 0);
});

/** 新版客户端与真实语音协调器共享当前问题和回答边界；播放时不允许启动录音。 */
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
  assert.match(page.el("voice-status").textContent, /朗读已停止/);
  const capture = await startCapture(page);
  await page.voice.finishAnswer();
  capture.onFinal("Public fixture answer.", 10);
  assert.equal(ws.sent.at(-1).type, "answer");
  assert.equal(page.el("start-recording").disabled, true);
});

/** 采集/收尾取消使旧回调失效；真实资源释放路径运行，不打开设备或外部服务。 */
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

/** 生成真实客户端当前题；仅 WebSocket/版本接口使用离线替身，题目进入真实业务处理器。 */
async function answeringPage() {
  const page = await makePage();
  startPrepared(page);
  const ws = Socket.instances.at(-1);
  const id = hello(ws);
  ws.emit({ type: "question", request_id: id, question_index: 1,
    question: { question_id: "speech-q", text: "Describe one contribution.", difficulty: 1, dialogue_action: "project" } });
  return { ...page, ws };
}

/** 输入独立 VM 页面；仅替换设备/供应商启动与 flush，保留真实协调器及 close 资源逻辑。
 * 返回采集对象让测试显式交付部分/最终/错误回调，不证明实际 ASR 性能。 */
async function startCapture(page) {
  /** 离线授权和握手完成，不创建麦克风音轨。 */
  page.captureType.prototype.start = async function syntheticStart() { this.recording = true; };
  /** 离线结束只置采集标记；最终文本必须由测试另行交付，不伪造同步成功。 */
  page.captureType.prototype.end = async function syntheticEnd() { this.recording = false; };
  await page.voice.record();
  return page.voice.capture;
}

/** 验证显式结束与最终文本的两阶段边界、字幕纯文本、重复防护及下一题清理。 */
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

/** 原采集时限的 final 不代表用户结束确认；有最终文本后仍须按钮，空白/失败不提交。 */
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

/** 没有可用输入时禁止 start，失败不选择其他版本；这些是模拟权限响应，不替代真实服务权限。 */
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
