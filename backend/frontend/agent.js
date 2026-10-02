/**
 * @module agent
 * 职责：选择个人中心已就绪简历并进行语音/文字面试，提供真实阶段、计时、评分和报告。
 * 实现：读取本人分页元数据；开始入口打开原生准备弹窗，确认后才通过 UUID 发送 start。
 * 关联：agent.html、i18n.js、/api/resume-versions/、/ws/agent/；资料维护集中在 /resumes/。
 * 目录：
 * - el：按 ID 查询元素。
 * - uiText：读取当前语言动态文案并插值。
 * - uiText.callback1：纯文本替换命名参数。
 * - controls：按版本加载、请求和面试状态切换控件。
 * - status：显示纯文本状态。
 * - loadResumeVersions：加载全部本人版本元数据，只显示 ready，不调用模型。
 * - onResumeSelect：验证手动选择并清空旧面试展示。
 * - updateClock：显示实际等待与阶段秒数。
 * - beginWait：启动新请求唯一计时器。
 * - endWait：停止计时并保留实际耗时。
 * - clearResults：清除旧问答、报告及计时显示。
 * - send：校验消息大小并发送唯一请求 UUID。
 * - stop：关闭连接、命令与计时，保留已展示评分和版本选择。
 * - displayAssessment：展示后端评分与能力状态。
 * - onMessage：按连接和请求 UUID 分派完整事件。
 * - onError：停止当前错误连接。
 * - onClose：意外断线停止计时，不重连。
 * - dispatch：创建或复用连接，等待 hello 后发送一次命令。
 * - onStart：使用已加载 ready 版本 UUID 和原有参数开始面试。
 * - onOpenPreparation：空闲时打开准备弹窗，保留输入并聚焦版本选择，不连接后端。
 * - onClosePreparation：显式关闭准备弹窗，不清空输入或发起面试。
 * - onPreparationClosed：关闭后将焦点交还入口或正在准备首题的状态区域。
 * - onPreparationKeydown：在弹窗首尾循环 Tab 焦点；Esc 继续由原生 dialog 处理。
 * - onPreparationLanguage：重绘版本标签和已知选择状态，不请求网络或改变已选版本。
 * - onAnswer：仅在未朗读/录音且请求空闲时提交确认答案。
 * - onCancel：取消当前面试连接。
 * - onClear：清空面试问答，保留版本与岗位设置。
 * - onPageHide：离开时清理连接、计时、录音和数字人资源。
 * 关键变量：
 * - el：模板节点查询函数。
 * - STAGES：服务端阶段名对应翻译 key。
 * - FALLBACK_TEXT：独立客户端测试未加载 i18n 时的中文资源。
 * - uiText：动态文案查询函数。
 * - socket：当前唯一 WebSocket。
 * - voice：数字人呈现和语音交互协调器，不参与面试评分。
 * - questionId：当前问题 ID。
 * - pendingId：当前请求 UUID。
 * - pendingCommand：等待 hello 的唯一命令。
 * - terminal：当前连接是否主动结束。
 * - interviewActive：是否已开始面试。
 * - resumesLoading：版本列表请求锁，防止在完成前发送 start。
 * - availableResumes：本人所有 ready 版本元数据，无原始正文。
 * - messageLimit：服务端公告消息字节上限。
 * - waitStarted：实际等待起点。
 * - stageStarted：当前阶段起点。
 * - clockTimer：显示用 interval ID。
 * - completedStages：已完成阶段实际耗时文案。
 * - preparationInvoker：打开准备弹窗的按钮，关闭后恢复焦点；不保存用户资料。
 * - resumeSelectionMessage：选择区动态文案 key；未知异常正文保持原错误，不按语言伪造状态。
 * 约束：
 * 不上传或编辑简历，不改变预算/评分；无重发、自动选择其他版本或模型调用降级。
 */
import { InterviewVoice } from "./interview-voice.js";

/** 输入模板唯一 ID，返回 DOM 节点；模板缺失由调用位置显式失败。 */
const el = (id) => document.getElementById(id);
const voice = new InterviewVoice();
const STAGES = {
  resume_parsing: "agent_stage_resume", question_generation: "agent_stage_question", answer_evaluation: "agent_stage_evaluation",
  next_action: "agent_stage_next", report_generation: "agent_stage_report",
};
const FALLBACK_TEXT = {
  agent_resume_choice: "选择已保存简历",
  agent_resume_select: "请选择已就绪的简历版本",
  agent_manage_profile: "前往个人中心维护资料与简历 ↗",
  agent_resumes_loading: "正在加载已保存简历…",
  agent_resumes_failed: "加载简历失败，请点击刷新后查看。",
  agent_resume_unavailable: "指定或此前选择的简历不可用，请重新选择已就绪版本。",
  agent_resumes_ready: "这里只选择面试使用的版本。资料上传、解析与维护请前往个人中心。",
  agent_resumes_empty: "暂无已就绪简历，请到个人中心上传并完成解析。",
  agent_resume_required: "请选择已就绪的简历并填写目标岗位。",
  agent_unexpected_close: "连接意外关闭，当前操作未完成。不会自动重发。",
  agent_message_limit: "消息超过服务端大小限制，请缩短回答或检查面试设置。",
  agent_stage_resume: "解析简历",
  agent_stage_question: "生成首题",
  agent_stage_evaluation: "评价回答",
  agent_question_placeholder: "开始面试后，问题会显示在这里。",
  agent_stage_next: "决定下一步并生成问题",
  agent_stage_report: "生成报告文字",
  agent_waiting: "已等待 {seconds} 秒",
  agent_waiting_stage: "已等待 {seconds} 秒 · 当前阶段 {stage} 秒",
  agent_request_time: "本次请求用时 {seconds} 秒",
  agent_closed: "连接已关闭，请重新开始。",
  agent_report_incomplete: "评分已计算，但完整报告未完成。",
  agent_score_missing: "暂无足够证据评分",
  agent_score: "综合评分：{score} / 5",
  agent_request_received: "请求已接收，等待处理…",
  agent_question_answer: "请回答当前问题",
  agent_submit: "正在提交回答…",
  agent_connecting: "正在连接后端…",
  agent_unknown_stage: "未知处理阶段。",
  agent_unknown_response: "收到未知响应。",
  agent_backend_old: "后端版本不支持阶段进度，请更新后端。",
  agent_mismatch: "响应与当前请求不匹配。",
  report_generating: "评分已计算，报告文字生成中；完整报告尚未完成。",
  agent_finished: "面试完成",
  agent_cancelled: "面试已取消；已发送的模型请求可能仍会完成。",
  agent_cancel_message: "已取消；已发送的模型请求可能仍会完成。",
  agent_processing_failed: "处理失败：{message}",
  agent_connection_failed: "连接失败，请检查后端服务。",
  agent_page_left: "页面已离开，连接已结束。",
  agent_cleared: "内容已清空",
  agent_report_fallback: "模型报告文字生成失败，以下为既有的确定性摘要；评分保持有效。\n",
  rm_auth_error: "登录已失效或请求未获授权，请重新登录后操作。",
  rm_unnamed: "未命名文本简历",
  rm_current: "当前简历",
};
/** 返回翻译文案；独立运行测试只加载本模块时使用中文兼容回退。 */
const uiText = (key, values = {}) => {
  const template = window.AppI18n?.t(key, values) ?? (FALLBACK_TEXT[key] ?? key);
  return template.replace(/\{(\w+)\}/g, /** 输入匹配内容与参数名，返回纯文本替换，不解析 HTML。 */ (_, name) => String(values[name] ?? `{${name}}`));
};
let socket = null;
let questionId = null;
let pendingId = null;
let pendingCommand = null;
let terminal = false;
let interviewActive = false;
let resumesLoading = false;
let availableResumes = [];
let messageLimit = null;
let waitStarted = null;
let stageStarted = null;
let clockTimer = null;
let completedStages = [];
let preparationInvoker = null;
let resumeSelectionMessage = null;

/** 读取请求与面试状态更新 disabled；版本加载及会话期间保护版本选择，无网络副作用。 */
function controls() {
  const busy = pendingId !== null || pendingCommand !== null;
  for (const id of ["open-preparation", "interview-settings"]) el(id).disabled = busy || interviewActive;
  el("start-agent").disabled = busy || interviewActive || resumesLoading || !el("resume-select").value;
  for (const id of ["resume-select", "refresh-resumes"]) el(id).disabled = busy || interviewActive || resumesLoading;
  for (const id of ["job", "duration", "limit", "probes"]) el(id).disabled = interviewActive;
  const answering = interviewActive && questionId !== null && !busy;
  el("answer").disabled = !answering;
  el("submit-answer").disabled = !answering;
  el("cancel-agent").disabled = socket === null;
  window.dispatchEvent(new CustomEvent("interview-controls", { detail: { active: interviewActive, answering } }));
}
/** 输入状态文字，用 textContent 展示，不执行 HTML，输出无。 */
function status(text) { el("agent-status").textContent = text; }
/** 读取本人分页元数据，返回无；初次使用 URL 指定 ID 或 current，刷新保持原选择。
 * 只提供 ready 版本；指定/既选版本消失时明确要求重选，不静默换成其他版本，不调用模型。
 * 失败清空选择、记录异常类型并允许显式刷新；已开始面试或正在加载时不发重复请求。
 */
async function loadResumeVersions() {
  if (resumesLoading || interviewActive || pendingId || pendingCommand) return;
  const previous = el("resume-select").value;
  const requested = new URLSearchParams(location.search).get("resume_version_id");
  resumesLoading = true; controls();
  resumeSelectionMessage = "agent_resumes_loading";
  el("resume-selection-status").textContent = uiText("agent_resumes_loading");
  try {
    const versions = [];
    let page = 1;
    while (true) {
      const response = await fetch(`/api/resume-versions/?page=${page}`, { credentials: "same-origin" });
      if (!response.ok) {
        resumeSelectionMessage = response.status === 401 || response.status === 403 ? "rm_auth_error" : "agent_resumes_failed";
        throw new Error(uiText(resumeSelectionMessage));
      }
      const data = await response.json();
      for (const version of data.results) if (version.status === "ready") versions.push(version);
      if (!data.next) break;
      page += 1;
    }
    availableResumes = versions;
    const select = el("resume-select");
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = ""; placeholder.textContent = uiText("agent_resume_select"); select.append(placeholder);
    let current = "";
    for (const version of versions) {
      const option = document.createElement("option"); option.value = version.id;
      option.textContent = version.label || version.original_name || uiText("rm_unnamed");
      if (version.is_current) { current = version.id; option.textContent += " · " + uiText("rm_current"); }
      select.append(option);
    }
    const selected = previous || requested || current;
    let found = false;
    for (const version of versions) if (version.id === selected) found = true;
    select.value = found ? selected : "";
    resumeSelectionMessage = selected && !found ? "agent_resume_unavailable" : versions.length ? "agent_resumes_ready" : "agent_resumes_empty";
    el("resume-selection-status").textContent = uiText(resumeSelectionMessage);
  } catch (error) {
    availableResumes = [];
    el("resume-select").replaceChildren();
    el("resume-select").value = "";
    console.error("Interview resume list failed", error.name);
    if (resumeSelectionMessage === "agent_resumes_loading") resumeSelectionMessage = null;
    el("resume-selection-status").textContent = error.message;
  } finally { resumesLoading = false; controls(); }
}
/** 手动选择仅更新本次面试输入并清除旧展示，不修改后端当前版本或调用模型。 */
function onResumeSelect() { el("preparation-error").textContent = ""; clearResults(); controls(); }
/** 读取单调时钟更新实际秒数；不作为预算、超时、完成或重试条件。 */
function updateClock() {
  if (waitStarted === null) return;
  const now = performance.now();
  const seconds = Math.floor((now - waitStarted) / 1000);
  const stageSeconds = stageStarted === null ? null : Math.floor((now - stageStarted) / 1000);
  el("wait-time").textContent = stageSeconds === null
    ? uiText("agent_waiting", { seconds })
    : uiText("agent_waiting_stage", { seconds, stage: stageSeconds });
}
/** 开始新命令时重置显示时钟，先清理旧 interval，不发网络请求。 */
function beginWait() {
  if (clockTimer !== null) clearInterval(clockTimer);
  waitStarted = performance.now();
  stageStarted = null;
  completedStages = [];
  el("stage-log").textContent = "";
  clockTimer = setInterval(updateClock, 1000);
  updateClock();
}
/** 停止 interval 并显示请求总耗时；已返回的问题和评分不清除。 */
function endWait() {
  if (clockTimer !== null) clearInterval(clockTimer);
  clockTimer = null;
  if (waitStarted !== null) el("wait-time").textContent = uiText("agent_request_time", { seconds: ((performance.now() - waitStarted) / 1000).toFixed(1) });
  waitStarted = null;
  stageStarted = null;
}
/** 清空旧问答、报告与时间显示，保留已保存版本选择和岗位，不操作网络。 */
function clearResults() {
  voice.reset();
  voice.setState("idle");
  questionId = null;
  el("question").textContent = uiText("agent_question_placeholder");
  for (const id of ["question-meta", "evaluation", "report", "score", "report-summary", "assessment", "stage-log", "wait-time"]) el(id).textContent = "";
  el("answer").value = "";
  for (const id of ["evaluation-panel", "report-panel", "assessment-panel"]) el(id).hidden = true;
}
/** 输入命令，校验已公告 UTF-8 上限，绑定 UUID 并发送一次；失败原样传播。 */
function send(command) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error(uiText("agent_closed"));
  const id = crypto.randomUUID();
  const text = JSON.stringify({ ...command, request_id: id, progress_events: true });
  if (messageLimit === null || new TextEncoder().encode(text).byteLength > messageLimit) throw new Error(uiText("agent_message_limit"));
  pendingId = id;
  socket.send(text);
  controls();
}
/** 输入终态提示；关闭连接、待发命令、计时与缓存，保留已展示问题和数值评分。 */
function stop(message) {
  voice.reset();
  voice.setState("idle");
  terminal = true;
  const old = socket;
  socket = null;
  if (old) old.close();
  pendingId = null;
  pendingCommand = null;
  interviewActive = false;
  endWait();
  controls();
  if (!el("report-panel").hidden && el("report").textContent === "") {
    el("report-summary").textContent = uiText("agent_report_incomplete");
  }
  status(message);
}
/** 输入服务端数值评分；展示总分和能力状态，不重新计算、不标记报告完成。 */
function displayAssessment(assessment) {
  el("report-panel").hidden = false;
  el("assessment-panel").hidden = false;
  el("score").textContent = assessment.overall_score === null ? uiText("agent_score_missing") : uiText("agent_score", { score: assessment.overall_score.toFixed(2) });
  el("assessment").textContent = JSON.stringify(assessment.competencies, null, 2);
}
/** 输入 MessageEvent；绑定 currentTarget 与请求 UUID，未知事件或解析失败明确停止。 */
function onMessage(event) {
  if (socket !== event.currentTarget) return;
  try {
    const message = JSON.parse(event.data);
    if (message.type === "hello") {
      if (!pendingCommand || !message.capabilities?.includes("progress")) throw new Error(uiText("agent_backend_old"));
      messageLimit = message.max_message_bytes;
      const command = pendingCommand;
      pendingCommand = null;
      send(command);
      return;
    }
    if (message.type === "error") { stop(`${message.code}：${message.detail}`); return; }
    if (message.request_id !== pendingId) throw new Error(uiText("agent_mismatch"));
    if (message.type === "started") status(uiText("agent_request_received"));
    else if (message.type === "progress") {
      const label = STAGES[message.stage];
      if (!label) throw new Error(uiText("agent_unknown_stage"));
      const stageText = uiText(label);
      if (message.state === "running") {
        stageStarted = performance.now();
        status(window.AppI18n?.language() === "en" ? `${stageText}…` : `正在${stageText}…`);
      } else if (message.state === "completed") {
        completedStages.push(`${stageText} ${(message.duration_ms / 1000).toFixed(1)} ${window.AppI18n?.language() === "en" ? "s" : "秒"}`);
        el("stage-log").textContent = completedStages.join(" → ");
        stageStarted = null;
      } else throw new Error(uiText("agent_unknown_stage"));
      updateClock();
    } else if (message.type === "assessment") {
      displayAssessment(message.assessment);
      el("report-summary").textContent = uiText("report_generating");
    } else if (message.type === "question") {
      questionId = message.question.question_id;
      pendingId = null;
      el("question").textContent = message.question.text;
      el("question-meta").textContent = window.AppI18n?.language() === "en"
        ? `Question ${message.question_index} · ${message.question.dialogue_action} · Difficulty ${message.question.difficulty}`
        : `第 ${message.question_index} 题 · ${message.question.dialogue_action} · 难度 ${message.question.difficulty}`;
      el("answer").value = "";
      el("evaluation-panel").hidden = !message.last_evaluation;
      el("evaluation").textContent = message.last_evaluation ? JSON.stringify(message.last_evaluation, null, 2) : "";
      const backendWaitMs = waitStarted === null ? null : performance.now() - waitStarted;
      endWait(); controls(); status(uiText("agent_question_answer")); el("answer").focus();
      voice.setQuestion(message.question, backendWaitMs);
    } else if (message.type === "finished") {
      const report = message.result.final_report;
      displayAssessment(report);
      const summaryPrefix = message.result.report_narrative_status === "fallback" ? uiText("agent_report_fallback") : "";
      el("report-summary").textContent = summaryPrefix + report.summary;
      el("report").textContent = JSON.stringify(message.result, null, 2);
      stop(uiText("agent_finished"));
    } else if (message.type === "cancelled") stop(uiText("agent_cancelled"));
    else throw new Error(uiText("agent_unknown_response"));
  } catch (error) { stop(uiText("agent_processing_failed", { message: error.message })); }
}
/** 输入 error 事件，仅停止当前连接，旧连接事件无副作用。 */
function onError(event) { if (socket === event.currentTarget) stop(uiText("agent_connection_failed")); }
/** 输入 close 事件，意外断线时清理计时，不自动重连或声称报告完成。 */
function onClose(event) {
  if (socket === event.currentTarget && !terminal) stop(uiText("agent_unexpected_close"));
}
/** 输入业务命令，使用空闲连接或创建同源连接等待 hello，不排队或自动重试。 */
function dispatch(command) {
  if (pendingId !== null || pendingCommand !== null) return;
  voice.setState("thinking");
  beginWait(); terminal = false;
  try {
    if (socket && socket.readyState === WebSocket.OPEN) send(command);
    else {
      pendingCommand = command;
      messageLimit = null;
      socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/agent/`);
      socket.addEventListener("message", onMessage);
      socket.addEventListener("error", onError);
      socket.addEventListener("close", onClose);
      status(uiText("agent_connecting")); controls();
    }
  } catch (error) { stop(error.message); }
}
/** 输入开始表单事件；弹窗内显式确认才发送 ready 版本 ID，不发送正文。
 * 原生 required/min/step 校验沿用表单；客户端再检查已载元数据和岗位，失败留在弹窗。
 * 确认后关闭弹窗并进入原调度器，后端再次校验归属与状态；预算和错误语义保持原值。
 */
function onStart(event) {
  event.preventDefault();
  if (!el("preparation-dialog").open || pendingId || pendingCommand || interviewActive || resumesLoading) return;
  const id = el("resume-select").value;
  const job = el("job").value.trim();
  let ready = false;
  for (const version of availableResumes) if (version.id === id) ready = true;
  if (!ready || !job) {
    el("preparation-error").textContent = uiText("agent_resume_required");
    return;
  }
  clearResults(); interviewActive = true;
  el("preparation-dialog").close();
  dispatch({ type: "start", resume_version_id: id, job_title: job,
    duration_minutes: Number(el("duration").value),
    max_questions: Number(el("limit").value), max_follow_up_per_topic: Number(el("probes").value) });
}
/** 输入开始/设置按钮点击事件；空闲时打开原生模态框，不请求模型、设备或修改版本。
 * 保存调用按钮以恢复焦点；元数据加载中先聚焦标题，否则聚焦简历选择，原生 dialog 限制焦点。
 */
function onOpenPreparation(event) {
  if (interviewActive || pendingId || pendingCommand || el("preparation-dialog").open) return;
  preparationInvoker = event.currentTarget;
  onPreparationLanguage();
  el("preparation-error").textContent = "";
  el("preparation-dialog").showModal();
  el(resumesLoading ? "preparation-title" : "resume-select").focus();
}
/** 用户点击关闭或稍后开始；原生 Esc 也走同一 close 事件，保留表单输入，不操作连接。 */
function onClosePreparation() { el("preparation-dialog").close(); }
/** 原生 close 事件后恢复焦点；面试已确认时定位准备进度，否则返回打开弹窗的按钮。 */
function onPreparationClosed() {
  if (interviewActive) el("agent-status").focus();
  else preparationInvoker?.focus();
}
/** 输入原生键盘事件；首尾 Tab 循环在可见且启用的控件间，防止焦点离开弹窗。
 * 仅处理 Tab，不拦截 Esc；元素集按当前 disabled/布局读取，不保存或重排表单状态。
 */
function onPreparationKeydown(event) {
  if (event.key !== "Tab") return;
  const focusable = [];
  for (const node of el("preparation-dialog").querySelectorAll("button, input, select, a[href]")) {
    if (!node.disabled && node.tabIndex >= 0 && node.getClientRects().length) focusable.push(node);
  }
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
}
/** 语言切换仅更新当前版本选项、已知状态和可见确认错误，不改变选择、参数或请求。
 * 原始版本名保持原样；已知状态使用稳定 key，非预期解析异常保留现有错误文本。
 */
function onPreparationLanguage() {
  for (const option of el("resume-select").children) {
    if (!option.value) option.textContent = uiText("agent_resume_select");
    else for (const version of availableResumes) if (version.id === option.value) {
      option.textContent = (version.label || version.original_name || uiText("rm_unnamed")) + (version.is_current ? " · " + uiText("rm_current") : "");
    }
  }
  if (resumeSelectionMessage) el("resume-selection-status").textContent = uiText(resumeSelectionMessage);
  if (el("preparation-error").textContent) el("preparation-error").textContent = uiText("agent_resume_required");
}
/** 输入回答表单事件，仅在当前题且无在途请求时提交；评价与下一题仍由后端顺序处理。 */
function onAnswer(event) {
  event.preventDefault();
  const answer = el("answer").value.trim();
  if (!answer || !questionId || pendingId || pendingCommand || voice.busy || voice.capture) return;
  voice.reset();
  status(uiText("agent_submit"));
  dispatch({ type: "answer", question_id: questionId, answer_text: answer });
}
/** 用户取消时关闭连接和显示计时，不保证供应商已发请求停止或不计费。 */
function onCancel() { stop(uiText("agent_cancel_message")); }
/** 清空连接和当前面试展示；已选版本、岗位和题数参数保持原值。 */
function onClear() { stop(uiText("agent_cleared")); clearResults(); }
/** 页面离开后停止连接与 interval，不使用浏览器持久存储恢复会话。 */
function onPageHide() { stop(uiText("agent_page_left")); voice.close(); }

el("start-form").addEventListener("submit", onStart);
el("open-preparation").addEventListener("click", onOpenPreparation);
el("interview-settings").addEventListener("click", onOpenPreparation);
el("close-preparation").addEventListener("click", onClosePreparation);
el("cancel-preparation").addEventListener("click", onClosePreparation);
el("preparation-dialog").addEventListener("close", onPreparationClosed);
el("preparation-dialog").addEventListener("keydown", onPreparationKeydown);
el("interview-language").addEventListener("change", onPreparationLanguage);
el("resume-select").addEventListener("change", onResumeSelect);
el("refresh-resumes").addEventListener("click", loadResumeVersions);
el("answer-form").addEventListener("submit", onAnswer);
el("cancel-agent").addEventListener("click", onCancel);
el("clear-agent").addEventListener("click", onClear);
window.addEventListener("pagehide", onPageHide);
window.addEventListener("languagechange", onPreparationLanguage);

loadResumeVersions();
