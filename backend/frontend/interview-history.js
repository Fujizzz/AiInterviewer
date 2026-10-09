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
import { reviewText, reviewTopics } from "./interview-progress.js";
let historyPage = 1;
let historyListing = null;
let historyDetail = null;
let historyListRequest = 0;
let historyDetailRequest = 0;
let historyInvoker = null;
let historyBusy = false;
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
      const assessment = evaluation.assessment_status === "pending" ? reviewText("assessment_pending")
        : evaluation.assessment_status === "unavailable" ? reviewText("assessment_unavailable")
          : `${reviewText("evidence")}: ${evaluation.evidence_strength}`;
      historyParagraph(article, `${reviewText("relevance")}: ${evaluation.answer_relevance} · ${assessment}`, "history-meta");
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
/**
 * Given personal list UUID and invoking button; opens read-only popup and loads detail, rejects late results after close or new request.
 */
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
/**
 * On close, clears detail memory and body, invalidates late requests, and restores focus to invoking button; does not delete database records.
 */
function closeHistory() { historyDetailRequest++; historyDetail = null; historyNode("history-detail-body").replaceChildren(); historyInvoker?.focus(); historyInvoker = null; }
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
