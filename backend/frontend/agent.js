/**
 * @module agent
 * Responsibilities: Run voice interviews with a ready resume selected from the personal center and display actual stages, timing, scores, and reports.
 * Implementation: Confirm preparation before connecting and sending start; submit final automatic answers using MCP when receipt-backed and ordinary answer/skip otherwise; explicit end evaluates/saves or discards after its acknowledgement.
 * Related Modules: agent.html, i18n.js, /api/resume-versions/, and /ws/agent/; resume maintenance is handled at /resumes/.
 * Declaration Index:
 * - el: Find a required page element by ID.
 * - uiText: Read and interpolate localized dynamic copy.
 * - uiText.callback1: Replace named parameters as plain text.
 * - controls: Update controls according to resume loading, request, and interview state.
 * - status: Display a plain-text status message.
 * - loadResumeVersions: Load the signed-in user's resume metadata and expose only ready versions; does not call a model.
 * - onResumeSelect: Validate a manual selection and clear the previous interview display.
 * - updateClock: Display elapsed request and stage times.
 * - beginWait: Start the timer for the current request.
 * - endWait: Stop the timer and retain the measured duration.
 * - clearResults: Clear previous questions, answers, report, and timing display.
 * - send: Enforce the message size limit and send one business command or MCP tools/call with a unique UUID.
 * - stop: Close interview/avatar connections and timers while retaining the displayed score and selected resume.
 * - displayAssessment: Render backend scores and competency states.
 * - onMessage: Complete MCP initialization and route business events and tool results by connection and UUID.
 * - onError: Stop the connection after an error.
 * - onClose: Stop timing after an unexpected disconnect; do not reconnect.
 * - dispatch: Create or reuse the connection, await hello and optional MCP handshake, then send one command.
 * - onStart: Connect the avatar and start an interview with the loaded ready-resume UUID and existing parameters.
 * - onOpenPreparation: Open the preparation dialog while idle, retain inputs, and focus resume selection without connecting.
 * - onClosePreparation: Close the dialog explicitly without clearing inputs or starting an interview.
 * - onPreparationClosed: Return focus to the entry control or the status region preparing the first question.
 * - onPreparationKeydown: Cycle Tab focus within the dialog; let the native dialog handle Escape.
 * - onPreparationLanguage: Redraw resume labels and known selection status without network access or selection changes.
 * - onAnswer: Submit the final transcript while the current question is idle; inactivity answers use answer, empty closure uses skip, and receipt-backed semantic completion uses MCP.
 * - onCancel: Suspend automatic answering and open evaluate/save versus discard choices.
 * - dispatchFinish: Functionality: Send the chosen finish after any already accepted request completes.
 * - onEndSave: Functionality: Select early evaluation and preserve the final current transcript if available.
 * - onEndDiscard: Functionality: End without evaluation and remove this session's history.
 * - onEndContinue: Functionality: Dismiss the end choice and restore automatic answering.
 * - onClear: Clear interview content while retaining resume and role settings.
 * - onPageHide: Release the connection, timers, recording, and avatar resources when leaving the page.
 * Variable Index:
 * - el: Required DOM element lookup helper.
 * - uiText: Localized dynamic-copy lookup and interpolation helper.
 * - STAGES: Maps backend stage names to localization keys.
 * - FALLBACK_TEXT: English copy used when a standalone client test omits i18n.js.
 * - socket: The sole active WebSocket.
 * - progress: Coordinator for validated budget/plan snapshots, frozen timing, and deduplicated warnings.
 * - voice: Avatar and voice interaction coordinator; it does not score interviews.
 * - questionId: Current question identifier.
 * - pendingId: Current request UUID.
 * - pendingCommand: The single command waiting for hello.
 * - terminal: Whether the current connection was ended intentionally.
 * - interviewActive: Whether an interview has started.
 * - resumesLoading: Resume-list request lock that prevents start before loading completes.
 * - availableResumes: Metadata for the signed-in user's ready versions; contains no resume body.
 * - messageLimit: Maximum message size announced by the server.
 * - waitStarted: Start time of the current request.
 * - stageStarted: Start time of the current stage.
 * - clockTimer: Display interval identifier.
 * - completedStages: Measured-duration labels for completed stages.
 * - preparationInvoker: Dialog entry button used to restore focus; stores no profile data.
 * - resumeSelectionMessage: Localization key for the selection message; unknown server errors retain their original text.
 * - mcpInitId: Current MCP initialization UUID; the pending start is sent only after handshake succeeds.
 * - ending: Explicit end-choice state and optional final transcript; gates automation and queued finish.
 * - mcpReady: Whether the connection completed MCP handshake; controls the automatic completion path.
 *
 * Constraints:
 * Does not upload or edit resumes or alter budgets/scores; has no resend, alternate-resume selection, or model-call fallback path.
 */
import { InterviewVoice } from "./interview-voice.js";
import { InterviewProgress } from "./interview-progress.js";

/**
 * Input template unique ID, return DOM node; missing template causes explicit failure at calling location.
 */
const el = (id) => document.getElementById(id);
const voice = new InterviewVoice(onAnswer);
const progress = new InterviewProgress();
let mcpInitId = null;
let mcpReady = false;
const STAGES = {
  resume_parsing: "agent_stage_resume", question_generation: "agent_stage_question", answer_evaluation: "agent_stage_evaluation",
  next_action: "agent_stage_next", report_generation: "agent_stage_report",
};
const FALLBACK_TEXT = {
  interview_discarded: "Interview ended. This interview was removed from history.",
  agent_resume_choice: "Select a saved resume",
  agent_resume_select: "Select a ready resume version",
  agent_manage_profile: "Manage profile and resumes in your personal center ↗",
  agent_resumes_loading: "Loading saved resumes…",
  agent_resumes_failed: "Could not load resumes. Click Refresh to check again.",
  agent_resume_unavailable: "The requested or previously selected resume is unavailable. Select a ready version.",
  agent_resumes_ready: "Select the version to use for this interview. Upload, extract and maintain information in your personal center.",
  agent_resumes_empty: "No ready resumes. Upload and finish extraction in your personal center.",
  agent_resume_required: "Select a ready resume and enter the target role.",
  agent_unexpected_close: "The connection closed unexpectedly. The current operation is incomplete and will not be resent automatically.",
  agent_message_limit: "The message exceeds the server limit. Shorten the answer or check your interview settings.",
  agent_stage_resume: "Parsing resume",
  agent_stage_question: "Generating first question",
  agent_stage_evaluation: "Evaluating answer",
  agent_question_placeholder: "Your question will appear here once the interview starts.",
  agent_stage_next: "Choosing next step and generating question",
  agent_stage_report: "Generating report text",
  agent_waiting: "Waiting {seconds} seconds",
  agent_waiting_stage: "Waiting {seconds} seconds · current stage {stage} seconds",
  agent_request_time: "Request took {seconds} seconds",
  agent_closed: "Connection closed. Please start again.",
  agent_report_incomplete: "The score is ready, but the full report is not complete.",
  agent_score_missing: "Not enough evidence to score",
  agent_score: "Overall score: {score} / 5",
  agent_request_received: "Request received; processing…",
  agent_question_answer: "Answer the current question",
  agent_submit: "Submitting answer…",
  agent_connecting: "Connecting to the backend…",
  agent_unknown_stage: "Unknown processing stage.",
  agent_unknown_response: "Received an unknown response.",
  agent_backend_old: "This backend does not support stage progress. Update the backend.",
  agent_mismatch: "Response does not match the current request.",
  report_generating: "The score is ready; report text is still being generated.",
  agent_finished: "Interview complete",
  agent_cancelled: "Interview cancelled; sent model requests may still finish.",
  agent_processing_failed: "Processing failed: {message}",
  agent_connection_failed: "Connection failed. Check the backend service.",
  agent_page_left: "Page left; connection ended.",
  agent_cleared: "Content cleared",
  agent_report_fallback: "The model report failed; the existing deterministic summary is shown. The score remains valid.\n",
  rm_auth_error: "Your session expired or the request was not authorized. Sign in again.",
  rm_unnamed: "Untitled text resume",
  rm_current: "Current resume",
};
/**
 * Return translated text; used for Chinese compatibility fallback when running standalone tests that load only this module.
 */
const uiText = (key, values = {}) => {
  const template = window.AppI18n?.t(key, values) ?? (FALLBACK_TEXT[key] ?? key);
  return template.replace(/\{(\w+)\}/g, /**
 * Input matching content and parameter name, return plain text replacement, do not parse HTML.
 */ (_, name) => String(values[name] ?? `{${name}}`));
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
let ending = null;

/**
 * Read request and interview state update disabled; protect version selection during version loading and session, no network side effects.
 */
function controls() {
  const busy = pendingId !== null || pendingCommand !== null;
  for (const id of ["open-preparation", "interview-settings"]) el(id).disabled = busy || interviewActive;
  el("start-agent").disabled = busy || interviewActive || resumesLoading || !el("resume-select").value;
  for (const id of ["resume-select", "refresh-resumes"]) el(id).disabled = busy || interviewActive || resumesLoading;
  for (const id of ["job", "duration", "limit", "probes"]) el(id).disabled = interviewActive;
  const answering = interviewActive && questionId !== null && !busy;
  el("cancel-agent").disabled = socket === null || ending !== null;
  el("clear-agent").disabled = socket !== null;
  window.dispatchEvent(new CustomEvent("interview-controls", { detail: { active: interviewActive, answering: answering && ending === null } }));
}
/**
 * Input status text, display using textContent, do not execute HTML, output is empty.
 */
function status(text) { el("agent-status").textContent = text; }
/**
 * Read own pagination metadata, return none; first use specifies ID or current, refresh maintains original selection.
 * Only provide ready versions; if specified or currently selected version disappears, explicitly require reselection, do not silently switch to another version, do not invoke model.
 * On failure, clear selection, record exception type, allow explicit refresh; do not send repeated requests if interview already started or loading.
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
/**
 * Manual selection updates only current interview input and clears old display, does not modify backend current version or invoke model.
 */
function onResumeSelect() { el("preparation-error").textContent = ""; clearResults(); controls(); }
/**
 * Read monotonic clock to update actual seconds; not used as condition for budget, timeout, completion, or retry.
 */
function updateClock() {
  if (waitStarted === null) return;
  const now = performance.now();
  const seconds = Math.floor((now - waitStarted) / 1000);
  const stageSeconds = stageStarted === null ? null : Math.floor((now - stageStarted) / 1000);
  el("wait-time").textContent = stageSeconds === null
    ? uiText("agent_waiting", { seconds })
    : uiText("agent_waiting_stage", { seconds, stage: stageSeconds });
}
/**
 * Reset display clock when starting new command; clear old interval first, no network request sent.
 */
function beginWait() {
  if (clockTimer !== null) clearInterval(clockTimer);
  waitStarted = performance.now();
  stageStarted = null;
  completedStages = [];
  el("stage-log").textContent = "";
  clockTimer = setInterval(updateClock, 1000);
  updateClock();
}
/**
 * Stop interval and display total request duration; do not clear previously returned questions or scores.
 */
function endWait() {
  if (clockTimer !== null) clearInterval(clockTimer);
  clockTimer = null;
  if (waitStarted !== null) el("wait-time").textContent = uiText("agent_request_time", { seconds: ((performance.now() - waitStarted) / 1000).toFixed(1) });
  waitStarted = null;
  stageStarted = null;
}
/**
 * Clear old Q&A, report, and time display, retain saved version selection and job, no network operation.
 */
function clearResults() {
  progress.reset();
  voice.reset();
  voice.setState("idle");
  questionId = null;
  el("question").textContent = uiText("agent_question_placeholder");
  for (const id of ["question-meta", "evaluation", "report", "score", "report-summary", "assessment", "stage-log", "wait-time"]) el(id).textContent = "";
  for (const id of ["evaluation-panel", "report-panel", "assessment-panel"]) el(id).hidden = true;
}
/**
 * Input business command or automatic end parameter, validate UTF-8 limit; send unique UUID-wrapped business or MCP package.
 * Model cannot provide tool name; fixed finish_current_answer carries final text/credentials, failures propagated unchanged.
 */
function send(command) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error(uiText("agent_closed"));
  const id = crypto.randomUUID();
  const text = JSON.stringify(command.type === "mcp_finish"
    ? { jsonrpc: "2.0", id, method: "tools/call", params: { name: "finish_current_answer",
      arguments: { question_id: command.question_id, answer_text: command.answer_text,
        completion_receipt: command.completion_receipt, progress_events: true } } }
    : { ...command, request_id: id, progress_events: true });
  if (messageLimit === null || new TextEncoder().encode(text).byteLength > messageLimit) throw new Error(uiText("agent_message_limit"));
  pendingId = id;
  socket.send(text);
  controls();
}
/**
 * Input terminal state prompt; close connection, pending commands, timing, and cache, retain displayed questions and numeric scores.
 */
function stop(message) {
  progress.freeze();
  voice.reset();
  voice.setState("idle");
  voice.disconnectAvatar();
  terminal = true;
  mcpInitId = null; mcpReady = false; voice.completionEnabled = false;
  const old = socket;
  socket = null;
  if (old) old.close();
  pendingId = null;
  pendingCommand = null;
  interviewActive = false;
  ending = null;
  el("end-interview-dialog").close();
  endWait();
  controls();
  if (!el("report-panel").hidden && el("report").textContent === "") {
    el("report-summary").textContent = uiText("agent_report_incomplete");
  }
  status(message);
}
/**
 * Input server-side numeric score; display total score and capability status, do not recalculate, do not mark report as complete.
 */
function displayAssessment(assessment) {
  el("report-panel").hidden = false;
  el("assessment-panel").hidden = false;
  el("score").textContent = assessment.overall_score === null ? uiText("agent_score_missing") : uiText("agent_score", { score: assessment.overall_score.toFixed(2) });
  el("assessment").textContent = JSON.stringify(assessment.competencies, null, 2);
}
/**
 * Input MessageEvent; validate connection/UUID, complete MCP handshake or unpack tool approval result, then deliver to original business branch.
 * MCP protocol/tool errors and unknown events explicitly stop processing, no retransmission; progress still uses original request UUID.
 */
function onMessage(event) {
  if (socket !== event.currentTarget) return;
  try {
    let message = JSON.parse(event.data);
    // Discard is the only control allowed to preempt an outstanding request. Ignore its
    // now-obsolete response; only a matching discard acknowledgement completes deletion.
    if (ending?.kind === "discard" && message.request_id !== pendingId) return;
    if (ending?.kind === "discard" && message.type !== "discarded" && message.type !== "error") return;
    if (message.jsonrpc === "2.0") {
      if (message.id === mcpInitId && mcpInitId !== null) {
        if (message.error || message.result?.protocolVersion !== "2025-06-18" || !message.result?.capabilities?.tools) throw new Error("MCP initialization failed.");
        mcpInitId = null; mcpReady = true; voice.completionEnabled = true;
        socket.send(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }));
        const command = pendingCommand; pendingCommand = null; send(command); return;
      }
      if (message.id !== pendingId) throw new Error(uiText("agent_mismatch"));
      if (message.error || message.result?.isError) throw new Error(message.error?.message ?? "MCP tool failed.");
      if (message.result?.structuredContent?.request_id !== message.id) throw new Error(uiText("agent_mismatch"));
      message = message.result.structuredContent;
    }
    if (message.type === "hello") {
      if (!pendingCommand || !message.capabilities?.includes("progress")) throw new Error(uiText("agent_backend_old"));
      messageLimit = message.max_message_bytes;
      if (message.capabilities.includes("answer_completion_mcp")) {
        mcpInitId = crypto.randomUUID();
        socket.send(JSON.stringify({ jsonrpc: "2.0", id: mcpInitId, method: "initialize", params: {
          protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "interview-voice", version: "1.0.0" }
        } }));
        return;
      }
      const command = pendingCommand;
      pendingCommand = null;
      send(command);
      return;
    }
    if (message.type === "error") { progress.warn(message.code, `${message.code}：${message.detail}`); stop(`${message.code}：${message.detail}`); return; }
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
      progress.update(message);
      questionId = message.question.question_id;
      pendingId = null;
      el("question").textContent = message.question.text;
      el("question-meta").textContent = window.AppI18n?.language() === "en"
        ? `Question ${message.question_index} · ${message.question.dialogue_action} · Difficulty ${message.question.difficulty}`
        : `第 ${message.question_index} 题 · ${message.question.dialogue_action} · 难度 ${message.question.difficulty}`;
      el("evaluation-panel").hidden = !message.last_evaluation;
      el("evaluation").textContent = message.last_evaluation ? JSON.stringify(message.last_evaluation, null, 2) : "";
      const backendWaitMs = waitStarted === null ? null : performance.now() - waitStarted;
      endWait(); controls(); status(uiText("agent_question_answer"));
      if (ending?.kind === "finish") { dispatchFinish(); return; }
      voice.setQuestion(message.question, backendWaitMs);
      if (ending?.kind === "choosing") voice.suspended = true;
    } else if (message.type === "finished") {
      if (ending?.kind === "choosing") {
        ending.completed = message;
        pendingId = null;
        endWait();
        voice.reset(); voice.suspended = true;
        voice.disconnectAvatar();
        controls();
        return;
      }
      progress.update(message.result);
      const report = message.result.final_report;
      displayAssessment(report);
      const summaryPrefix = message.result.report_narrative_status === "fallback" ? uiText("agent_report_fallback") : "";
      el("report-summary").textContent = summaryPrefix + report.summary;
      el("report").textContent = JSON.stringify(message.result, null, 2);
      stop(uiText("agent_finished"));
    } else if (message.type === "discarded") { stop(uiText("interview_discarded")); clearResults(); }
    else if (message.type === "cancelled") stop(uiText("agent_cancelled"));
    else throw new Error(uiText("agent_unknown_response"));
  } catch (error) { progress.warn("client_protocol", error.message); stop(uiText("agent_processing_failed", { message: error.message })); }
}
/**
 * Input error event, stop only current connection, old connection events have no side effects.
 */
function onError(event) { if (socket === event.currentTarget) { progress.warn("connection_failed", uiText("agent_connection_failed")); stop(uiText("agent_connection_failed")); } }
/**
 * Input close event, clean up timer on unexpected disconnection, do not auto-reconnect or claim report completion.
 */
function onClose(event) {
  if (socket === event.currentTarget && !terminal) { progress.warn("unexpected_close", uiText("agent_unexpected_close")); stop(uiText("agent_unexpected_close")); }
}
/**
 * Input business command, reuse idle connection or wait for hello and announced MCP handshake; do not queue or auto-retry.
 */
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
/**
 * Input start form event; send ready version ID only after explicit confirmation inside dialog, do not send body.
 * Native required/min/step validation used; client-side checks loaded metadata and job, failure stays in dialog.
 * After confirmation, close dialog and enter scheduler; backend revalidates ownership and state; budget and error semantics remain unchanged.
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
  void voice.connectAvatar();
  el("preparation-dialog").close();
  dispatch({ type: "start", resume_version_id: id, job_title: job,
    keep_end_choice_open: true,
    duration_minutes: Number(el("duration").value),
    max_questions: Number(el("limit").value), max_follow_up_per_topic: Number(el("probes").value) });
}
/**
 * Input start/settings button click event; open native modal only when idle, no model, device, or version modification.
 * Save calling button to restore focus; during metadata loading, focus title first, otherwise focus resume selector; native dialog restricts focus.
 */
function onOpenPreparation(event) {
  if (interviewActive || pendingId || pendingCommand || el("preparation-dialog").open) return;
  preparationInvoker = event.currentTarget;
  onPreparationLanguage();
  el("preparation-error").textContent = "";
  el("preparation-dialog").showModal();
  el(resumesLoading ? "preparation-title" : "resume-select").focus();
}
/**
 * User clicks close or later start; native Esc also triggers same close event, retain form input, no connection operation.
 */
function onClosePreparation() { el("preparation-dialog").close(); }
/**
 * After native close event, restore focus; if interview confirmed, position to preparation progress, otherwise return to button that opened dialog.
 */
function onPreparationClosed() {
  if (interviewActive) el("agent-status").focus();
  else preparationInvoker?.focus();
}
/**
 * Input native keyboard event; first and last Tab loop between visible and enabled controls, prevent focus escape from dialog.
 * Only handle Tab, do not intercept Esc; element set read from current disabled/layout, no form state saved or reordered.
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
/**
 * Language switch only updates current version options, known status, and visible confirmation errors, does not change selection, parameters, or request.
 * Original version name remains unchanged; known status uses stable key, unexpected parsing exceptions preserve existing error text.
 */
function onPreparationLanguage() {
  progress.render();
  for (const option of el("resume-select").children) {
    if (!option.value) option.textContent = uiText("agent_resume_select");
    else for (const version of availableResumes) if (version.id === option.value) {
      option.textContent = (version.label || version.original_name || uiText("rm_unnamed")) + (version.is_current ? " · " + uiText("rm_current") : "");
    }
  }
  if (resumeSelectionMessage) el("resume-selection-status").textContent = uiText(resumeSelectionMessage);
  if (el("preparation-error").textContent) el("preparation-error").textContent = uiText("agent_resume_required");
}
/**
 * Inputs: Complete final text and optional semantic receipt. Outputs: None. Logic: Semantic receipts use MCP, ordinary inactivity/capture-limit text uses answer and empty closure uses skip.
 * Current question, idle state, and handshake revalidated; complete answer still processed by same backend secure/evaluation/planning flow.
 */
function onAnswer(text, completionReceipt = null) {
  const answer = text.trim();
  if (!interviewActive || !questionId || pendingId || pendingCommand || voice.busy || voice.capture || ending) return;
  voice.reset(false);
  voice.message(uiText("agent_submit"));
  status(uiText("agent_submit"));
  if (completionReceipt && !mcpReady) { stop("MCP answer completion is unavailable."); return; }
  dispatch(!answer ? { type: "skip", question_id: questionId } : completionReceipt
    ? { type: "mcp_finish", question_id: questionId, answer_text: answer, completion_receipt: completionReceipt }
    : { type: "answer", question_id: questionId, answer_text: answer });
}
/**
 * Functionality: Open the explicit end choice from any active connection. Inputs: Current socket/ending state. Outputs: None. Logic: Suspend automatic transitions and show a native modal; no backend command until a choice. Constraints: Already accepted requests may complete.
 */
function onCancel() {
  if (!socket || ending) return;
  ending = { kind: "choosing" };
  voice.suspended = true;
  el("end-interview-error").textContent = "";
  el("end-interview-dialog").showModal();
  controls();
}

/** Functionality: Send the chosen finish after any already accepted request completes.
 * Inputs: ending's optional final transcript and current server question ID.
 * Outputs: None. Logic: No retry or extra question submission; finished responses use regular review.
 * Constraints: A pending request must resolve before this command can be reserved by the backend.
 */
function dispatchFinish() {
  if (ending?.kind !== "finish" || pendingId || pendingCommand) return;
  const command = { type: "finish" };
  if (ending.text) { command.question_id = ending.questionId; command.answer_text = ending.text; }
  dispatch(command);
}

/** Functionality: Select early evaluation and preserve the final current transcript if available.
 * Inputs: Current voice/question/request state. Outputs: None; errors stay visible in the dialog.
 * Logic: Suspend automation, flush active recording once, and queue finish behind an accepted request.
 * Constraints: Final speech errors are not silently replaced by partial captions or omitted answers.
 */
async function onEndSave() {
  if (ending?.kind !== "choosing") return;
  if (ending.completed) {
    const message = ending.completed;
    ending = { kind: "finish" };
    pendingId = message.request_id;
    el("end-interview-dialog").close();
    onMessage({ currentTarget: socket, data: JSON.stringify(message) });
    return;
  }
  ending = { kind: "finalizing" };
  el("end-and-save").disabled = true;
  el("end-without-save").disabled = true;
  el("continue-interview").disabled = true;
  try {
    const text = pendingId || pendingCommand ? "" : await voice.takeFinalAnswer();
    if (!ending || terminal) return;
    ending = { kind: "finish", text, questionId };
    voice.reset(false);
    voice.suspended = true;
    el("end-interview-dialog").close();
    dispatchFinish();
  } catch (error) {
    console.error("Early interview finalization failed", { name: error.name });
    if (ending) ending = { kind: "choosing" };
    el("end-interview-error").textContent = error.message;
  } finally {
    for (const id of ["end-and-save", "end-without-save", "continue-interview"]) el(id).disabled = false;
    controls();
  }
}

/** Functionality: End without evaluation and remove this session's history.
 * Inputs: User's explicit discard choice. Outputs: None.
 * Logic: Cancel capture locally and send a distinct discard UUID even during a pending request.
 * Constraints: Keep connection open until deletion acknowledgement; failures never claim deletion.
 */
function onEndDiscard() {
  if (ending?.kind !== "choosing") return;
  ending = { kind: "discard" };
  voice.reset();
  voice.suspended = true;
  el("end-interview-dialog").close();
  beginWait();
  try { send({ type: "discard" }); }
  catch (error) { stop(error.message); }
}

/** Functionality: Dismiss the end choice and restore automatic answering.
 * Inputs: Dialog close/cancel event and ending state. Outputs: None.
 * Logic: Only an uncommitted choice is resumable; save/discard closures stay terminal.
 * Constraints: No duplicate speech capture or backend command is created.
 */
function onEndContinue(event) {
  event?.preventDefault();
  if (ending?.kind !== "choosing") return;
  if (ending.completed) { void onEndSave(); return; }
  ending = null;
  el("end-interview-dialog").close();
  controls();
  voice.resumeAnswer();
}
/**
 *  Clear connection and current interview display; keep original values for selected version, position, and number of questions.
 */
function onClear() { stop(uiText("agent_cleared")); clearResults(); }
/**
 *  Stop connection and interval after page leaves; do not use browser persistent storage to restore session.
 */
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
el("cancel-agent").addEventListener("click", onCancel);
el("end-and-save").addEventListener("click", onEndSave);
el("end-without-save").addEventListener("click", onEndDiscard);
el("continue-interview").addEventListener("click", onEndContinue);
el("end-interview-dialog").addEventListener("cancel", onEndContinue);
el("clear-agent").addEventListener("click", onClear);
window.addEventListener("pagehide", onPageHide);
window.addEventListener("languagechange", onPreparationLanguage);

loadResumeVersions();
