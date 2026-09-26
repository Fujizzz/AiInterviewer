/**
 * @module resume-pdf
 * 职责：上传 PDF、展示真实进度、最终文本与必要疑点，人工采用后失效既有简历预解析，并翻译上传状态。
 * 实现：从服务端模板读取 CSRF token 随上传发送；fetch 读取 NDJSON，取消使用 AbortController；与面试状态通过 DOM 禁用属性联动；状态文案经 i18n.js 解析。
 * 关联：agent.html、i18n.js、/api/resume/parse/；不将 PDF 放进 WebSocket，不持久化文件或文本。
 * 目录：
 * - pdfEl：查找模板元素。
 * - pdfText：取得当前语言的 PDF 状态文案。
 * - pdfText.callback1：以纯文本替换状态文案中的命名参数。
 * - pdfControls：更新 PDF 控件，不修改面试脚本维护的状态。
 * - pdfClock：显示实际耗时，不估计完成百分比。
 * - pdfReset：取消请求并清除所选文件对应的旧结果。
 * - pdfCancel：终止本次接收，停止接收，禁止采用未完成结果。
 * - pdfEvent：处理经过 JSON 解码的阶段及结果。
 * - pdfRun：携带模板 CSRF token 上传文件并消费 UTF-8 流，拒绝缺失终态。
 * - pdfCancelReadError：记录已取消流的清理错误类型，不覆盖原错误。
 * - pdfUse：人工采用文本并派发 input 使既有候选人缓存失效。
 * - pdfClear：清空时取消解析并删除选定文件及结果。
 * 关键变量：
 * - pdfEl：DOM 查询函数。
 * - pdfController：当前唯一请求；旧请求不能改变新请求界面。
 * - pdfTimer：显示计时器，结束或取消清理。
 * - pdfStarted：实际开始时间，不参与模型或面试预算。
 * - pdfReviewed：仅收到成功终态且流完整结束后允许采用校对结果。
 * - pdfObserver：观察简历禁用状态，避免面试期间替换输入。
 * - PDF_FALLBACK：独立客户端测试未加载 i18n.js 时使用的既有中文资源。
 * - pdfText：PDF 状态文案查询函数。
 * 约束：
 * 模型输出仅写 value/textContent；采用前始终可核对，失败不自动使用规则文本。
 */

/** 输入元素 ID，输出真实 DOM；缺失模板元素由调用位置暴露错误。 */
const pdfEl = (id) => document.getElementById(id);
let pdfController = null;
let pdfTimer = null;
let pdfStarted = 0;
let pdfReviewed = false;
const PDF_FALLBACK = {
  pdf_selected: "文件已选择，点击后将依次执行传统提取和视觉校对。", pdf_cancelled: "已取消，尚未完成视觉校对。", pdf_initial: "选择 PDF 后解析并校对；也可以直接粘贴文本。",
  pdf_done: "校对完成，可直接使用下方结果。", pdf_empty: "PDF 必须非空且不超过 10 MiB。", pdf_uploading: "正在上传 PDF…",
  pdf_extra_data: "完成事件后收到额外数据。", pdf_incomplete: "解析连接提前结束，结果未完成。", pdf_failed: "解析失败：{message}（未自动采用文本）",
  pdf_extracting: "正在隔离环境中提取 PDF 文本并渲染页面", pdf_review_progress: "视觉校对：已完成 {done}/{total} 页", pdf_uncertainty: "第 {page} 页待核对：{notes}",
  pdf_used: "已填入简历文本，可继续编辑、提前解析或开始面试。",
};
/** 返回 PDF 状态文案；独立客户端测试上下文没有 i18n 模块时使用中文兼容回退。 */
const pdfText = (key, values = {}) => {
  const template = window.AppI18n?.t(key, values) ?? PDF_FALLBACK[key] ?? key;
  return template.replace(/\{(\w+)\}/g, /** 输入匹配文本和参数名，返回文本插值，不解释 HTML。 */ (_, name) => String(values[name] ?? `{${name}}`));
};

/** 读取文件、请求和面试输入状态；只管理 PDF 控件，不改变既有面试预算。 */
function pdfControls() {
  const busy = pdfController !== null;
  const locked = pdfEl("resume").disabled;
  const selected = pdfEl("pdf-file").files.length > 0;
  pdfEl("pdf-file").disabled = busy || locked;
  pdfEl("pdf-parse").disabled = busy || locked || !selected;
  pdfEl("pdf-cancel").disabled = !busy;
  pdfEl("pdf-preview").disabled = busy || !pdfReviewed;
  pdfEl("pdf-use").disabled = busy || locked || !pdfReviewed || !pdfEl("pdf-preview").value.trim();
}

/** 读取单调时钟，以秒显示实际等待；无网络或预算副作用。 */
function pdfClock() {
  pdfEl("pdf-time").textContent = `已等待 ${Math.floor((performance.now() - pdfStarted) / 1000)} 秒`;
}

/** 文件变化时取消接收并清空旧证据，防止新文件误用旧文本；不修改面试简历。 */
function pdfReset() {
  pdfCancel();
  pdfReviewed = false;
  pdfEl("pdf-preview").value = "";
  pdfEl("pdf-notes").textContent = "";
  pdfEl("pdf-panel").hidden = true;
  pdfEl("pdf-status").textContent = pdfText("pdf_selected");
  pdfEl("pdf-time").textContent = "";
  pdfControls();
}

/** 取消当前 fetch 并清理计时；已接收结果保留，已发供应商请求不保证停止计费。 */
function pdfCancel() {
  if (pdfController) {
    pdfController.abort();
    pdfController = null;
    pdfReviewed = false;
    pdfEl("pdf-status").textContent = pdfText("pdf_cancelled");
  }
  if (pdfTimer !== null) clearInterval(pdfTimer);
  pdfTimer = null;
  pdfControls();
}

/** 输入服务端事件，更新本次预览并返回是否成功终态；error 抛错且不伪装完成。 */
function pdfEvent(data) {
  if (data.type === "error") throw new Error(data.detail);
  if (data.type === "progress") {
    const extracting = data.detail === "正在隔离环境中提取 PDF 文本并渲染页面";
    const review = /^视觉校对：已完成 (\d+)\/(\d+) 页$/.exec(data.detail || "");
    pdfEl("pdf-status").textContent = extracting ? pdfText("pdf_extracting")
      : review ? pdfText("pdf_review_progress", { done: review[1], total: review[2] }) : data.detail;
  }
  else if (data.type === "result") {
    pdfEl("pdf-preview").value = data.text;
    pdfEl("pdf-panel").hidden = false;
    const notes = [];
    for (const page of data.pages) {
      if (page.uncertainties.length) notes.push(pdfText("pdf_uncertainty", { page: page.number, notes: page.uncertainties.join("；") }));
    }
    pdfEl("pdf-notes").textContent = notes.join("\n");
    pdfEl("pdf-status").textContent = pdfText("pdf_done");
    return true;
  }
  return false;
}

/** 读取文件与页面 CSRF token；单次上传依次提取和校对，完整成功后允许采用，失败不重发。 */
async function pdfRun() {
  if (pdfController || pdfEl("resume").disabled) return;
  const file = pdfEl("pdf-file").files[0];
  if (!file) return;
  if (!file.size || file.size > 10 * 1024 * 1024) {
    pdfEl("pdf-status").textContent = pdfText("pdf_empty");
    return;
  }
  pdfReset();
  const controller = new AbortController();
  pdfController = controller;
  pdfStarted = performance.now();
  pdfTimer = setInterval(pdfClock, 1000);
  pdfEl("pdf-status").textContent = pdfText("pdf_uploading");
  pdfControls();
  const body = new FormData();
  body.append("file", file);
  let reader = null;
  try {
    const response = await fetch("/api/resume/parse/", { method: "POST", body, signal: controller.signal, headers: { "X-CSRFToken": pdfEl("csrf-token").content } });
    if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (pdfController !== controller) return;
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line.trim()) {
          if (completed) throw new Error(pdfText("pdf_extra_data"));
          completed = pdfEvent(JSON.parse(line));
        }
      }
      if (done) break;
    }
    if (!completed || buffer.trim()) throw new Error(pdfText("pdf_incomplete"));
    pdfReviewed = true;
  } catch (error) {
    if (pdfController === controller) pdfEl("pdf-status").textContent = pdfText("pdf_failed", { message: error.message });
  } finally {
    if (reader) {
      await reader.cancel().catch(pdfCancelReadError);
      reader.releaseLock();
    }
    if (pdfController === controller) {
      pdfClock();
      pdfController = null;
      clearInterval(pdfTimer);
      pdfTimer = null;
      pdfControls();
    }
  }
}

/** 读取流已取消时 cancel 也可能拒绝；只记录清理阶段，原请求错误已在 UI 展示。 */
function pdfCancelReadError(error) { console.debug("PDF reader cleanup:", error.name); }
/** 人工点击后使用预览文本；重新检查状态防止异步完成后覆盖正在面试的简历。 */
function pdfUse() {
  if (pdfController || !pdfReviewed || pdfEl("resume").disabled || !pdfEl("pdf-preview").value.trim()) return;
  pdfEl("resume").value = pdfEl("pdf-preview").value;
  pdfEl("resume").dispatchEvent(new Event("input", { bubbles: true }));
  pdfEl("pdf-status").textContent = pdfText("pdf_used");
  pdfEl("resume").focus();
}
/** 清空动作同时删除文件选择与解析证据；取消在途操作以免旧结果重新出现。 */
function pdfClear() {
  pdfReset();
  pdfEl("pdf-file").value = "";
  pdfEl("pdf-status").textContent = pdfText("pdf_initial");
  pdfControls();
}

const pdfObserver = new MutationObserver(pdfControls);
pdfObserver.observe(pdfEl("resume"), { attributes: true, attributeFilter: ["disabled"] });
pdfEl("pdf-file").addEventListener("change", pdfReset);
pdfEl("pdf-parse").addEventListener("click", pdfRun);
pdfEl("pdf-cancel").addEventListener("click", pdfCancel);
pdfEl("pdf-use").addEventListener("click", pdfUse);
pdfEl("pdf-preview").addEventListener("input", pdfControls);
pdfEl("clear-agent").addEventListener("click", pdfClear);
window.addEventListener("pagehide", pdfCancel);
pdfControls();
