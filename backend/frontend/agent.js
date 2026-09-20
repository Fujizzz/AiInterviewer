/**
 * @module agent
 * 功能：同源文字面试客户端，提供预解析、真实阶段、等待计时与先行评分。
 * 实现：每次只发送一个命令；响应绑定连接和 UUID；预解析精确复用同连接简历。
 * 关联：agent.html 提供 DOM，/ws/agent/ 提供 prepare/progress/assessment 事件。
 * 目录：
 * - el：按 ID 查询元素。
 * - controls：按请求与面试状态切换表单；预解析时允许填写岗位设置。
 * - status：以纯文本显示状态。
 * - updateClock：显示实际等待与阶段秒数，不参与逻辑预算。
 * - beginWait：启动新请求唯一计时器。
 * - endWait：停止计时并保留总耗时。
 * - clearPrepared：清除预解析标记与预览。
 * - clearResults：清除旧问答、报告及计时显示。
 * - send：校验消息大小并生成请求 UUID。
 * - stop：关闭连接、计时与预解析，保留已返回评分。
 * - displayAssessment：原样显示后端评分与能力状态。
 * - onMessage：验证连接和请求，再分派完整事件。
 * - onError：当前连接错误后终止，不自动重连。
 * - onClose：意外断开后失效预解析并保留结果。
 * - dispatch：使用空闲连接或等待新连接 hello 后发送一次命令。
 * - onStart：使用原有参数开始面试。
 * - onPrepare：显式调用预解析，不在编辑时发送模型请求。
 * - onResumeInput：编辑后清除已准备标记。
 * - onAnswer：提交当前题答案。
 * - onCancel：关闭整场连接与计时。
 * - onClear：清空会话和候选人内容。
 * - onPageHide：离开时清理连接与计时，不持久化。
 * 关键变量：
 * - el：DOM 查询函数引用。
 * - STAGES：固定服务端阶段名到显示文字的映射。
 * - socket：唯一有效 WebSocket，旧连接事件不能更新界面。
 * - questionId：当前问题 ID。
 * - pendingId：当前请求 UUID。
 * - pendingCommand：等待 hello 的一次性命令。
 * - terminal：是否已主动结束连接。
 * - interviewActive：是否已提交 start，准备阶段为 false。
 * - preparedText：本连接已解析文本，编辑或断线即失效。
 * - submittedResume：当前 prepare 的文本快照。
 * - messageLimit：服务器公告的消息字节上限。
 * - waitStarted：本请求实际起始时刻，null 表示未计时。
 * - stageStarted：当前阶段起始时刻。
 * - clockTimer：仅负责显示的 interval 标识。
 * - completedStages：本请求已完成阶段的耗时文字。
 * 约束：
 * 不推测进度百分比、不重发模型请求、不改变评分或题目预算；
 * 模型内容只经 textContent 展示，取消不能保证供应商已发请求停止。
 */
/** 输入模板唯一 ID，返回 DOM 节点；模板缺失由调用位置显式失败。 */
const el = (id) => document.getElementById(id);
const STAGES = {
  resume_parsing: "解析简历", question_generation: "生成首题", answer_evaluation: "评价回答",
  next_action: "决定下一步并生成问题", report_generation: "生成报告文字",
};
let socket = null;
let questionId = null;
let pendingId = null;
let pendingCommand = null;
let terminal = false;
let interviewActive = false;
let preparedText = null;
let submittedResume = null;
let messageLimit = null;
let waitStarted = null;
let stageStarted = null;
let clockTimer = null;
let completedStages = [];

/** 读取请求与面试状态更新 disabled；准备期间允许岗位设置，无网络副作用。 */
function controls() {
  const busy = pendingId !== null || pendingCommand !== null;
  for (const id of ["start-agent", "prepare-resume", "resume"]) el(id).disabled = busy || interviewActive;
  for (const id of ["job", "limit", "probes"]) el(id).disabled = interviewActive;
  const answering = interviewActive && questionId !== null && !busy;
  el("answer").disabled = !answering;
  el("submit-answer").disabled = !answering;
  el("cancel-agent").disabled = socket === null;
}
/** 输入状态文字，用 textContent 展示，不执行 HTML，输出无。 */
function status(text) { el("agent-status").textContent = text; }
/** 读取单调时钟更新实际秒数；不作为预算、超时、完成或重试条件。 */
function updateClock() {
  if (waitStarted === null) return;
  const now = performance.now();
  const stage = stageStarted === null ? "" : ` · 当前阶段 ${Math.floor((now - stageStarted) / 1000)} 秒`;
  el("wait-time").textContent = `已等待 ${Math.floor((now - waitStarted) / 1000)} 秒${stage}`;
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
  if (waitStarted !== null) el("wait-time").textContent = `本次请求用时 ${((performance.now() - waitStarted) / 1000).toFixed(1)} 秒`;
  waitStarted = null;
  stageStarted = null;
}
/** 清除预解析文本标记和预览；不请求模型，也不直接关闭连接。 */
function clearPrepared() {
  preparedText = null;
  el("prepared-status").textContent = "";
  el("profile-preview").textContent = "";
  el("profile-panel").hidden = true;
}
/** 清空旧问答、报告与时间显示，保留简历、岗位和预解析，不操作网络。 */
function clearResults() {
  questionId = null;
  el("question").textContent = "开始面试后，问题会显示在这里。";
  for (const id of ["question-meta", "evaluation", "report", "score", "report-summary", "assessment", "stage-log", "wait-time"]) el(id).textContent = "";
  el("answer").value = "";
  for (const id of ["evaluation-panel", "report-panel", "assessment-panel"]) el(id).hidden = true;
}
/** 输入命令，校验已公告 UTF-8 上限，绑定 UUID 并发送一次；失败原样传播。 */
function send(command) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("连接已关闭，请重新开始。");
  const id = crypto.randomUUID();
  const text = JSON.stringify({ ...command, request_id: id, progress_events: true });
  if (messageLimit === null || new TextEncoder().encode(text).byteLength > messageLimit) throw new Error("消息超过服务端大小限制，请缩短简历或回答。");
  pendingId = id;
  socket.send(text);
  controls();
}
/** 输入终态提示；关闭连接、待发命令、计时与缓存，保留已展示问题和数值评分。 */
function stop(message) {
  terminal = true;
  const old = socket;
  socket = null;
  if (old) old.close();
  pendingId = null;
  pendingCommand = null;
  interviewActive = false;
  submittedResume = null;
  clearPrepared();
  endWait();
  controls();
  if (!el("report-panel").hidden && el("report").textContent === "") {
    el("report-summary").textContent = "评分已计算，但完整报告未完成。";
  }
  status(message);
}
/** 输入服务端数值评分；展示总分和能力状态，不重新计算、不标记报告完成。 */
function displayAssessment(assessment) {
  el("report-panel").hidden = false;
  el("assessment-panel").hidden = false;
  el("score").textContent = assessment.overall_score === null ? "暂无足够证据评分" : `综合评分：${assessment.overall_score.toFixed(2)} / 5`;
  el("assessment").textContent = JSON.stringify(assessment.competencies, null, 2);
}
/** 输入 MessageEvent；绑定 currentTarget 与请求 UUID，未知事件或解析失败明确停止。 */
function onMessage(event) {
  if (socket !== event.currentTarget) return;
  try {
    const message = JSON.parse(event.data);
    if (message.type === "hello") {
      if (!pendingCommand || !message.capabilities?.includes("progress")) throw new Error("后端版本不支持阶段进度，请更新后端。");
      messageLimit = message.max_message_bytes;
      const command = pendingCommand;
      pendingCommand = null;
      send(command);
      return;
    }
    if (message.type === "error") { stop(`${message.code}：${message.detail}`); return; }
    if (message.request_id !== pendingId) throw new Error("响应与当前请求不匹配。");
    if (message.type === "started") status("请求已接收，等待处理…");
    else if (message.type === "progress") {
      const label = STAGES[message.stage];
      if (!label) throw new Error("未知处理阶段。");
      if (message.state === "running") {
        stageStarted = performance.now();
        status(`正在${label}…`);
      } else if (message.state === "completed") {
        completedStages.push(`${label} ${(message.duration_ms / 1000).toFixed(1)} 秒`);
        el("stage-log").textContent = completedStages.join(" → ");
        stageStarted = null;
      } else throw new Error("未知阶段状态。");
      updateClock();
    } else if (message.type === "prepared") {
      pendingId = null;
      preparedText = submittedResume;
      el("profile-preview").textContent = JSON.stringify(message.candidate_profile, null, 2);
      el("profile-panel").hidden = false;
      el("prepared-status").textContent = "简历已解析。保持此页面连接，开始面试时将直接复用。";
      endWait(); controls(); status("简历已准备，可设置岗位后开始面试");
    } else if (message.type === "assessment") {
      displayAssessment(message.assessment);
      el("report-summary").textContent = "评分已计算，报告文字生成中；完整报告尚未完成。";
    } else if (message.type === "question") {
      questionId = message.question.question_id;
      pendingId = null;
      el("question").textContent = message.question.text;
      el("question-meta").textContent = `第 ${message.question_index} 题 · ${message.question.dialogue_action} · 难度 ${message.question.difficulty}`;
      el("answer").value = "";
      el("evaluation-panel").hidden = !message.last_evaluation;
      el("evaluation").textContent = message.last_evaluation ? JSON.stringify(message.last_evaluation, null, 2) : "";
      endWait(); controls(); status("请回答当前问题"); el("answer").focus();
    } else if (message.type === "finished") {
      const report = message.result.final_report;
      displayAssessment(report);
      const summaryPrefix = message.result.report_narrative_status === "fallback"
        ? "模型报告文字生成失败，以下为既有的确定性摘要；评分保持有效。\n" : "";
      el("report-summary").textContent = summaryPrefix + report.summary;
      el("report").textContent = JSON.stringify(message.result, null, 2);
      stop("面试完成");
    } else if (message.type === "cancelled") stop("面试已取消；已发送的模型请求可能仍会完成。");
    else throw new Error("收到未知响应。");
  } catch (error) { stop(`处理失败：${error.message}`); }
}
/** 输入 error 事件，仅停止当前连接，旧连接事件无副作用。 */
function onError(event) { if (socket === event.currentTarget) stop("连接失败，请检查后端服务。"); }
/** 输入 close 事件，意外断线时失效缓存和计时，不自动重连或声称报告完成。 */
function onClose(event) {
  if (socket === event.currentTarget && !terminal) stop("连接意外关闭，当前操作未完成。预解析已失效，不会自动重发。");
}
/** 输入业务命令，使用空闲连接或创建同源连接等待 hello，不排队或自动重试。 */
function dispatch(command) {
  if (pendingId !== null || pendingCommand !== null) return;
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
      status("正在连接后端…"); controls();
    }
  } catch (error) { stop(error.message); }
}
/** 输入表单事件；校验简历和岗位，以原有预算开始面试，服务端再次匹配预解析文本。 */
function onStart(event) {
  event.preventDefault();
  if (pendingId || pendingCommand || interviewActive) return;
  const resume = el("resume").value.trim();
  const job = el("job").value.trim();
  if (!resume || !job) { status("请填写简历文本和目标岗位。"); return; }
  clearResults(); interviewActive = true;
  dispatch({ type: "start", resume_text: resume, job_title: job,
    max_questions: Number(el("limit").value), max_follow_up_per_topic: Number(el("probes").value) });
}
/** 显式预解析，只要求简历；相同已准备结果不重复发请求，不在输入事件中调用模型。 */
function onPrepare() {
  if (pendingId || pendingCommand || interviewActive) return;
  const resume = el("resume").value.trim();
  if (!resume) { status("请先粘贴简历文本。"); return; }
  if (resume === preparedText && socket?.readyState === WebSocket.OPEN) { status("简历已准备，无需重复解析。"); return; }
  clearPrepared(); submittedResume = resume;
  dispatch({ type: "prepare", resume_text: resume });
}
/** 简历编辑后只清空浏览器预览与准备标记；下次命令携带完整新文本供后端精确匹配。 */
function onResumeInput() {
  if (preparedText !== null) { clearPrepared(); el("prepared-status").textContent = "简历已修改，需要重新解析。"; }
}
/** 输入回答表单事件，仅在当前题且无在途请求时提交；评价与下一题仍由后端顺序处理。 */
function onAnswer(event) {
  event.preventDefault();
  const answer = el("answer").value.trim();
  if (!answer || !questionId || pendingId || pendingCommand) return;
  status("正在提交回答…");
  dispatch({ type: "answer", question_id: questionId, answer_text: answer });
}
/** 用户取消时关闭连接和显示计时，不保证供应商已发请求停止或不计费。 */
function onCancel() { stop("已取消；已发送的模型请求可能仍会完成。"); }
/** 清空连接、问答及所有候选人文本，岗位和题数参数保持原值。 */
function onClear() { stop("内容已清空"); clearResults(); el("resume").value = ""; submittedResume = null; }
/** 页面离开后停止连接与 interval，不使用浏览器持久存储恢复会话。 */
function onPageHide() { stop("页面已离开，连接已结束。"); }

el("start-form").addEventListener("submit", onStart);
el("prepare-resume").addEventListener("click", onPrepare);
el("resume").addEventListener("input", onResumeInput);
el("answer-form").addEventListener("submit", onAnswer);
el("cancel-agent").addEventListener("click", onCancel);
el("clear-agent").addEventListener("click", onClear);
window.addEventListener("pagehide", onPageHide);
