/**
 * @module agent
 * 功能：文字面试测试页：通过同源 WebSocket 协调简历提交、逐题回答、评价和报告展示。
 *
 * 目录：
 * - el：
 *   按唯一 ID 读取页面元素。
 * - controls：
 *   按连接阶段统一切换表单禁用状态。
 * - status：
 *   以纯文本更新用户可见状态。
 * - clearResults：
 *   清除问题、答案、报告与请求关联，保留简历和参数供调用者决定是否复用。
 * - send：
 *   生成请求 UUID 并发送一条命令，保留关联标识。
 * - stop：
 *   使旧连接失效并恢复控件，保留结果供查看。
 * - callback1：
 *   开始事件：读取并校验表单，创建同源连接；收到 hello 公告后才提交简历。
 * - callback1.callback1：
 *   消息事件：以连接身份和请求 UUID 隔离响应，按完整问题/报告更新 UI。
 * - callback1.callback2：
 *   传输错误事件：提示检查服务，不尝试重连或重复触发模型请求。
 * - callback1.callback3：
 *   关闭事件：只有尚未进入页面终态的当前连接，才被解释为意外中断。
 * - callback2：
 *   回答事件：只允许非空答案关联当前问题，发送后立即禁用输入等待服务器结果。
 * - callback3：
 *   取消事件：直接断开整场连接，保留当前界面供查看，不承诺远端请求停止。
 * - callback4：
 *   清空事件：先隔离旧连接，再清除问题、答案、报告与简历，保留岗位和预算设置。
 * - callback5：
 *   页面离开事件：释放当前连接，不向浏览器持久存储写入面试上下文。
 *
 * 关键变量：
 * - el：
 *   DOM 查询函数引用。
 * - socket：
 *   当前有效 WebSocket；旧连接回调必须核对该引用。
 * - questionId：
 *   服务端当前问题 ID，提交答案时用于防止回答旧问题。
 * - pendingId：
 *   尚未完成命令的 UUID；收到完整问题后清空。
 * - terminal：
 *   页面是否主动进入完成、取消或错误终态，避免重复提示意外断线。
 *
 * 关键状态说明：
 * 事件路径：start-form 创建连接，hello 触发初始化；answer-form 逐题提交；消息回调按 UUID 展示结果。cancel-agent
 * 直接断开，clear-agent 同时清空简历，pagehide 关闭连接。无浏览器持久存储，不自动重连或重发。
 */
/** 按页面内唯一 ID 取得 DOM 元素；模板必须提供该元素，调用方负责具体读写操作。 */
const el = (id) => document.getElementById(id);
let socket = null;
let questionId = null;
let pendingId = null;
let terminal = false;

/**
 * 根据连接阶段统一切换表单可编辑性，防止处理中重复开始或重复提交。
 * @param {boolean} active 是否存在正在建立或进行中的面试。
 * @param {boolean} answering 是否已有当前问题且允许填写答案；默认不允许。
 * 副作用：只修改控件 disabled，不打开连接或改变 Agent 状态。
 */
function controls(active, answering = false) {
  el("start-agent").disabled = active;
  for (const id of ["resume", "job", "limit", "probes"]) el(id).disabled = active;
  el("answer").disabled = !answering;
  el("submit-answer").disabled = !answering;
  el("cancel-agent").disabled = !active;
}

/** 用 textContent 更新可访问状态提示；模型和错误文本均不作为 HTML 执行。 */
function status(text) { el("agent-status").textContent = text; }

/**
 * 释放页面对上一轮问题、请求和报告文本的引用，并恢复结果区域初始展示。
 * 前置条件：调用者负责先结束旧连接；本函数不关闭网络，也不清空简历和岗位参数。
 * 开始新面试时复用简历；“清空内容”事件另行清除简历输入，两者语义明确区分。
 */
function clearResults() {
  questionId = null;
  pendingId = null;
  el("question").textContent = "开始面试后，问题会显示在这里。";
  for (const id of ["question-meta", "evaluation", "report", "score", "report-summary"]) {
    el(id).textContent = "";
  }
  el("answer").value = "";
  el("evaluation-panel").hidden = true;
  el("report-panel").hidden = true;
}

/**
 * 为一条业务命令生成 UUID 并立即发送，记录 pendingId 以关联后续响应。
 * @param {object} command start 或 answer 命令；调用者保证没有未完成的业务请求。
 * 异常：连接未打开或发送失败时向上传播；不缓存命令，也不自动重发。
 */
function send(command) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("连接已关闭，请重新开始面试。");
  pendingId = crypto.randomUUID();
  socket.send(JSON.stringify({ ...command, request_id: pendingId }));
}

/**
 * 标记页面终态并关闭连接，使旧连接的后续事件不能覆盖当前结果。
 * 参数 message 为用户可见状态；问题与报告保留供查看，清理由 clearResults 显式执行。
 * close 仅停止本地会话，不能保证已发送的同步模型请求在供应商处取消。
 */
function stop(message) {
  terminal = true;
  if (socket) socket.close();
  socket = null;
  controls(false);
  status(message);
}

/** 开始事件：读取并校验表单，创建同源连接；收到 hello 公告后才提交简历。 */
el("start-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const resume = el("resume").value.trim();
  const job = el("job").value.trim();
  if (!resume || !job) { status("请填写简历文本和目标岗位。"); return; }
  clearResults();
  terminal = false;
  controls(true);
  status("正在连接后端…");
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/agent/`);
  socket = ws;
  /** 消息事件：以连接身份和请求 UUID 隔离响应，按完整问题/报告更新 UI。 */
  ws.addEventListener("message", (event) => {
    // 局部 ws 代表本次连接；重启或清空后 socket 已变化，旧事件不得继续修改页面。
    if (socket !== ws) return;
    try {
      const message = JSON.parse(event.data);
      if (message.type === "hello") {
        send({ type: "start", resume_text: resume, job_title: job,
          max_questions: Number(el("limit").value),
          max_follow_up_per_topic: Number(el("probes").value) });
        status("正在解析简历并生成问题…");
      } else if (message.type === "error") {
        // 测试页在任何服务端错误后主动结束；协议本身允许部分校验错误修正后再提交。
        stop(`${message.code}：${message.detail}`);
      } else if (message.request_id !== pendingId) {
        throw new Error("响应与当前请求不匹配。");
      } else if (message.type === "started") {
        status(message.operation === "start" ? "正在解析简历并生成问题…" : "正在评价回答并生成下一步…");
      } else if (message.type === "question") {
        // 完整问题到达后才释放 pendingId 并允许下一次回答，避免一次问题被重复提交。
        questionId = message.question.question_id;
        pendingId = null;
        el("question").textContent = message.question.text;
        el("question-meta").textContent = `第 ${message.question_index} 题 · ${message.question.target_competency} · 难度 ${message.question.difficulty}`;
        el("answer").value = "";
        el("evaluation-panel").hidden = !message.last_evaluation;
        el("evaluation").textContent = message.last_evaluation ? JSON.stringify(message.last_evaluation, null, 2) : "";
        controls(true, true);
        status("请回答当前问题");
        el("answer").focus();
      } else if (message.type === "finished") {
        // 分数由后端计算；页面只格式化展示，不根据叙述重新推断或修改评分。
        const result = message.result;
        const report = result.final_report;
        el("report-panel").hidden = false;
        el("score").textContent = report.overall_score === null ? "暂无足够证据评分" : `综合评分：${report.overall_score.toFixed(2)} / 5`;
        el("report-summary").textContent = report.summary;
        el("report").textContent = JSON.stringify(result, null, 2);
        stop("面试完成");
      } else if (message.type === "cancelled") {
        stop("面试已取消；已发送的模型请求可能仍会完成。");
      } else {
        throw new Error("收到未知响应。");
      }
    } catch (error) { stop(`测试失败：${error.message}`); }
  });
  /** 传输错误事件：提示检查服务，不尝试重连或重复触发模型请求。 */
  ws.addEventListener("error", () => {
    if (socket === ws) stop("连接失败，请检查后端是否启动及控制台日志。");
  });
  /** 关闭事件：只有尚未进入页面终态的当前连接，才被解释为意外中断。 */
  ws.addEventListener("close", () => {
    if (socket === ws && !terminal) stop("连接意外关闭，面试未完成。不会自动重连或重发。");
  });
});

/** 回答事件：只允许非空答案关联当前问题，发送后立即禁用输入等待服务器结果。 */
el("answer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const answer = el("answer").value.trim();
  if (!answer || !questionId || pendingId) return;
  try {
    send({ type: "answer", question_id: questionId, answer_text: answer });
    controls(true);
    status("正在提交回答…");
  } catch (error) { stop(error.message); }
});

/** 取消事件：直接断开整场连接，保留当前界面供查看，不承诺远端请求停止。 */
el("cancel-agent").addEventListener("click", () => {
  // 关闭连接即可取消本地任务，也可在握手尚未完成时立即停止。
  stop("面试已取消；已发送的模型请求可能仍会完成。");
});
/** 清空事件：先隔离旧连接，再清除问题、答案、报告与简历，保留岗位和预算设置。 */
el("clear-agent").addEventListener("click", () => {
  stop("内容已清空");
  clearResults();
  el("resume").value = "";
});
/** 页面离开事件：释放当前连接，不向浏览器持久存储写入面试上下文。 */
window.addEventListener("pagehide", () => { if (socket) socket.close(); });
