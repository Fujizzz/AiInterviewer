/**
 * @module interview-history
 * 职责：个人中心的本人面试分页列表与只读复盘弹窗。
 * 实现：请求序号隔离迟到结果；安全 DOM 展示已检题目、本人回答、评价、计划与报告。
 * 关联：resumes.html、interview-history.css、只读 /api/agent-interviews/；不恢复面试或调用模型。
 * 目录：
 * - historyNode：读取模板节点。
 * - historyElement：创建纯文本节点。
 * - historyStatus：生命周期本地化。
 * - historyFetch：读取只读同源接口。
 * - renderHistoryList：绘制列表与分页状态。
 * - loadHistory：读取指定页并隔离迟到请求。
 * - historyParagraph：追加纯文本段落。
 * - historySection：追加标题与 section。
 * - renderHistoryDetail：绘制报告、对话及获准诊断。
 * - openHistory：打开弹窗并读取本人详情。
 * - closeHistory：清空详情并恢复焦点。
 * - historyPrevious：显式读取上一页。
 * - historyNext：显式读取下一页。
 * - historyRefresh：刷新列表或关闭详情。
 * - historyLanguage：重绘内存文案。
 * - historyView：使用本人列表 UUID 打开详情。
 * 关键变量：
 * - historyPage：当前页。
 * - historyListing：列表内存快照。
 * - historyDetail：详情内存快照。
 * - historyListRequest：列表请求序号，拒绝迟到结果。
 * - historyDetailRequest：详情请求序号，关闭或新请求后拒绝迟到结果。
 * - historyInvoker：详情关闭后恢复焦点的按钮。
 * - historyBusy：列表加载锁，禁用分页。
 * 约束：
 * 不在浏览器持久化资料，不将旧记录缺失内容从内部上下文补齐；失败要求显式刷新。
 */
import { reviewText, reviewTopics } from "./interview-progress.js";
let historyPage = 1;
let historyListing = null;
let historyDetail = null;
let historyListRequest = 0;
let historyDetailRequest = 0;
let historyInvoker = null;
let historyBusy = false;
/** 输入模板 ID 返回节点；缺失模板在调用处失败，不静默隐藏功能。 */
function historyNode(id) { return document.getElementById(id); }
/** 输入标签与可选正文，创建纯文本节点；模型内容不会进入 HTML 解析器。 */
function historyElement(tag, text = "") { const node = document.createElement(tag); node.textContent = text; return node; }
/** 输入持久化生命周期状态，返回独立本地化标签；未知状态按原始代码呈现。 */
function historyStatus(status) { return reviewText({ completed: "review_completed", active: "review_active", failed: "failed_status" }[status] ?? status); }
/** 输入同源历史 URL，输出 JSON；HTTP/认证失败原样抛出，不改用其他数据源。 */
async function historyFetch(url) { const response = await fetch(url, { credentials: "same-origin", cache: "no-store" }); if (!response.ok) { const error = new Error(`HTTP ${response.status}`); error.status = response.status; throw error; } return response.json(); }
/** 读取内存页绘制安全卡片和分页状态；只读元数据，不请求详情或自动翻页。 */
function renderHistoryList() {
  historyNode("history-refresh").textContent = reviewText("refresh");
  historyNode("history-previous").textContent = reviewText("previous"); historyNode("history-next").textContent = reviewText("next");
  historyNode("history-previous").disabled = historyBusy || historyPage <= 1;
  historyNode("history-next").disabled = historyBusy || !historyListing?.next;
  historyNode("history-refresh").disabled = historyBusy;
  const list = historyNode("history-list"); list.setAttribute("aria-busy", String(historyBusy)); list.replaceChildren();
  if (!historyListing) return;
  historyNode("history-page").textContent = `${historyPage} · ${historyListing.count}`;
  for (const interview of historyListing.results) {
    const card = historyElement("article"); card.className = "history-card";
    const copy = historyElement("div"); copy.append(historyElement("h3", interview.job_title || reviewText("unknown")), historyElement("p", `${new Date(interview.created_at).toLocaleString()} · ${historyStatus(interview.status)}`));
    const button = historyElement("button", reviewText("detail")); button.type = "button"; button.dataset.interviewId = interview.id; button.addEventListener("click", historyView);
    card.append(copy, button); list.append(card);
  }
}
/** 无外部参数；读取当前页并隔离迟到响应，错误显示固定提示和 HTTP 诊断，不记录正文。 */
async function loadHistory() {
  const request = ++historyListRequest; historyBusy = true; historyListing = null;
  historyNode("history-status").textContent = reviewText("loading"); renderHistoryList();
  try {
    const result = await historyFetch(`/api/agent-interviews/?page=${historyPage}`);
    if (request !== historyListRequest) return;
    historyListing = result; historyNode("history-status").textContent = result.results.length ? "" : reviewText("empty");
  } catch (error) { if (request === historyListRequest) historyNode("history-status").textContent = reviewText("failed"); console.error("Interview history list failed", { page: historyPage, error: error.name, httpStatus: error.status ?? null }); }
  finally { if (request === historyListRequest) { historyBusy = false; renderHistoryList(); } }
}
/** 输入容器、文字和可选类；追加纯文本段落，支持保留模型换行。 */
function historyParagraph(parent, text, className = "") { const p = historyElement("p", text); p.className = className; parent.append(p); }
/** 输入父容器和标题，追加语义 section/h3 并返回新容器，不使用模板字符串 HTML。 */
function historySection(parent, title) { const section = historyElement("section"); section.append(historyElement("h3", title)); parent.append(section); return section; }
/** 输入 v2 或旧版获准详情，绘制报告、题目难度/追问、回答/评价、话题与原始获准诊断。
 * 未完成、无获准输出和未评价答案分别呈现；无评分重新计算，无未经检查的 context 读取。
 */
function renderHistoryDetail(detail) {
  const body = historyNode("history-detail-body"); body.replaceChildren();
  historyParagraph(body, `${detail.job_title} · ${historyStatus(detail.status)} · ${new Date(detail.created_at).toLocaleString()}`, "history-meta");
  if (detail.status !== "completed") historyParagraph(body, reviewText("incomplete"), "history-notice");
  if (!detail.security_output_available) historyParagraph(body, reviewText("unavailable"), "history-notice");
  for (const issue of detail.request_issues ?? []) historyParagraph(body, reviewText("request_issue", { code: issue.error_code || issue.kind, status: historyStatus(issue.status) }), "history-notice");
  if (detail.processing?.status === "running") historyParagraph(body, `${reviewText("pending")} · ${detail.processing.kind}`, "history-notice");
  const logs = detail.decision_logs ?? [];
  let fallback = false;
  const sources = new Set();
  for (const log of logs) {
    fallback ||= log.fallback_used;
    for (const source of [...(log.failed_retrieval_sources ?? []), ...(log.timeout_retrieval_sources ?? [])]) sources.add(source);
  }
  if (fallback) historyParagraph(body, reviewText("fallback"), "history-notice");
  if (sources.size) historyParagraph(body, reviewText("retrieval", { sources: [...sources].join(", ") }), "history-notice");
  const report = detail.final_report;
  if (report) {
    const summary = historySection(body, reviewText("summary"));
    historyParagraph(summary, `${reviewText("score")}: ${report.overall_score === null ? reviewText("unknown") : report.overall_score} / 5`, "history-score");
    if (detail.report_narrative_status === "fallback") historyParagraph(summary, reviewText("narrative"), "history-notice");
    historyParagraph(summary, report.summary);
    for (const key of ["strengths", "weaknesses"]) { const section = historySection(summary, reviewText(key)); const list = historyElement("ul"); for (const text of report[key] ?? []) list.append(historyElement("li", text)); section.append(list); }
  }
  const topics = reviewTopics(detail);
  if (topics) {
    const section = historySection(body, reviewText("topics"));
    historyParagraph(section, `${reviewText(detail.interview_state?.stage ?? "unknown")} · ${reviewText("ceiling", topics)}`);
    const list = historyElement("ul");
    for (const row of topics.rows) list.append(historyElement("li", `${reviewText(row.status)} · ${row.title} · ${row.count === null ? reviewText("unknown") : reviewText("asked", row)}`));
    section.append(list);
  }
  const conversation = historySection(body, reviewText("dialogue"));
  for (const turn of detail.questions ?? []) {
    const q = turn.question;
    const article = historyElement("article"); article.className = "history-turn";
    article.append(historyElement("h4", `#${turn.ordinal} · ${q.topic ?? reviewText("unknown")}`));
    historyParagraph(article, `${reviewText("difficulty")} ${q.difficulty}/5 · ${reviewText("depth")} ${q.probe_depth} · ${q.dialogue_action} · ${q.question_type}`, "history-meta");
    historyParagraph(article, q.text, "history-question");
    article.append(historyElement("h5", reviewText("answer"))); historyParagraph(article, turn.answer?.text ?? reviewText("unanswered"));
    const evaluation = turn.answer?.evaluation;
    if (!evaluation) historyParagraph(article, reviewText("unevaluated"), "history-meta");
    else {
      historyParagraph(article, `${reviewText("relevance")}: ${evaluation.answer_relevance} · ${reviewText("evidence")}: ${evaluation.evidence_strength}`, "history-meta");
      if (evaluation.analysis?.summary) historyParagraph(article, evaluation.analysis.summary);
      for (const [key, title] of [["missing_information", "missing"], ["contradictions", "contradictions"], ["uncertainties", "uncertainties"]]) {
        if (evaluation.analysis?.[key]?.length) historyParagraph(article, `${reviewText(title)}: ${evaluation.analysis[key].join("; ")}`);
      }
      for (const dimension of evaluation.dimensions ?? []) historyParagraph(article, `${dimension.competency} · ${reviewText("difficulty")} ${dimension.rubric_level ?? reviewText("unknown")} / 5 · ${dimension.observation} · ${dimension.rationale}\n${dimension.fact}\n“${dimension.quote}”`);
    }
    conversation.append(article);
  }
  const diagnostics = historyElement("details"); diagnostics.append(historyElement("summary", reviewText("diagnostics")));
  diagnostics.append(historyElement("pre", JSON.stringify({ interview_state: detail.interview_state, interview_plan: detail.interview_plan, plan_history: detail.plan_history, topic_progress: detail.topic_progress, decision_logs: detail.decision_logs, competencies: report?.competencies }, null, 2))); body.append(diagnostics);
}
/** 输入本人列表的 UUID 和调用按钮；打开只读弹窗并加载详情，关闭/新请求后拒绝迟到结果。 */
async function openHistory(id, invoker) {
  const request = ++historyDetailRequest; historyDetail = null; historyInvoker = invoker;
  historyNode("history-detail-body").replaceChildren(historyElement("p", reviewText("loading")));
  if (!historyNode("history-dialog").open) historyNode("history-dialog").showModal();
  historyNode("history-close").focus();
  try {
    const result = await historyFetch(`/api/agent-interviews/${encodeURIComponent(id)}/`);
    if (request !== historyDetailRequest || !historyNode("history-dialog").open) return;
    historyDetail = result; renderHistoryDetail(result);
  } catch (error) { if (request === historyDetailRequest) historyNode("history-detail-body").replaceChildren(historyElement("p", reviewText("failed"))); console.error("Interview history detail failed", { error: error.name, httpStatus: error.status ?? null }); }
}
/** 关闭时清除详情内存与正文、使迟到请求失效并恢复调用按钮焦点；不删除数据库记录。 */
function closeHistory() { historyDetailRequest++; historyDetail = null; historyNode("history-detail-body").replaceChildren(); historyInvoker?.focus(); historyInvoker = null; }
/** 输入按钮事件；只读取其列表 UUID，不从 URL 推断其他用户内容。 */
function historyView(event) { openHistory(event.currentTarget.dataset.interviewId, event.currentTarget); }
/** 无外部参数；空闲且非首页时明确翻到上一页，无自动重试。 */
function historyPrevious() { if (!historyBusy && historyPage > 1) { historyPage--; loadHistory(); } }
/** 无外部参数；仅在服务端声明下一页且空闲时翻页。 */
function historyNext() { if (!historyBusy && historyListing?.next) { historyPage++; loadHistory(); } }
/** 输入刷新/关闭按钮事件；关闭详情或显式刷新列表，不恢复 Agent 连接。 */
function historyRefresh(event) { if (event.currentTarget.id === "history-close") historyNode("history-dialog").close(); else loadHistory(); }
/** 无外部参数；重绘内存快照文案，保持页码和正文，不触发网络或模型调用。 */
function historyLanguage() { renderHistoryList(); if (historyDetail) renderHistoryDetail(historyDetail); }
historyNode("history-refresh").addEventListener("click", historyRefresh);
historyNode("history-close").addEventListener("click", historyRefresh);
historyNode("history-previous").addEventListener("click", historyPrevious);
historyNode("history-next").addEventListener("click", historyNext);
historyNode("history-dialog").addEventListener("close", closeHistory);
for (const picker of document.querySelectorAll("[data-language-selector]")) picker.addEventListener("change", historyLanguage);
window.addEventListener("languagechange", historyLanguage);
loadHistory();
