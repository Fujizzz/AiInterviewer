/**
 * @module interview-history
 * Responsibilities: Paginated list of personal interviews and read-only review popup for standalone review page.
 * Implementation: Isolate late-arriving results by request sequence number; safely render inspected questions, personal answers, evaluations, plans, and reports.
 * Related Modules: interview-review.html, interview-history.css, read-only /api/agent-interviews/; does not resume interviews or invoke models.
 * Declaration Index:
 * - historyNode: reads template node.
 * - historyElement: creates plain text node.
 * - historyStatus: localizes lifecycle state.
 * - historyFetch: reads read-only same-origin API.
 * - renderHistoryList: renders list and pagination state.
 * - loadHistory: reads specified page and isolates late requests.
 * - historyParagraph: appends plain text paragraph.
 * - historySection: appends heading and section.
 * - renderHistoryDetail: renders report, conversation, and approved diagnosis.
 * - openHistory: opens popup and loads personal detail.
 * - closeHistory: clears detail and restores focus.
 * - historyPrevious: explicitly loads previous page.
 * - historyNext: explicitly loads next page.
 * - historyRefresh: refreshes list or closes detail.
 * - historyLanguage: redraws in-memory text.
 * - historyView: opens detail using personal list UUID.
 * Variable Index:
 * - historyPage: current page.
 * - historyListing: in-memory snapshot of list.
 * - historyDetail: in-memory snapshot of detail.
 * - historyListRequest: request sequence number for list, rejects late results.
 * - historyDetailRequest: request sequence number for detail, rejects late results after close or new request.
 * - historyInvoker: button that restores focus after detail closes.
 * - historyBusy: list loading lock, disables pagination.
 * Constraints:
 * No browser persistence of data; do not fill missing content from internal context in old records; failure requires explicit refresh.
 *
 */
import { renderEvaluationReport } from "./evaluation-report.js";
import { pollEvaluationReport } from "./evaluation-poll.js";
import { reviewText, reviewTopics } from "./interview-progress.js";
let historyPage = 1;
let historyListing = null;
let historyDetail = null;
let historyListRequest = 0;
let historyDetailRequest = 0;
let historyInvoker = null;
let historyBusy = false;
let cancelHistoryPolling = null;
/**
 * Returns node given input template ID; fails at call site if template is missing, does not silently hide functionality.
 */
function historyNode(id) { return document.getElementById(id); }
/**
 * Given a tag and optional body text, creates a plain text node; model content will not enter HTML parser.
 */
function historyElement(tag, text = "") { const node = document.createElement(tag); node.textContent = text; return node; }
/**
 * Given persisted lifecycle state, returns localized label; unknown states rendered as original code.
 */
function historyStatus(status) { return reviewText({ completed: "review_completed", active: "review_active", failed: "failed_status" }[status] ?? status); }
/**
 * Given same-origin history URL, outputs JSON; HTTP/auth failure thrown verbatim, no fallback data source used.
 */
async function historyFetch(url) { const response = await fetch(url, { credentials: "same-origin", cache: "no-store" }); if (!response.ok) { const error = new Error(`HTTP ${response.status}`); error.status = response.status; throw error; } return response.json(); }
/**
 * Reads in-memory page to render safe cards and pagination status; read-only metadata, no detail request or auto-pagination.
 */
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
/**
 * No external parameters; reads current page and isolates late responses, displays fixed error message and HTTP diagnostics, does not log body content.
 */
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
/**
 * Given container, text, and optional class; appends plain text paragraph, supports preserving model line breaks.
 */
function historyParagraph(parent, text, className = "") { const p = historyElement("p", text); p.className = className; parent.append(p); }
/**
 * Given parent container and title, appends semantic section/h3 and returns new container, does not use template string HTML.
 */
function historySection(parent, title) { const section = historyElement("section"); section.append(historyElement("h3", title)); parent.append(section); return section; }

function historyLocal(zh, en) { return window.AppI18n?.language() === "en" ? en : zh; }
function historyChip(text, kind = "neutral") {
  const chip = historyElement("span", text); chip.className = `ht-chip ht-${kind}`; return chip;
}
/** A visual scale preserves the metric's unit and leaves unknown values unfilled. */
function historyMetric(title, value, maximum) {
  const item = historyElement("div"); item.className = "ht-metric";
  const known = Number.isFinite(value) && value >= 0 && value <= maximum;
  const heading = historyElement("div"); heading.className = "ht-metric-heading";
  heading.append(historyElement("span", title), historyElement("strong", known ? `${value.toFixed(2)} / ${maximum}` : "—"));
  item.append(heading);
  if (known) {
    const meter = historyElement("meter"); meter.setAttribute("min", "0"); meter.setAttribute("max", String(maximum));
    meter.setAttribute("value", String(value)); meter.setAttribute("aria-label", title); item.append(meter);
  }
  return item;
}

function historyDimension(dimension) {
  const names = {
    technical_depth: ["技术深度", "Technical depth"], ownership: ["个人贡献", "Ownership"],
    decision_making: ["决策与权衡", "Decision making"], debugging: ["问题排查", "Debugging"],
    evaluation: ["评估与验证", "Evaluation"], adaptability: ["适应与复盘", "Adaptability"],
  };
  const observations = {
    supported: ["有证据支持", "Supported"], weak: ["证据有限", "Limited evidence"],
    contradicted: ["存在矛盾", "Conflicting evidence"], not_observed: ["尚未观察到", "Not observed"],
  };
  const card = historyElement("div"); card.className = "ht-dimension";
  const heading = historyElement("div"); heading.className = "ht-dimension-heading";
  heading.append(historyElement("h6", names[dimension.competency] ? historyLocal(...names[dimension.competency]) : dimension.competency));
  const observation = observations[dimension.observation];
  heading.append(historyChip(observation ? historyLocal(...observation) : historyLocal("未记录", "Not recorded"), dimension.observation === "supported" ? "supported" : "limited"));
  card.append(heading);
  const level = Number.isInteger(dimension.rubric_level) && dimension.rubric_level >= 1 && dimension.rubric_level <= 5 ? dimension.rubric_level : null;
  const scale = historyElement("div"); scale.className = "ht-level";
  const marks = historyElement("span"); marks.className = "ht-level-marks"; marks.setAttribute("aria-hidden", "true");
  for (let i = 1; i <= 5; i++) { const mark = historyElement("i"); mark.className = level !== null && i <= level ? "is-filled" : ""; marks.append(mark); }
  scale.append(marks, historyElement("span", `${historyLocal("观察等级", "Observation level")} ${level ?? "—"} / 5`)); card.append(scale);
  if (dimension.fact) historyParagraph(card, dimension.fact, "ht-fact");
  if (dimension.rationale || dimension.quote) {
    const details = historyElement("details"); details.className = "ht-evidence";
    details.append(historyElement("summary", historyLocal("查看分析与回答原文", "Why this assessment?")));
    if (dimension.rationale) {
      details.append(historyElement("h6", historyLocal("判断依据", "Assessment rationale")));
      historyParagraph(details, dimension.rationale);
    }
    if (dimension.quote) {
      details.append(historyElement("h6", historyLocal("回答原文", "Your words")), historyElement("blockquote", dimension.quote));
    }
    card.append(details);
  }
  return card;
}

function historyTurn(turn) {
  const q = turn.question;
  const article = historyElement("article"); article.className = "history-turn";
  const heading = historyElement("header"); heading.className = "ht-heading";
  const number = historyElement("span", String(turn.ordinal).padStart(2, "0")); number.className = "ht-number";
  const title = historyElement("div"); title.append(historyElement("h4", q.topic ?? reviewText("unknown")));
  const tags = historyElement("div"); tags.className = "ht-tags";
  tags.append(historyChip(`${reviewText("difficulty")} ${q.difficulty ?? "—"}/5`),
    historyChip(q.parent_question_id || q.dialogue_action === "probe" ? historyLocal("追问", "Follow-up") : historyLocal("主题问题", "Topic question"), "accent"),
    historyChip(`${reviewText("depth")} ${q.probe_depth ?? "—"}`));
  title.append(tags); heading.append(number, title); article.append(heading);
  const question = historyElement("div"); question.className = "ht-question-block";
  question.append(historyElement("h5", historyLocal("面试问题", "Interview question")));
  historyParagraph(question, q.text, "history-question"); article.append(question);
  const answer = historyElement("details"); answer.className = "ht-answer";
  const answerHeading = historyElement("summary", reviewText("answer"));
  answerHeading.append(historyChip(turn.answer?.text ? historyLocal("已回答", "Answered") : historyLocal("尚未回答", "Not answered"), turn.answer?.text ? "supported" : "limited"));
  answer.append(answerHeading); historyParagraph(answer, turn.answer?.text ?? reviewText("unanswered"));
  article.append(answer);
  const evaluation = turn.answer?.evaluation;
  if (!evaluation) {
    historyParagraph(article, reviewText(turn.answer?.unscored_reason === "self_introduction" ? "intro_unscored" : "unevaluated"), "history-notice");
    return article;
  }
  const assessed = !["pending", "unavailable"].includes(evaluation.assessment_status);
  const signals = historyElement("div"); signals.className = "ht-signals";
  signals.append(historyMetric(reviewText("relevance"), evaluation.answer_relevance, 1));
  if (assessed) signals.append(historyMetric(reviewText("evidence"), evaluation.evidence_strength, 1));
  else historyParagraph(signals, reviewText(evaluation.assessment_status === "pending" ? "assessment_pending" : "assessment_unavailable"), "history-notice");
  article.append(signals);
  if (evaluation.analysis?.summary) {
    const summary = historyElement("div"); summary.className = "ht-takeaway";
    summary.append(historyElement("h5", historyLocal("本题小结", "Answer takeaway")));
    historyParagraph(summary, evaluation.analysis.summary); article.append(summary);
  }
  const dimensions = assessed ? evaluation.dimensions ?? [] : [];
  if (dimensions.length) {
    const observations = historyElement("div"); observations.className = "ht-observations";
    observations.append(historyElement("h5", historyLocal("本题能力观察", "Competency observations")));
    historyParagraph(observations, historyLocal("以下为本题的观察等级，不等同于最终能力得分。", "These are observations from this answer, not final competency scores."), "history-meta");
    const grid = historyElement("div"); grid.className = "ht-dimension-grid";
    for (const dimension of dimensions) grid.append(historyDimension(dimension));
    observations.append(grid); article.append(observations);
  }
  const notes = historyElement("div"); notes.className = "ht-notes";
  for (const [key, title] of [["missing_information", "missing"], ["contradictions", "contradictions"], ["uncertainties", "uncertainties"]]) {
    if (!evaluation.analysis?.[key]?.length) continue;
    const note = historyElement("div"); note.className = `ht-note ht-note-${key}`;
    note.append(historyElement("h5", reviewText(title)));
    const list = historyElement("ul");
    for (const item of evaluation.analysis[key]) list.append(historyElement("li", item));
    note.append(list); notes.append(note);
  }
  if (notes.children.length) article.append(notes);
  return article;
}
/**
 * Given v2 or legacy approved detail, renders report, question difficulty/ follow-up, answer/evaluation, topics, and raw approved diagnosis.
 * Unfinished, no approval output, and ungraded answers are displayed separately; no score recalculated, no unverified context read.
 *
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
    if (detail.report_narrative_status === "fallback") historyParagraph(summary, reviewText("narrative"), "history-notice");
    const reportBody = historyElement("div");
    renderEvaluationReport(reportBody, report);
    summary.append(reportBody);
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
  const turns = detail.questions ?? [];
  const totals = historyElement("div"); totals.className = "ht-conversation-stats";
  for (const [label, value] of [[historyLocal("问题", "Questions"), turns.length],
    [historyLocal("已回答", "Answered"), turns.filter(turn => turn.answer?.text?.trim()).length],
    [historyLocal("追问", "Follow-ups"), turns.filter(turn => turn.question.parent_question_id || turn.question.dialogue_action === "probe").length]]) {
    const item = historyElement("div"); item.append(historyElement("strong", String(value)), historyElement("span", label)); totals.append(item);
  }
  conversation.append(totals);
  for (const turn of turns) conversation.append(historyTurn(turn));
  const diagnostics = historyElement("details"); diagnostics.append(historyElement("summary", reviewText("diagnostics")));
  diagnostics.append(historyElement("pre", JSON.stringify({ interview_state: detail.interview_state, interview_plan: detail.interview_plan, plan_history: detail.plan_history, topic_progress: detail.topic_progress, decision_logs: detail.decision_logs, competencies: report?.competencies }, null, 2))); body.append(diagnostics);
}
/**
 * Given personal list UUID and invoking button; opens read-only popup and loads detail, rejects late results after close or new request.
 */
async function openHistory(id, invoker) {
  cancelHistoryPolling?.(); cancelHistoryPolling = null;
  const request = ++historyDetailRequest; historyDetail = null; historyInvoker = invoker;
  historyNode("history-detail-body").replaceChildren(historyElement("p", reviewText("loading")));
  if (!historyNode("history-dialog").open) historyNode("history-dialog").showModal();
  historyNode("history-close").focus();
  try {
    const result = await historyFetch(`/api/agent-interviews/${encodeURIComponent(id)}/`);
    if (request !== historyDetailRequest || !historyNode("history-dialog").open) return;
    historyDetail = result; renderHistoryDetail(result);
    if (["pending", "running", "idle"].includes(result.evaluation_status)) {
      historyParagraph(historyNode("history-detail-body"), "评分仍在后台进行，报告将自动更新。", "history-notice");
      cancelHistoryPolling = pollEvaluationReport({ interviewId: id,
        onUpdate(detail) { if (request === historyDetailRequest) { historyDetail = detail; renderHistoryDetail(detail); } },
        onError() { if (request === historyDetailRequest) historyParagraph(historyNode("history-detail-body"), "请稍后重新打开历史记录查看报告。", "history-notice"); },
      });
    }
  } catch (error) { if (request === historyDetailRequest) historyNode("history-detail-body").replaceChildren(historyElement("p", reviewText("failed"))); console.error("Interview history detail failed", { error: error.name, httpStatus: error.status ?? null }); }
}
/**
 * On close, clears detail memory and body, invalidates late requests, and restores focus to invoking button; does not delete database records.
 */
function closeHistory() { cancelHistoryPolling?.(); cancelHistoryPolling = null; historyDetailRequest++; historyDetail = null; historyNode("history-detail-body").replaceChildren(); historyInvoker?.focus(); historyInvoker = null; }
/**
 * Given button event; only reads its list UUID, does not infer other user content from URL.
 */
function historyView(event) { openHistory(event.currentTarget.dataset.interviewId, event.currentTarget); }
/**
 * No external parameters; explicitly navigates to previous page when idle and not on first page, no automatic retry.
 */
function historyPrevious() { if (!historyBusy && historyPage > 1) { historyPage--; loadHistory(); } }
/**
 * No external parameters; only advances to next page when server declares it exists and system is idle.
 */
function historyNext() { if (!historyBusy && historyListing?.next) { historyPage++; loadHistory(); } }
/**
 * Given refresh/close button event; closes detail or explicitly refreshes list, does not restore Agent connection.
 */
function historyRefresh(event) { if (event.currentTarget.id === "history-close") historyNode("history-dialog").close(); else loadHistory(); }
/**
 * No external parameters; redraws in-memory snapshot text, preserves page number and body, triggers no network or model call.
 */
function historyLanguage() { renderHistoryList(); if (historyDetail) renderHistoryDetail(historyDetail); }
historyNode("history-refresh").addEventListener("click", historyRefresh);
historyNode("history-close").addEventListener("click", historyRefresh);
historyNode("history-previous").addEventListener("click", historyPrevious);
historyNode("history-next").addEventListener("click", historyNext);
historyNode("history-dialog").addEventListener("close", closeHistory);
for (const picker of document.querySelectorAll("[data-language-selector]")) picker.addEventListener("change", historyLanguage);
window.addEventListener("languagechange", historyLanguage);
loadHistory();
