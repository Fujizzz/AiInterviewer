/**
 *
 * @module resumes
 * Responsibilities: Maintain basic information, original documents, unit edit drafts, and recommendation slots; explicitly request job recommendations after saving independent versions, protecting unsaved modifications.
 * Implementation: Upload PDF once after selection, parsing still triggered explicitly; recommendation area directly selects all saved versions and prompts for parsing/verification/saving;
 * Single-operation lock and CSRF protection for writes; read persistent details only after full NDJSON; hash-based partition switching changes only visibility, does not clear edit drafts.
 * Related Modules: resumes.html, i18n.js, /api/resume-versions/ and its recommendations actions; Django session authentication.
 * Declaration Index:
 * - rmWorkspacePanel: Maps page partitions based on existing anchors.
 * - rmReveal: Displays target partition and synchronizes title/navigation, allows explicit hash update, does not read or save data.
 * - rmWorkspaceHash: Responds to initial URL and browser forward/backward navigation.
 * - rmWorkspaceLink: Intercepts top-page entries, preserves unsaved drafts.
 * - rmScroll: Displays target panel first, then smoothly scrolls, avoiding hiding content.
 * - rmEl: Retrieves template elements.
 * - rmText: Reads loaded internationalized text.
 * - rmStatus: Displays plain-text operation status.
 * - rmControls: Switches controls based on operation and pagination state.
 * - rmRecommend: Only explicitly uses selected saved versions to request job recommendations, does not submit unsaved resumes.
 * - rmRecommend.recommend: Calls personal recommendation action, failure retains clear source/coarse-ranking/fine-ranking failure messages.
 * - rmRenderRecommendations: Safely displays LLM order, bilingual rationales, job sources, and skill intersections, does not convert scores into probabilities.
 * - rmRenderResumePicker: Redraws recommendation selector based on fully loaded version metadata, labels status and current version.
 * - rmChooseResume: User explicitly switches recommendation version, reuses un-saved confirmation and detail retrieval, does not modify current.
 * - rmChooseResume.choose: Loads selected persisted version within operation lock.
 * - rmReviewRecommendation: Expands skills and recommendation fields and moves focus, does not modify or save data.
 * - rmHasRecommendationDetails: Checks if at least one slot has known value, does not fill in unknown values.
 * - rmSource: Switches mutually exclusive upload forms.
 * - rmChooseFile: Opens native file picker, allows re-selecting same file, does not send request.
 * - rmFileSelected: Saves original when there is a clear file selection; canceling does not create version.
 * - rmRequest: Sends same-origin requests, uniformly handles authentication and business errors, retains stream response.
 * - rmRun: Executes operations serially, shows failure and releases UI state.
 * - rmButton: Generates named version action buttons.
 * - rmRender: Synchronizes full version selector and constructs current page history cards, does not insert HTML.
 * - rmLoad: Reads historical current page and other page metadata, updates full version selector and history pagination only after full success.
 * - rmPreview: Queries version and edit contract, displays original text and online units, returns persisted record.
 * - rmClearEditor: Clears edit state and disables operations, does not delete saved versions.
 * - rmFillEditor: Fills back unit/confirmation values and pending suggestion, marks evidence, protects unsaved suggestions, original text read-only.
 * - rmDirty: Records unit/slot input, clears save status.
 * - rmConfirmDiscard: Explicitly confirms leaving unsaved edits, cancels keep current content.
 * - rmReadSlots: Reads strictly recommended values, does not infer missing fields.
 * - rmSaveEdition: Assembles units and slots, saves independent edit snapshot.
 * - rmSaveEdition.save: Reads new version after creating snapshot, does not overwrite original.
 * - rmParseUploaded: Parses current pending attachment nearby.
 * - rmParseUploaded.parse: Executes one explicit parsing within operation lock.
 * - rmUseEdition: Sets an already saved and unedited version as current.
 * - rmUseEdition.select: Persists personal current version selection.
 * - rmSection: First displays target sidebar partition, then expands edit unit and positions.
 * - rmBeforeLeave: Triggers browser leave protection when unsaved modifications exist.
 * - rmUpload.save: Clears upload input and displays new version after successful creation.
 * - rmRefresh.refresh: Reads list and existing selection.
 * - rmPrevious.previous: Requests previous page by number.
 * - rmNext.next: Requests next page by number.
 * - rmAction.act: Executes confirmed action within operation lock.
 * - rmUpload: Saves one PDF or text version, then displays new version; ready text opens edit partition.
 * - rmSaveProfile: Submits personal name/email after form event validation.
 * - rmSaveProfile.save: After PATCH success, fills back server-side data and displays save status.
 * - rmRefresh: Explicitly refreshes current page and selected details.
 * - rmPrevious: Loads previous page.
 * - rmNext: Loads next page.
 * - rmEvent: Handles progress, error, and success terminal states, returns array of concerns or null.
 * - rmParse: Consumes extraction stream, only shows edit area and declares completion after full success, no retry or use of intermediate text.
 * - rmAction: Dispatches preview, current selection, and confirmed deletion based on loaded versions; parsing only triggered in attachment area.
 * - rmCancel: Cancels current stream reception, does not guarantee external model stops immediately.
 * - rmLanguage: Synchronizes partition titles and redraws version list when language changes, retains current content.
 * - rmInit.load: Reads list and opens current page's saved current; if none, retains selection prompt.
 * - rmInit: Loads initial list, retains refreshable interface on failure.
 * Variable Index:
 * - RM_WORKSPACE_SECTIONS: DOM elements corresponding to partitions; attachments and version history shown side-by-side.
 * - RM_WORKSPACE_TITLES: Translation keys for partition titles, does not affect business state.
 * - RM_API: Base prefix for same-origin version API.
 * - RM_PROFILE: Same-origin endpoint for personal profile.
 * - RM_UNITS: Stable unit IDs, consistent with backend contract.
 * - RM_SLOTS: Recommendation field input types, consistent with CandidateInput.
 * - rmEl: Element query function.
 * - rmBusy: Single operation lock.
 * - rmController: Current parsing AbortController; terminated on page leave.
 * - rmVersions: Metadata for current history page, no file bytes.
 * - rmChoiceVersions: Full pagination metadata for recommendation selector, no body or file bytes.
 * - rmPage: Current page number, starting from 1.
 * - rmNextPage: Server-declared presence of next page.
 * - rmSelected: Preview version ID in memory; does not write to browser storage or implicitly set current.
 * - rmAttachment: Metadata of most recently selected/uploaded original, used for nearby parsing.
 * - rmEditorBase: ID of currently edited saved baseline version.
 * - rmEditorDirty: Whether there are unsaved unit or slot modifications.
 * - rmInitialSlots: Confirmation values and pending suggestions from form; retains explicit empty array if not edited, written to new version only upon save.
 * - rmSlotTouched: Set of slot names manually modified.
 * - rmSuggestedCount: Number of pending suggestions loaded this time; reset to zero after first manual modification or reset.
 * - rmRecommendations: Sorted response from selected saved versions; cleared after edit or switch.
 * - rmRecommendationError: Current fault message key in recommendation area; cleared on success or switch.
 * - RM_RECOMMENDATION_ERRORS: Allowed list mapping stable service error codes to Chinese and English messages.
 * Constraints:
 * User text is displayed only via value/textContent; each save generates a new version, does not overwrite original or alter failure semantics.
 *
 */
const RM_WORKSPACE_SECTIONS = {
  files: ["resume-files", "version-history"], editor: ["resume-editor"], jobs: ["job-recommendations"], profile: ["profile-panel"],
};
const RM_WORKSPACE_TITLES = { files: "ws_resumes", editor: "re_editor_heading", jobs: "ws_jobs", profile: "rm_account" };
/**
 *  Input existing DOM anchor, output partition name; show attachments for unknown or empty URL, without modifying version selection or model parameters.
 */
function rmWorkspacePanel(id) {
  if (id === "job-recommendations") return "jobs";
  if (id === "profile-panel") return "profile";
  if (id === "resume-editor" || (id.startsWith("section-") && RM_UNITS.includes(id.slice(8)))) return "editor";
  return "files";
}
/**
 *  Input anchor and whether to update URL; only change hidden, title, and aria-current, without destroying form or sending request.
 * Use replaceState for URL; preserve explicit browser hash navigation; anchor marks unique sidebar entry point, does not include body in address or logs.
 *
 */
function rmReveal(id, navigate = false) {
  const panel = rmWorkspacePanel(id);
  let anchor = RM_WORKSPACE_SECTIONS[panel][0];
  if (id === "section-preferences" || id === "version-history") anchor = id;
  for (const [name, sections] of Object.entries(RM_WORKSPACE_SECTIONS)) {
    for (const section of sections) rmEl(section).hidden = name !== panel;
  }
  const title = rmEl("workspace-title");
  title.dataset.i18n = RM_WORKSPACE_TITLES[panel]; title.textContent = rmText(title.dataset.i18n);
  for (const link of document.querySelectorAll(".section-navigation a")) {
    const target = link.getAttribute("href").slice(1);
    if (target === anchor) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const link of document.querySelectorAll("[data-workspace-link]")) {
    const selected = panel === "jobs" ? link.dataset.workspaceLink === "job-recommendations" : link.dataset.workspaceLink === "resume-files";
    if (selected) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  if (navigate) window.history.replaceState(null, "", "#" + id);
}
/**
 *  No external input; read current URL hash and synchronize partition/edit unit expansion, supporting refresh, sharing, and browser history.
 */
function rmWorkspaceHash() {
  const id = window.location.hash.slice(1);
  rmReveal(id);
  if (id.startsWith("section-") && RM_UNITS.includes(id.slice(8))) rmEl(id).open = true;
}
/**
 *  Input click on top-of-page entry; preserve shortcut keys for new tab, normal click cancels page reload and displays target, retains all inputs and leave protection state.
 */
function rmWorkspaceLink(event) {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); rmReveal(event.currentTarget.dataset.workspaceLink, true);
}
/**
 *  Input existing element ID; expand containing partition first, then scroll; user data and focus handled by caller.
 */
function rmScroll(id) {
  rmReveal(id, true); rmEl(id).scrollIntoView({ behavior: "smooth", block: "start" });
}
const RM_API = "/api/resume-versions/";
const RM_PROFILE = "/api/profile/";
const RM_UNITS = ["basic", "education", "experience", "projects", "skills", "awards", "publications", "preferences", "other"];
const RM_SLOTS = {
  skills: "tags", interests: "tags", majors: "tags", in_person_commitment: "select",
  gpa: "number", months_experience: "number", academic_level: "select", hours_per_week: "number",
  length_of_commitment: "number", num_publications: "integer", commit_to_summer: "boolean",
};
/**
 *  Input template ID, return DOM node; missing node explicitly exposes template contract error.
 */
const rmEl = (id) => document.getElementById(id);
let rmBusy = false;
let rmController = null;
let rmVersions = [];
let rmChoiceVersions = [];
let rmPage = 1;
let rmNextPage = false;
let rmSelected = null;
let rmAttachment = null;
let rmEditorBase = null;
let rmEditorDirty = false;
let rmInitialSlots = {};
let rmSlotTouched = new Set();
let rmSuggestedCount = 0;
let rmRecommendations = null;
let rmRecommendationError = null;
const RM_RECOMMENDATION_ERRORS = {
  job_catalog_not_configured: "rj_no_catalog", job_catalog_invalid: "rj_bad_catalog",
  recommendation_profile_empty: "rj_empty_profile", recommendation_model_unavailable: "rj_model_error",
  recommendation_profile_invalid: "rj_profile_error", recommendation_features_invalid: "rj_feature_error",
  recommendation_llm_not_configured: "rj_llm_config", recommendation_llm_unavailable: "rj_llm_error",
  recommendation_llm_invalid_output: "rj_llm_output",
  resume_not_ready: "rm_unready",
};

/**
 *  Input translation key and parameters, return current language plain text; i18n.js is explicit dependency loaded beforehand.
 */
function rmText(key, values = {}) { return window.AppI18n.t(key, values); }
/**
 *  Input status and error flags, output text and color; does not log user text or server responses.
 */
function rmStatus(message, error = false) {
  rmEl("operation-status").textContent = message;
  rmEl("operation-status").classList.toggle("error", error);
}
/**
 *  Read page state to set disabled/aria-busy; allows cancellation of parsing but disallows concurrent writes.
 */
function rmControls() {
  for (const id of ["upload-submit", "choose-file", "upload-kind", "version-label", "resume-file", "resume-text", "parse-mode", "refresh-versions", "profile-save", "profile-name", "profile-email"]) rmEl(id).disabled = rmBusy;
  for (const button of rmEl("versions-list").querySelectorAll("button")) button.disabled = rmBusy;
  rmEl("previous-page").disabled = rmBusy || rmPage <= 1;
  rmEl("next-page").disabled = rmBusy || !rmNextPage;
  rmEl("cancel-parse").disabled = rmController === null;
  rmEl("cancel-parse").hidden = rmController === null;
  rmEl("uploaded-parse").disabled = rmBusy || rmAttachment?.status !== "uploaded";
  rmEl("attachment-toolbar").hidden = rmAttachment?.status !== "uploaded" && rmController === null;
  rmEl("parse-mode-hint").hidden = rmEl("parse-mode").value !== "advanced";
  rmEl("uploaded-file-name").hidden = rmAttachment === null;
  rmEl("edition-fields").hidden = rmEditorBase === null;
  rmEl("editor-savebar").hidden = rmEditorBase === null;
  rmEl("edition-fields").disabled = rmBusy || rmEditorBase === null;
  rmEl("edition-save").disabled = rmBusy || rmEditorBase === null;
  rmEl("edition-current").disabled = rmBusy || rmEditorBase === null || rmEditorDirty;
  rmEl("recommend-jobs").disabled = rmBusy || rmEditorBase === null || rmEditorDirty || !rmHasRecommendationDetails();
  rmEl("recommendation-resume").disabled = rmBusy || !rmChoiceVersions.length;
  rmEl("recommendation-review").disabled = rmBusy;
  rmEl("recommendation-save").disabled = rmBusy;
  rmEl("recommendation-parse").disabled = rmBusy;
  rmEl("recommendation-results").setAttribute("aria-busy", String(rmBusy));
  rmRenderRecommendations();
  rmEl("versions-list").setAttribute("aria-busy", String(rmBusy));
}
/**
 *  Read user options, only display corresponding file/text input; does not clear other input or send request.
 */
function rmSource() {
  const pdf = rmEl("upload-kind").value === "pdf";
  rmEl("pdf-input").hidden = !pdf;
  rmEl("text-input").hidden = pdf;
  rmEl("upload-submit").hidden = pdf;
}
/**
 *  No external parameters; clear native input to allow selecting same file again; do not open when busy, no database write or automatic retry.
 */
function rmChooseFile() {
  if (rmBusy) return;
  rmEl("resume-file").value = "";
  rmEl("resume-file").click();
}
/**
 *  Input file selection change event; only proceed with existing upload validation/CSRF if non-empty and in PDF mode; canceling does not discard online draft or parsed content.
 */
async function rmFileSelected(event) {
  if (rmBusy || rmEl("upload-kind").value !== "pdf" || !rmEl("resume-file").files.length) return;
  await rmUpload(event);
}
/**
 *  Input relative path to interface, fetch parameters, and internal base, return response; write operations include CSRF, errors thrown explicitly.
 */
async function rmRequest(path = "", options = {}, base = RM_API) {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET") headers.set("X-CSRFToken", rmEl("csrf-token").content);
  const response = await fetch(base + path, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) throw new Error(rmText("rm_auth_error"));
    const data = await response.json();
    if (RM_RECOMMENDATION_ERRORS[data.code]) {
      const error = new Error(rmText(RM_RECOMMENDATION_ERRORS[data.code]));
      error.translationKey = RM_RECOMMENDATION_ERRORS[data.code];
      throw error;
    }
    const key = data.code === "resume_in_use" ? "rm_in_use" : data.code === "resume_not_uploaded" ? "rm_not_uploaded" : "rm_http_error";
    throw new Error(rmText(key, { status: response.status }));
  }
  return response;
}
/**
 *  Input no-parameter asynchronous operation; execute single operation, display and record exception type, finally release all controls.
 */
async function rmRun(operation) {
  if (rmBusy) return;
  rmBusy = true;
  rmControls();
  try { await operation(); }
  catch (error) {
    console.error("Resume management operation failed", error.name);
    rmStatus(error.name === "AbortError" ? rmText("rm_cancelled") : error.message, true);
  } finally { rmBusy = false; rmControls(); }
}
/**
 *  Input action, ID, and translation key, return safe DOM node with type=button, does not trigger action.
 */
function rmButton(action, id, key) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = action === "delete" ? "secondary danger" : "secondary";
  button.dataset.action = action;
  button.dataset.id = id;
  button.textContent = rmText(key);
  return button;
}
/**
 *  Read full selection metadata, current history page, and preview ID, synchronize options/cards; download uses only UUID, does not interpret user HTML.
 */
function rmRender() {
  rmRenderResumePicker();
  const list = rmEl("versions-list");
  list.replaceChildren();
  if (!rmVersions.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = rmText("rm_empty");
    list.append(empty);
  }
  for (const version of rmVersions) {
    const card = document.createElement("article");
    card.className = "version-card" + (version.id === rmSelected ? " selected" : "");
    const title = document.createElement("h3");
    title.textContent = version.label || version.original_name || rmText("rm_unnamed");
    const state = document.createElement("span");
    state.className = "badge";
    state.textContent = rmText("rm_status_" + version.status);
    card.append(title, state);
    const kind = document.createElement("span");
    kind.className = "badge";
    kind.textContent = rmText(version.source_version ? "re_edited_kind" : "re_original_kind");
    card.append(kind);
    if (version.is_current) {
      const current = document.createElement("span");
      current.className = "badge current";
      current.textContent = rmText("rm_current");
      card.append(current);
    }
    const meta = document.createElement("p");
    meta.className = "version-meta";
    meta.textContent = new Date(version.created_at).toLocaleString(document.documentElement.lang);
    if (version.original_name) meta.textContent += " · " + version.original_name;
    if (version.extraction_mode) meta.textContent += " · " + rmText("rm_" + version.extraction_mode);
    if (version.error_code) meta.textContent += " · " + version.error_code;
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(rmButton("preview", version.id, "rm_view"));
    const more = document.createElement("details");
    more.className = "version-more";
    const summary = document.createElement("summary");
    summary.textContent = rmText("re_more");
    const secondary = document.createElement("div");
    secondary.className = "version-menu";
    if (version.status === "ready" && !version.is_current) secondary.append(rmButton("current", version.id, "rm_select"));
    if (version.status === "ready") {
      const exported = document.createElement("a");
      exported.className = "nav-link";
      exported.href = RM_API + encodeURIComponent(version.id) + "/export/";
      exported.textContent = rmText("re_download");
      secondary.append(exported);
      const interview = document.createElement("a");
      interview.className = "nav-link";
      interview.href = "/agent/?resume_version_id=" + encodeURIComponent(version.id);
      interview.textContent = rmText("rm_use_interview");
      secondary.append(interview);
    }
    if (version.original_name) {
      const download = document.createElement("a");
      download.className = "nav-link";
      download.href = RM_API + encodeURIComponent(version.id) + "/download/";
      download.textContent = rmText("rm_download");
      secondary.append(download);
    }
    if (version.status !== "parsing") secondary.append(rmButton("delete", version.id, "rm_delete"));
    if (secondary.children.length) { more.append(summary, secondary); actions.append(more); }
    card.append(meta, actions);
    list.append(card);
  }
  rmEl("page-number").textContent = rmText("rm_page", { page: rmPage });
  rmControls();
}
/**
 *  Input the page number of the history; read all page metadata to fully list selectable versions, reuse the target page response without altering backend pagination.
 * Replace the list only when all requests succeed; failures are handed over to the operation lock for display, without truncation or rollback. Only after selection, read the body and edit contract.
 */
async function rmLoad(page = rmPage) {
  const data = await (await rmRequest("?page=" + page)).json();
  const choices = [];
  let index = 1;
  let next = true;
  while (next) {
    const result = index === page ? data : await (await rmRequest("?page=" + index)).json();
    choices.push(...result.results);
    next = Boolean(result.next);
    index += 1;
  }
  rmChoiceVersions = choices;
  rmVersions = data.results;
  rmPage = page;
  rmNextPage = Boolean(data.next);
  rmEl("version-count").textContent = String(data.count);
  rmRender();
}
/**
 *  Input the personal version ID and internal confirmed flag, return details; confirm unsaved content before switching, ready before reading the unit contract, fail without impersonating old results.
 */
async function rmPreview(id, discardConfirmed = false) {
  if (!discardConfirmed && !rmConfirmDiscard()) return null;
  rmSelected = id;
  rmAttachment = null;
  rmClearEditor();
  rmEl("preview-text").value = "";
  rmEl("preview-notes").textContent = "";
  rmEl("preview-meta").textContent = rmText("rm_loading");
  const version = await (await rmRequest(encodeURIComponent(id) + "/")).json();
  if (!version.source_version && version.original_name) {
    rmAttachment = version;
    rmEl("uploaded-file-name").textContent = version.original_name + " · " + rmText("rm_status_" + version.status);
  }
  rmEl("preview-meta").textContent = (version.label || version.original_name || rmText("rm_unnamed")) + " · " + rmText("rm_status_" + version.status);
  if (version.status === "ready") {
    const editor = await (await rmRequest(encodeURIComponent(id) + "/editor/")).json();
    rmFillEditor(editor);
  } else rmEl("preview-notes").textContent = rmText("rm_unready");
  rmRender();
  return version;
}
/**
 *  Clear unsaved state and download pointer, disable editing; confirmation of discarding must be made by action entry prior to calling, do not delete server-side data.
 */
function rmClearEditor() {
  rmRecommendations = null; rmRecommendationError = null;
  rmEditorBase = null; rmEditorDirty = false; rmInitialSlots = {}; rmSlotTouched.clear(); rmSuggestedCount = 0;
  for (const key of RM_UNITS) rmEl("unit-" + key).value = "";
  for (const key of Object.keys(RM_SLOTS)) rmEl("slot-" + key).value = "";
  rmEl("edition-label").value = "";
  rmEl("edition-download").hidden = true; rmEl("original-download").hidden = true;
  rmEl("editor-state").textContent = rmText("re_editor_empty");
  rmControls();
}
/**
 *  Input the authorized edit response; prioritize confirmed values, insert suggestions with valid basis into the draft to be saved and display evidence, do not re-extract the edited draft.
 * Disable setting current when automatic suggestion is unsaved and protect against leaving; keep blank for unknown, do not overwrite confirmed 0/false/[] values.
 */
function rmFillEditor(editor) {
  rmEditorBase = editor.id;
  if (editor.original_name) {
    rmAttachment = { id: editor.source_version, original_name: editor.original_name, status: "ready" };
    rmEl("uploaded-file-name").textContent = editor.original_name + " · " + rmText("rm_status_ready");
  }
  rmInitialSlots = { ...(editor.slots || {}) };
  rmSuggestedCount = 0;
  rmSlotTouched.clear();
  for (const key of RM_UNITS) {
    const value = editor.units[key] || "";
    rmEl("unit-" + key).value = value;
    rmEl("section-" + key).open = Boolean(value.trim()) || ["education", "experience", "projects", "skills"].includes(key);
  }
  for (const key of Object.keys(RM_SLOTS)) {
    const confirmed = rmInitialSlots[key];
    const suggestion = editor.slot_suggestions?.values?.[key];
    const suggested = (confirmed === null || confirmed === undefined) && suggestion !== null && suggestion !== undefined;
    const value = suggested ? suggestion : confirmed;
    if (suggested) { rmInitialSlots[key] = value; rmSuggestedCount += 1; }
    const field = rmEl("slot-" + key);
    const sources = suggested ? editor.slot_suggestions.evidence?.[key] || [] : [];
    const sourceText = [];
    for (const source of sources) sourceText.push(source.text);
    field.setAttribute("title", suggested ? rmText("re_suggestion_source", { text: sourceText.join("\n") }) : "");
    field.value = value === null || value === undefined ? "" : Array.isArray(value) ? value.join(", ") : String(value);
    if (suggested) {
      const section = editor.slot_units?.[key];
      if (section && RM_UNITS.includes(section)) rmEl("section-" + section).open = true;
      const details = field.closest(".recommendation-fields");
      if (details) details.open = true;
    }
    if (RM_SLOTS[key] === "tags") field.placeholder = Array.isArray(value) && !value.length ? rmText("re_known_empty") : rmText("re_unknown");
  }
  rmEl("edition-label").value = editor.label || "";
  rmEl("preview-meta").textContent = editor.original_name || rmText("re_original_kind");
  rmEl("preview-text").value = editor.original_text;
  rmEl("edition-download").href = RM_API + encodeURIComponent(editor.id) + "/export/";
  rmEl("edition-download").hidden = false;
  rmEl("original-download").href = RM_API + encodeURIComponent(editor.source_version) + "/download/";
  rmEl("original-download").hidden = !editor.original_name;
  rmEl("editor-state").textContent = rmSuggestedCount ? rmText("re_suggestions", { count: rmSuggestedCount }) : rmText("re_editor_loaded");
  rmEditorDirty = rmSuggestedCount > 0;
  rmControls();
}
/**
 *  Input real input/change event; mark manual changes, recommend clearing fields this time to indicate unknown, do not perform automatic extraction.
 */
function rmDirty(event) {
  if (!rmEditorBase) return;
  rmRecommendations = null; rmRecommendationError = null;
  rmEditorDirty = true;
  rmSuggestedCount = 0;
  if (event.currentTarget.dataset.slot) rmSlotTouched.add(event.currentTarget.dataset.slot);
  rmEl("editor-state").textContent = rmText("re_unsaved");
  rmControls();
}
/**
 *  Read unsaved status and return whether switching is allowed; only explicitly confirmed actions discard the workspace, no auto-save or retry.
 */
function rmConfirmDiscard() {
  if (!rmEditorDirty) return true;
  if (!window.confirm(rmText("re_discard"))) return false;
  return true;
}
/**
 *  Output optional fields of CandidateInput; comma-separated tags preserve case, numbers strictly validated, blanks and unknowns do not pad with zeros.
 */
function rmReadSlots() {
  const slots = {};
  for (const [key, kind] of Object.entries(RM_SLOTS)) {
    const raw = rmEl("slot-" + key).value.trim();
    if (!raw) {
      slots[key] = !rmSlotTouched.has(key) && Array.isArray(rmInitialSlots[key]) && !rmInitialSlots[key].length ? [] : null;
    } else if (kind === "tags") {
      const names = [];
      for (const part of raw.split(/[,，;；\n]+/)) if (part.trim()) names.push(part.trim());
      slots[key] = names;
    } else if (kind === "number" || kind === "integer") {
      const number = Number(raw);
      if (!Number.isFinite(number) || number < 0 || (kind === "integer" && !Number.isInteger(number))) throw new Error(rmText("re_number_error"));
      slots[key] = number;
    } else if (kind === "boolean") {
      if (!["true", "false"].includes(raw)) throw new Error(rmText("re_number_error"));
      slots[key] = raw === "true";
    } else slots[key] = raw;
  }
  return slots;
}
/**
 *  Input edit form event; validate and save as a separate new draft, do not overwrite original/history or automatically set current.
 * When submitter is the recommendation area save button, return to the recommendation area on success; other save entries maintain original edit position.
 */
async function rmSaveEdition(event) {
  event.preventDefault();
  if (rmBusy || !rmEditorBase) return;
  const units = {};
  for (const key of RM_UNITS) units[key] = rmEl("unit-" + key).value;
  let slots;
  try { slots = rmReadSlots(); }
  catch (error) { rmStatus(error.message, true); return; }
  const base = rmEditorBase;
  const returnToRecommendations = event.submitter?.id === "recommendation-save";
  const body = JSON.stringify({ label: rmEl("edition-label").value.trim(), units, slots });
  /**
 *  Clear unsaved state only after successful POST, read the new draft as the baseline for next edit; retain original input on failure.
 */
  async function save() {
    const version = await (await rmRequest(encodeURIComponent(base) + "/editions/", { method: "POST", headers: { "Content-Type": "application/json" }, body })).json();
    rmEditorDirty = false;
    await rmLoad(1); await rmPreview(version.id, true);
    rmStatus(rmText("re_saved"));
    if (returnToRecommendations) rmScroll("job-recommendations");
  }
  await rmRun(save);
}
/**
 *  Input optional button event; parse only the current uploaded attachment, explicitly confirm unsaved draft, do not re-parse ready/failed states.
 * On recommendation area trigger, return to that area on success; attachment original entry maintains original position, no change in parsing mode or vendor call.
 */
async function rmParseUploaded(event) {
  if (rmBusy || rmAttachment?.status !== "uploaded" || !rmConfirmDiscard()) return;
  const id = rmAttachment.id;
  // Clear currentTarget after asynchronous wait for native events; save entry flag in advance to preserve navigation position after parsing.
  const returnToRecommendations = event?.currentTarget?.id === "recommendation-parse";
  /**
 *  Maintain full-page write operation lock, reuse one explicit stream consumption.
 */
  async function parse() { await rmParse(id); }
  await rmRun(parse);
  if (returnToRecommendations && rmEditorBase) rmScroll("job-recommendations");
}
/**
 *  Only saved and unmodified versions can be set as current; modifications must be saved first to avoid mistaking unsaved content for adopted.
 */
async function rmUseEdition() {
  if (rmBusy || !rmEditorBase || rmEditorDirty) return;
  const id = rmEditorBase;
  /**
 *  Refresh metadata after POSTing the current selection, do not clear user-saved edit content.
 */
  async function select() {
    await rmRequest(encodeURIComponent(id) + "/current/", { method: "POST" });
    await rmLoad(); rmStatus(rmText("rm_selected"));
  }
  await rmRun(select);
}
/**
 *  No external input; only personally saved and unmodified versions are allowed to request, CSRF and full-page serial lock reused from version interface.
 */
async function rmRecommend() {
  if (rmBusy || !rmEditorBase || rmEditorDirty || !rmHasRecommendationDetails()) return;
  const id = rmEditorBase;
  rmRecommendations = null; rmRecommendationError = null;
  /**
 *  Read real snapshot sorting; on exception, display stable text and hand over to operation lock for logging, do not retry or retain expired results.
 */
  async function recommend() {
    rmEl("recommendation-state").textContent = rmText("rj_loading");
    try {
      const data = await (await rmRequest(encodeURIComponent(id) + "/recommendations/", { method: "POST" })).json();
      if (id === rmEditorBase && !rmEditorDirty) rmRecommendations = data;
    } catch (error) {
      rmRecommendationError = error.translationKey || "rj_error";
      throw error;
    }
  }
  await rmRun(recommend);
}
/**
 *  Read confirmed/pending slots; at least one non-null/undefined item indicates known, []/0/false retain existing semantics.
 */
function rmHasRecommendationDetails() {
  for (const key of Object.keys(RM_SLOTS)) if (rmInitialSlots[key] !== null && rmInitialSlots[key] !== undefined) return true;
  return false;
}
/**
 *  Input full metadata and current preview ID; generate plain text options including status, save time, and current marker.
 * Do not automatically select latest draft, do not alter current interview resume; language and selected value synchronized, all body still loaded per user selection.
 */
function rmRenderResumePicker() {
  const picker = rmEl("recommendation-resume");
  picker.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = ""; placeholder.textContent = rmText("rj_choose_resume");
  placeholder.disabled = true; picker.append(placeholder);
  for (const version of rmChoiceVersions) {
    const option = document.createElement("option");
    option.value = version.id;
    option.textContent = (version.label || version.original_name || rmText("rm_unnamed")) + " · " + rmText("rm_status_" + version.status) + " · " + new Date(version.created_at).toLocaleString(document.documentElement.lang) + (version.is_current ? " · " + rmText("rm_current") : "");
    picker.append(option);
  }
  picker.value = rmSelected || "";
}
/**
 *  Input user change event; restore selector on cancel discard, serially read selected details after approval, do not request recommendations or auto-save.
 */
async function rmChooseResume(event) {
  const id = event.currentTarget.value;
  if (rmBusy || !id || id === rmSelected || !rmConfirmDiscard()) { rmRenderResumePicker(); return; }
  /**
 *  After confirmed switch, load same preview/edit path, maintain save and permission boundaries.
 */
  async function choose() {
    await rmPreview(id, true);
    rmScroll("job-recommendations");
  }
  await rmRun(choose);
  if (rmSelected === id) rmEl("recommendation-resume").focus({ preventScroll: true });
}
/**
 *  Expand skills and optional recommendation fields when user explicitly verifies, scroll and focus; do not modify content, do not infer or write to API.
 */
function rmReviewRecommendation() {
  if (rmBusy || !rmEditorBase) return;
  rmEl("section-skills").open = true;
  const details = rmEl("slot-skills").closest(".recommendation-fields");
  if (details) details.open = true;
  rmScroll("section-skills");
  rmEl("slot-skills").focus({ preventScroll: true });
}
/**
 *  Read current selection, save status, and service response, output next action and plain DOM job card.
 * messageKey determines prompt order: failure, unsaved, not ready, unknown data, recommended; final ranking comes from LLM.
 * Original score not used as probability; reason output as textContent in current language, language switch does not trigger API request.
 */
function rmRenderRecommendations() {
  rmEl("recommendation-review").hidden = !rmEditorBase;
  rmEl("recommendation-save").hidden = !rmEditorBase || !rmEditorDirty;
  rmEl("recommendation-parse").hidden = Boolean(rmEditorBase) || rmAttachment?.status !== "uploaded";
  rmEl("recommendation-upload").hidden = Boolean(rmEditorBase) || rmAttachment?.status === "uploaded";
  const list = rmEl("recommendation-results");
  list.replaceChildren();
  const state = rmEl("recommendation-state");
  let selected = null;
  for (const item of rmChoiceVersions) if (item.id === rmSelected) selected = item;
  const version = selected?.label || selected?.original_name || rmText("rm_unnamed");
  let messageKey = "rj_ready";
  if (rmRecommendationError) messageKey = rmRecommendationError;
  else if (rmEditorDirty) messageKey = "rj_save_first";
  else if (!rmEditorBase) {
    if (rmAttachment?.status === "uploaded") messageKey = "rj_parse_first";
    else messageKey = rmChoiceVersions.length ? "rj_select_first" : "rj_upload_first";
  } else if (!rmHasRecommendationDetails()) messageKey = "rj_empty_profile";
  state.textContent = messageKey === "rj_ready" ? rmText(messageKey, { version }) : rmText(messageKey);
  list.hidden = !rmRecommendations;
  if (!rmRecommendations) return;
  state.textContent = rmText("rj_source", { version, source: rmRecommendations.source_name, count: rmRecommendations.results.length }) + (rmRecommendations.source_kind === "experience" ? " · " + rmText("rj_experience") : "");
  for (const result of rmRecommendations.results) {
    const card = document.createElement("article"); card.className = "recommended-job";
    const heading = document.createElement("h3"); heading.textContent = result.job.title;
    const meta = document.createElement("p"); meta.className = "hint";
    const parts = [];
    for (const value of [result.job.company, result.job.location]) if (value) parts.push(value);
    const mode = result.job.requirements.job_in_person_commitment;
    if (mode) parts.push(rmText("re_option_" + mode.replaceAll(" ", "_")));
    meta.textContent = parts.join(" · ");
    const rank = document.createElement("span"); rank.className = "badge";
    rank.textContent = rmText("rj_rank", { rank: result.rank });
    const header = document.createElement("div"); header.className = "job-heading";
    header.append(rank, heading); card.append(header);
    if (parts.length) card.append(meta);
    const reason = document.createElement("p"); reason.className = "job-reason";
    const reasonLabel = document.createElement("strong"); reasonLabel.textContent = rmText("rj_reason");
    const reasonText = document.createElement("span");
    reasonText.textContent = result.recommendation_reason[window.AppI18n.language()];
    reason.append(reasonLabel, reasonText); card.append(reason);
    const skills = document.createElement("p"); skills.className = "job-skills";
    skills.textContent = rmText("rj_skills", { skills: result.job.requirements.required_skills?.join(", ") || rmText("re_unknown") });
    const matches = document.createElement("p"); matches.className = "hint";
    matches.textContent = rmText("rj_matches", { skills: result.matched_skills.join(", ") || rmText("rj_no_matches"), count: result.available_feature_count });
    card.append(skills, matches);
    if (result.job.description) {
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = rmText("rj_details");
      const description = document.createElement("p"); description.textContent = result.job.description;
      details.append(summary, description); card.append(details);
    }
    list.append(card);
  }
}
/**
 *  Input sidebar click event; first show target section, then expand specified edit unit and scroll; preserve new tab shortcut, do not modify text or request model.
 */
function rmSection(event) {
  const link = event.target.closest("a[href^='#']");
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const id = link.getAttribute("href").slice(1);
  if (id.startsWith("section-") && RM_UNITS.includes(id.slice(8))) rmEl(id).open = true;
  rmScroll(id);
}
/**
 *  Input beforeunload event; enable browser-native leave prompt only if unsaved text exists, do not store content locally.
 */
function rmBeforeLeave(event) { if (rmEditorDirty) { event.preventDefault(); event.returnValue = ""; } }
/**
 *  Input text submission or PDF selection event; validate and save original once, clear input only on success, ready text displayed in edit area, do not auto-parse or switch current.
 */
async function rmUpload(event) {
  event.preventDefault();
  if (rmBusy) return;
  let body;
  const headers = {};
  const label = rmEl("version-label").value.trim();
  if (rmEl("upload-kind").value === "pdf") {
    const file = rmEl("resume-file").files[0];
    if (!file || !file.size || file.size > 10 * 1024 * 1024) { rmStatus(rmText("rm_file_error"), true); return; }
    body = new FormData(); body.append("file", file); body.append("label", label);
  } else {
    const text = rmEl("resume-text").value.trim();
    if (!text || text.length > 200000) { rmStatus(rmText("rm_text_error"), true); return; }
    headers["Content-Type"] = "application/json";
    body = JSON.stringify({ label, text });
  }
  if (!rmConfirmDiscard()) return;
  /**
 *  Clear input only after successful request; text preview read from server, do not treat client cache as persistent success.
 */
  async function save() {
    rmStatus(rmText("rm_saving"));
    const version = await (await rmRequest("", { method: "POST", headers, body })).json();
    rmEl("version-label").value = ""; rmEl("resume-file").value = ""; rmEl("resume-text").value = "";
    await rmLoad(1); await rmPreview(version.id, true);
    if (version.status === "ready") rmReveal("resume-editor", true);
    rmStatus(rmText("rm_saved"));
  }
  await rmRun(save);
}
/**
 *  Input profile form event; read only name and email, reuse operation lock, retain filled content on failure, do not modify account identity.
 */
async function rmSaveProfile(event) {
  event.preventDefault();
  if (rmBusy) return;
  const body = JSON.stringify({ first_name: rmEl("profile-name").value.trim(), email: rmEl("profile-email").value.trim() });
  /**
 *  Submit personal profile and backfill fields with response, only show completion on successful save; no auto-retry.
 */
  async function save() {
    rmStatus(rmText("rm_profile_saving"));
    const data = await (await rmRequest("", { method: "PATCH", headers: { "Content-Type": "application/json" }, body }, RM_PROFILE)).json();
    rmEl("profile-name").value = data.first_name;
    rmEl("profile-email").value = data.email;
    rmStatus(rmText("rm_profile_saved"));
  }
  await rmRun(save);
}
/**
 *  Explicit refresh, do not auto-replay any write operations; details update per original selection, external deletion errors still clearly displayed.
 */
async function rmRefresh() {
  if (!rmConfirmDiscard()) return;
  /**
 *  Refresh list and selected details, all responses from real authorized interface.
 */
  async function refresh() { await rmLoad(); if (rmSelected) await rmPreview(rmSelected, true); rmStatus(rmText("rm_refreshed")); }
  await rmRun(refresh);
}
/**
 *  Move to previous page; double-check boundary via button and function, fail to keep current page.
 */
async function rmPrevious() {
  /**
 *  Use current page number to request previous page, no write operation.
 */
  async function previous() { await rmLoad(rmPage - 1); }
  if (rmPage > 1) await rmRun(previous);
}
/**
 *  Read next page only when server-side next exists, do not follow arbitrary URL.
 */
async function rmNext() {
  /**
 *  Construct local next page query using number, do not rely on external address.
 */
  async function next() { await rmLoad(rmPage + 1); }
  if (rmNextPage) await rmRun(next);
}
/**
 *  Input parsing event, display progress/necessary doubts; throw error and do not use text, result returns array of doubts, others return null.
 */
function rmEvent(data) {
  if (data.type === "error") throw new Error(data.detail);
  if (data.type === "progress") rmStatus(data.detail || rmText("rm_parsing"));
  else if (data.type === "result") {
    if (typeof data.text !== "string") throw new Error(rmText("rm_incomplete"));
    const notes = [];
    for (const page of data.pages || []) {
      if (page.uncertainties?.length) notes.push(rmText("rm_notes", { page: page.number, notes: page.uncertainties.join("；") }));
    }
    return notes;
  }
  return null;
}
/**
 *  Input the uploaded version ID; parse using the current mode, strictly consuming UTF-8 NDJSON; cancellation or failure maintains server-side failure semantics.
 */
async function rmParse(id) {
  const controller = new AbortController();
  rmController = controller; rmControls();
  let reader = null;
  let notes = null;
  let failure = null;
  try {
    rmStatus(rmText("rm_parsing"));
    const response = await rmRequest(encodeURIComponent(id) + "/parse/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: rmEl("parse-mode").value }), signal: controller.signal });
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
        if (line.trim()) {
          if (notes !== null) throw new Error(rmText("rm_incomplete"));
          notes = rmEvent(JSON.parse(line));
        }
      }
      if (done) break;
    }
    if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (notes === null || buffer.trim()) throw new Error(rmText("rm_incomplete"));
  } catch (error) { failure = error; }
  finally {
    if (reader) {
      try { await reader.cancel(); }
      catch (error) { console.error("Resume parse stream cleanup failed", error.name); }
      reader.releaseLock();
    }
    rmController = null; rmControls();
  }
  try {
    await rmLoad();
    const persisted = await rmPreview(id, true);
    if (!failure && persisted?.status !== "ready") throw new Error(rmText("rm_incomplete"));
  }
  catch (error) {
    if (!failure) throw error;
    console.error("Resume parse state refresh failed", error.name);
  }
  if (failure) throw failure;
  rmEl("preview-notes").textContent = notes.join("\n");
  rmReveal("resume-editor", true);
  rmStatus(rmText("rm_parsed"));
}
/**
 *  Handle list click events with preview display in the edit area, operating only on loaded IDs; deletion requires explicit confirmation, and reference conflicts are maintained by the backend with HTTP 409.
 */
async function rmAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button || rmBusy) return;
  const { action, id } = button.dataset;
  let version = null;
  for (const item of rmVersions) if (item.id === id) version = item;
  if (!version) return;
  if ((action === "preview" || action === "current" || (action === "delete" && rmSelected === id)) && !rmConfirmDiscard()) return;
  if (action === "delete" && !window.confirm(rmText("rm_delete_confirm"))) return;
  /**
 *  Call a single, unambiguous action within the operation lock; do not introduce automatic selection, parsing retry, or deletion bypass.
 */
  async function act() {
    if (action === "preview") { await rmPreview(id, true); rmScroll(rmEditorBase ? "resume-editor" : "resume-files"); rmStatus(""); }
    else if (action === "current") {
      await rmRequest(encodeURIComponent(id) + "/current/", { method: "POST" });
      await rmLoad(); await rmPreview(id, true); rmStatus(rmText("rm_selected"));
    } else if (action === "delete") {
      await rmRequest(encodeURIComponent(id) + "/", { method: "DELETE" });
      if (rmSelected === id) {
        rmClearEditor();
        rmSelected = null; rmEl("preview-text").value = ""; rmEl("preview-notes").textContent = "";
        rmEl("preview-meta").textContent = rmText("rm_preview_hint");
      }
      if (rmAttachment?.id === id) { rmAttachment = null; rmEl("uploaded-file-name").textContent = rmText("re_upload_empty"); }
      await rmLoad(rmVersions.length === 1 && rmPage > 1 ? rmPage - 1 : rmPage);
      rmStatus(rmText("rm_deleted"));
    }
  }
  await rmRun(act);
}
/**
 *  Cancel the sole parsing reception and disable duplicate cancellation; refresh server state after the original operation completes, with no retries.
 */
function rmCancel() { if (rmController) { rmController.abort(); rmEl("cancel-parse").disabled = true; } }
/**
 *  After language change, synchronize partition titles, cards, pagination, and edit/attachment status text; do not re-read or rewrite resume content.
 */
function rmLanguage() {
  rmWorkspaceHash();
  rmRenderRecommendations();
  rmRender();
  rmEl("editor-state").textContent = rmSuggestedCount ? rmText("re_suggestions", { count: rmSuggestedCount }) : rmText(rmEditorDirty ? "re_unsaved" : rmEditorBase ? "re_editor_loaded" : "re_editor_empty");
  rmEl("uploaded-file-name").textContent = rmAttachment ? rmAttachment.original_name + " · " + rmText("rm_status_" + rmAttachment.status) : rmText("re_upload_empty");
}
/**
 *  Initialize upload visibility and load the list/current page; allow explicit refresh after failure, without automatic retry.
 */
async function rmInit() {
  rmSource(); rmClearEditor(); rmLanguage();
  /**
 *  Initially read the saved current; if no current exists, maintain empty edit prompt, without silently selecting another version.
 */
  async function load() {
    await rmLoad();
    for (const version of rmVersions) if (version.is_current && version.status === "ready") { await rmPreview(version.id); break; }
  }
  await rmRun(load);
}

rmEl("upload-form").addEventListener("submit", rmUpload);
rmEl("choose-file").addEventListener("click", rmChooseFile);
rmEl("resume-file").addEventListener("change", rmFileSelected);
rmEl("profile-form").addEventListener("submit", rmSaveProfile);
rmEl("edition-form").addEventListener("submit", rmSaveEdition);
rmEl("uploaded-parse").addEventListener("click", rmParseUploaded);
rmEl("edition-current").addEventListener("click", rmUseEdition);
rmEl("recommend-jobs").addEventListener("click", rmRecommend);
rmEl("recommendation-resume").addEventListener("change", rmChooseResume);
rmEl("recommendation-review").addEventListener("click", rmReviewRecommendation);
rmEl("recommendation-parse").addEventListener("click", rmParseUploaded);
rmEl("edition-label").addEventListener("input", rmDirty);
for (const key of RM_UNITS) rmEl("unit-" + key).addEventListener("input", rmDirty);
for (const key of Object.keys(RM_SLOTS)) {
  rmEl("slot-" + key).addEventListener("input", rmDirty);
  rmEl("slot-" + key).addEventListener("change", rmDirty);
}
document.querySelector(".section-navigation").addEventListener("click", rmSection);
rmEl("upload-kind").addEventListener("change", rmSource);
rmEl("parse-mode").addEventListener("change", rmControls);
rmEl("versions-list").addEventListener("click", rmAction);
rmEl("refresh-versions").addEventListener("click", rmRefresh);
rmEl("previous-page").addEventListener("click", rmPrevious);
rmEl("next-page").addEventListener("click", rmNext);
rmEl("cancel-parse").addEventListener("click", rmCancel);
for (const selector of document.querySelectorAll("[data-language-selector]")) selector.addEventListener("change", rmLanguage);
window.addEventListener("languagechange", rmLanguage);
window.addEventListener("pagehide", rmCancel);
window.addEventListener("beforeunload", rmBeforeLeave);
for (const link of document.querySelectorAll("[data-workspace-link]")) link.addEventListener("click", rmWorkspaceLink);
window.addEventListener("hashchange", rmWorkspaceHash);
rmWorkspaceHash();
rmInit();
