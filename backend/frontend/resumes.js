/**
 * @module resumes
 * 职责：维护基本资料、原件、单元编辑稿和推荐槽位；保存独立版本后显式推荐岗位，保护未保存修改。
 * 实现：PDF 选择后一次上传，解析仍显式触发；推荐区直接选择全部版本并提示解析/核对/保存；
 * 单操作锁及 CSRF 保护写入；完整 NDJSON 后读持久化详情；哈希分区切换只改变可见性，不清空编辑稿。
 * 关联：resumes.html、i18n.js、/api/resume-versions/ 及其 recommendations 动作；Django session 认证。
 * 目录：
 * - rmWorkspacePanel：按已有锚点映射页面分区。
 * - rmReveal：显示目标分区并同步标题/导航，可显式更新哈希，不读取或保存资料。
 * - rmWorkspaceHash：响应初始 URL 和浏览器前进/后退。
 * - rmWorkspaceLink：拦截同页顶部入口，保留未保存草稿。
 * - rmScroll：先显示目标面板再平滑定位，避免滚动隐藏内容。
 * - rmEl：取得模板元素。
 * - rmText：读取已加载的国际化文案。
 * - rmStatus：显示纯文本操作状态。
 * - rmControls：根据操作与分页状态切换控件。
 * - rmRecommend：仅显式使用所选已保存版本请求岗位推荐，不提交未保存简历。
 * - rmRecommend.recommend：调用本人推荐动作，失败保留明确来源/粗排/精排故障提示。
 * - rmRenderRecommendations：安全展示 LLM 顺序、双语理由、岗位来源和技能交集，不把分数转换成概率。
 * - rmRenderResumePicker：按已读取的完整版本元数据重绘推荐选择器，标注状态和当前版本。
 * - rmChooseResume：用户明确切换推荐版本，沿用未保存确认和详情读取，不修改 current。
 * - rmChooseResume.choose：在操作锁内加载所选持久化版本。
 * - rmReviewRecommendation：展开技能及推荐字段并移动焦点，不修改或保存资料。
 * - rmHasRecommendationDetails：检查已加载槽位是否至少有一项已知，不填补未知值。
 * - rmSource：切换互斥上传表单。
 * - rmChooseFile：打开原生选择器，允许重新选择相同文件，不发送请求。
 * - rmFileSelected：有明确文件选择时保存原件，取消选择不创建版本。
 * - rmRequest：发同源请求，统一认证及业务错误，保留流响应。
 * - rmRun：串行执行操作，显示失败且释放 UI 状态。
 * - rmButton：生成命名版本动作按钮。
 * - rmRender：同步全版本选择器并构造当前页历史卡片，不插入 HTML。
 * - rmLoad：读取历史当前页及其他页元数据，完整成功后更新全版本选择器和历史分页。
 * - rmPreview：查询版本和编辑契约，显示原文及在线单元，返回持久化记录。
 * - rmClearEditor：清空编辑状态并禁用操作，不删除已保存版本。
 * - rmFillEditor：回填单元/确认值及原件待确认建议，标注证据并保护未保存建议，原文只读。
 * - rmDirty：记录单元/槽位输入，清除保存状态。
 * - rmConfirmDiscard：显式确认离开未保存编辑，取消时保持当前内容。
 * - rmReadSlots：读取严格推荐值，不推断缺失字段。
 * - rmSaveEdition：组装单元和槽位，保存独立编辑快照。
 * - rmSaveEdition.save：创建快照后读取新版本，不覆盖原件。
 * - rmParseUploaded：就近解析当前待解析附件。
 * - rmParseUploaded.parse：在操作锁内执行一次明确解析。
 * - rmUseEdition：将已保存且未被编辑的版本设为当前。
 * - rmUseEdition.select：持久化本人当前版本选择。
 * - rmSection：先显示侧栏目标分区，再展开编辑单元并定位。
 * - rmBeforeLeave：未保存修改时触发浏览器离开保护。
 * - rmUpload.save：成功创建后清空上传输入并展示新版本。
 * - rmRefresh.refresh：读取列表和现有选择。
 * - rmPrevious.previous：按编号请求上一页。
 * - rmNext.next：按编号请求下一页。
 * - rmAction.act：在操作锁内执行唯一已确认动作。
 * - rmUpload：保存一个 PDF 或文本版本，随后展示新版本；ready 文本打开编辑分区。
 * - rmSaveProfile：校验表单事件后提交本人姓名/邮箱。
 * - rmSaveProfile.save：PATCH 成功后回填服务端资料并显示保存状态。
 * - rmRefresh：显式刷新当前页与所选详情。
 * - rmPrevious：加载上一页。
 * - rmNext：加载下一页。
 * - rmEvent：处理进度、错误和成功终态，返回疑点数组或 null。
 * - rmParse：消费提取流，完整成功才显示编辑区并声明完成，不重试或采用中间文本。
 * - rmAction：按已加载版本分派预览、当前选择及确认删除；解析只在附件区触发。
 * - rmCancel：取消当前流接收，不承诺外部模型立即停止。
 * - rmLanguage：语言改变时同步分区标题并重绘版本列表，保留当前正文。
 * - rmInit.load：读取列表并打开当前页的已保存 current，没有时保留选择提示。
 * - rmInit：加载初始列表，失败时保留可刷新界面。
 * 关键变量：
 * - RM_WORKSPACE_SECTIONS：分区对应的既有 DOM 元素；附件与版本历史同屏。
 * - RM_WORKSPACE_TITLES：分区标题翻译键，不影响业务状态。
 * - RM_API：同源版本接口前缀。
 * - RM_PROFILE：本人资料同源接口。
 * - RM_UNITS：稳定单元 ID，与后端契约一致。
 * - RM_SLOTS：推荐字段输入类型，与 CandidateInput 保持一致。
 * - rmEl：元素查询函数。
 * - rmBusy：唯一操作锁。
 * - rmController：当前解析 AbortController；页面离开终止接收。
 * - rmVersions：历史当前页元数据，无文件字节。
 * - rmChoiceVersions：推荐选择器的全部分页元数据，无正文或文件字节。
 * - rmPage：当前页编号，从 1 开始。
 * - rmNextPage：服务端声明是否有下一页。
 * - rmSelected：内存中预览版本 ID；不写浏览器存储或隐式设置 current。
 * - rmAttachment：最近选择/上传的原件元数据，用于就近解析。
 * - rmEditorBase：正在编辑的已保存基线版本 ID。
 * - rmEditorDirty：是否有未保存的单元或槽位修改。
 * - rmInitialSlots：表单的确认值与待确认建议；保留未编辑的显式空数组，保存才写入新版本。
 * - rmSlotTouched：被人工修改的槽位名称集合。
 * - rmSuggestedCount：本次载入的待确认建议数量；首次手动修改或重置后清零。
 * - rmRecommendations：所选已保存版本的排序响应；编辑或切换后清空。
 * - rmRecommendationError：推荐区当前故障文案 key；成功或切换时清空。
 * - RM_RECOMMENDATION_ERRORS：稳定服务故障码到中英文文案的允许列表。
 * 约束：
 * 用户文本仅经 value/textContent 显示；每次保存生成新版本，不覆盖原件或改变失败语义。
 */
const RM_WORKSPACE_SECTIONS = {
  files: ["resume-files", "version-history"], editor: ["resume-editor"], jobs: ["job-recommendations"], profile: ["profile-panel"],
};
const RM_WORKSPACE_TITLES = { files: "ws_resumes", editor: "re_editor_heading", jobs: "ws_jobs", profile: "rm_account" };
/** 输入已有 DOM 锚点，输出分区名；未知或空 URL 显示附件，不修改版本选择或模型参数。 */
function rmWorkspacePanel(id) {
  if (id === "job-recommendations") return "jobs";
  if (id === "profile-panel") return "profile";
  if (id === "resume-editor" || (id.startsWith("section-") && RM_UNITS.includes(id.slice(8)))) return "editor";
  return "files";
}
/** 输入锚点及是否更新 URL；只改 hidden、标题与 aria-current，不销毁表单或发请求。
 * URL 使用 replaceState，保留显式浏览器哈希导航；anchor 标记唯一侧栏主入口，不将正文放入地址或日志。
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
/** 无外部输入；读取当前 URL 哈希并同步分区/编辑单元展开，支持刷新、分享和浏览器历史。 */
function rmWorkspaceHash() {
  const id = window.location.hash.slice(1);
  rmReveal(id);
  if (id.startsWith("section-") && RM_UNITS.includes(id.slice(8))) rmEl(id).open = true;
}
/** 输入顶部同页入口点击；保留新标签页快捷键，普通点击取消页面重载并显示目标，保留所有输入及离开保护状态。 */
function rmWorkspaceLink(event) {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); rmReveal(event.currentTarget.dataset.workspaceLink, true);
}
/** 输入已存在元素 ID；先展开所在分区再滚动，用户数据和焦点由调用者处理。 */
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
/** 输入模板 ID，返回 DOM 节点；缺失节点明确暴露模板契约错误。 */
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

/** 输入翻译 key 与参数，返回当前语言纯文本；i18n.js 是模板显式先加载的依赖。 */
function rmText(key, values = {}) { return window.AppI18n.t(key, values); }
/** 输入状态与错误标志，输出文本和颜色；不记录用户正文或服务器响应到日志。 */
function rmStatus(message, error = false) {
  rmEl("operation-status").textContent = message;
  rmEl("operation-status").classList.toggle("error", error);
}
/** 读取页面状态设置 disabled/aria-busy；允许取消解析但不允许并发写入。 */
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
/** 读取用户选项，仅显示对应文件/文本输入；不清空另一输入或发送请求。 */
function rmSource() {
  const pdf = rmEl("upload-kind").value === "pdf";
  rmEl("pdf-input").hidden = !pdf;
  rmEl("text-input").hidden = pdf;
  rmEl("upload-submit").hidden = pdf;
}
/** 无外部参数；空置原生输入以允许选择同一文件；忙碌时不打开，不写数据库或自动重试。 */
function rmChooseFile() {
  if (rmBusy) return;
  rmEl("resume-file").value = "";
  rmEl("resume-file").click();
}
/** 输入文件选择 change 事件；非空且 PDF 模式才走既有上传校验/CSRF，取消不丢弃在线稿或解析。 */
async function rmFileSelected(event) {
  if (rmBusy || rmEl("upload-kind").value !== "pdf" || !rmEl("resume-file").files.length) return;
  await rmUpload(event);
}
/** 输入接口相对路径、fetch 参数及内部指定 base，返回响应；写操作带 CSRF，错误明确抛出。 */
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
/** 输入无参数异步操作；单操作执行，异常展示并记录类型，finally 释放全部控件。 */
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
/** 输入动作、ID 和翻译 key，返回 type=button 的安全 DOM 节点，不触发动作。 */
function rmButton(action, id, key) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = action === "delete" ? "secondary danger" : "secondary";
  button.dataset.action = action;
  button.dataset.id = id;
  button.textContent = rmText(key);
  return button;
}
/** 读取完整选择元数据、当前历史页及预览 ID，同步选项/卡片；下载仅用 UUID，不解释用户 HTML。 */
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
/** 输入历史页编号；读取全部页元数据以完整列出可选版本，复用目标页响应，不改变后端分页。
 * 所有请求成功才替换列表；失败交给操作锁显示，不截断或回退。仅选择后读取正文和编辑契约。
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
/** 输入本人版本 ID 与内部已确认标志，返回详情；切换前确认未保存内容，ready 再读取单元契约，失败不冒充旧结果。 */
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
/** 清空未保存状态和下载指针，禁用编辑；调用前由动作入口确认丢弃，不删除服务端数据。 */
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
/** 输入授权编辑响应；确认值优先，原件有依据的建议填入待保存草稿并展示证据，编辑稿不重新提取。
 * 自动建议未保存时禁用设 current 并保护离开；未知保持空白，确认过的 0/false/[] 不覆盖。
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
/** 输入真实 input/change 事件；标记人工改动，推荐字段本次清空表示未知，不进行自动抽取。 */
function rmDirty(event) {
  if (!rmEditorBase) return;
  rmRecommendations = null; rmRecommendationError = null;
  rmEditorDirty = true;
  rmSuggestedCount = 0;
  if (event.currentTarget.dataset.slot) rmSlotTouched.add(event.currentTarget.dataset.slot);
  rmEl("editor-state").textContent = rmText("re_unsaved");
  rmControls();
}
/** 读取未保存状态并返回是否允许切换；只有明确确认才丢弃工作区，无自动保存或重试。 */
function rmConfirmDiscard() {
  if (!rmEditorDirty) return true;
  if (!window.confirm(rmText("re_discard"))) return false;
  return true;
}
/** 输出 CandidateInput 的可选字段；逗号标签保持大小写，数字严格校验，空白未知不补零。 */
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
/** 输入编辑表单事件；校验后保存独立新稿，不覆盖原件/历史或自动设 current。
 * submitter 为推荐区保存按钮时，成功后返回推荐区；其他保存入口保持原编辑位置。
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
  /** 成功 POST 后才清除未保存状态，读取新稿作为下一次编辑基线；失败保留原输入。 */
  async function save() {
    const version = await (await rmRequest(encodeURIComponent(base) + "/editions/", { method: "POST", headers: { "Content-Type": "application/json" }, body })).json();
    rmEditorDirty = false;
    await rmLoad(1); await rmPreview(version.id, true);
    rmStatus(rmText("re_saved"));
    if (returnToRecommendations) rmScroll("job-recommendations");
  }
  await rmRun(save);
}
/** 输入可选按钮事件；只解析当前 uploaded 附件，明确确认未保存稿，不重复解析 ready/failed。
 * 推荐区触发时成功后返回该区；附件原入口保留原位置，不改变解析模式或供应商调用。
 */
async function rmParseUploaded(event) {
  if (rmBusy || rmAttachment?.status !== "uploaded" || !rmConfirmDiscard()) return;
  const id = rmAttachment.id;
  // 原生事件在异步等待后清空 currentTarget；提前保存入口标志以保持解析后的导航位置。
  const returnToRecommendations = event?.currentTarget?.id === "recommendation-parse";
  /** 保持全页写操作锁，复用一次显式解析的流消费。 */
  async function parse() { await rmParse(id); }
  await rmRun(parse);
  if (returnToRecommendations && rmEditorBase) rmScroll("job-recommendations");
}
/** 只有已保存且未再次改动的版本可设 current；改动需先保存，避免把未保存正文误认为已采用。 */
async function rmUseEdition() {
  if (rmBusy || !rmEditorBase || rmEditorDirty) return;
  const id = rmEditorBase;
  /** POST 当前选择后刷新元数据，不清空用户已保存编辑内容。 */
  async function select() {
    await rmRequest(encodeURIComponent(id) + "/current/", { method: "POST" });
    await rmLoad(); rmStatus(rmText("rm_selected"));
  }
  await rmRun(select);
}
/** 无外部输入；仅本人已保存且未改动的版本允许请求，CSRF 与全页串行锁沿用版本接口。 */
async function rmRecommend() {
  if (rmBusy || !rmEditorBase || rmEditorDirty || !rmHasRecommendationDetails()) return;
  const id = rmEditorBase;
  rmRecommendations = null; rmRecommendationError = null;
  /** 读取保存快照的真实排序；异常展示稳定文案并交给操作锁记录，不重试或保留过期结果。 */
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
/** 读取已确认/待确认槽位；至少一个非 null/undefined 项表示已知，[]/0/false 保持既有语义。 */
function rmHasRecommendationDetails() {
  for (const key of Object.keys(RM_SLOTS)) if (rmInitialSlots[key] !== null && rmInitialSlots[key] !== undefined) return true;
  return false;
}
/** 输入全部元数据及当前预览 ID；生成纯文本选项，包含状态、保存时间和 current 标记。
 * 不自动选择最新稿，不改变当前面试简历；语言和选中值同步，所有正文仍按用户选择加载。
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
/** 输入用户 change 事件；取消丢弃时还原选择器，批准后串行读取所选详情，不请求推荐或自动保存。 */
async function rmChooseResume(event) {
  const id = event.currentTarget.value;
  if (rmBusy || !id || id === rmSelected || !rmConfirmDiscard()) { rmRenderResumePicker(); return; }
  /** 已确认切换后加载同一预览/编辑路径，维持保存和权限边界。 */
  async function choose() {
    await rmPreview(id, true);
    rmScroll("job-recommendations");
  }
  await rmRun(choose);
  if (rmSelected === id) rmEl("recommendation-resume").focus({ preventScroll: true });
}
/** 用户显式核对时展开技能和可选推荐字段，滚动及聚焦；不改内容、不进行推断或 API 写入。 */
function rmReviewRecommendation() {
  if (rmBusy || !rmEditorBase) return;
  rmEl("section-skills").open = true;
  const details = rmEl("slot-skills").closest(".recommendation-fields");
  if (details) details.open = true;
  rmScroll("section-skills");
  rmEl("slot-skills").focus({ preventScroll: true });
}
/** 读取当前选择、保存状态和服务响应，输出下一步动作和纯 DOM 岗位卡。
 * messageKey 按故障、待保存、未就绪、未知资料、可推荐顺序决定提示；最终名次来自 LLM。
 * 原分数不作概率；理由按当前语言以 textContent 输出，语言切换不发 API 请求。
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
/** 输入侧栏点击事件；先显示目标分区，再展开指定编辑单元并滚动；保留新标签页快捷键，不修改文本或请求模型。 */
function rmSection(event) {
  const link = event.target.closest("a[href^='#']");
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const id = link.getAttribute("href").slice(1);
  if (id.startsWith("section-") && RM_UNITS.includes(id.slice(8))) rmEl(id).open = true;
  rmScroll(id);
}
/** 输入 beforeunload 事件；有未保存文本才启用浏览器原生离开提示，不在本地存储内容。 */
function rmBeforeLeave(event) { if (rmEditorDirty) { event.preventDefault(); event.returnValue = ""; } }
/** 输入文本提交或 PDF 选择事件；验证后保存一次原件，成功才清空输入，ready 文本显示编辑区，不自动解析或切换 current。 */
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
  /** 请求成功后才清空输入；正文预览从服务端读取，不把客户端缓存当持久化成功。 */
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
/** 输入资料表单事件；仅读取姓名和邮箱，复用操作锁，失败保留填写内容，不修改账号身份。 */
async function rmSaveProfile(event) {
  event.preventDefault();
  if (rmBusy) return;
  const body = JSON.stringify({ first_name: rmEl("profile-name").value.trim(), email: rmEl("profile-email").value.trim() });
  /** 提交本人资料并用响应回填字段，只有保存成功才显示完成；不自动重试。 */
  async function save() {
    rmStatus(rmText("rm_profile_saving"));
    const data = await (await rmRequest("", { method: "PATCH", headers: { "Content-Type": "application/json" }, body }, RM_PROFILE)).json();
    rmEl("profile-name").value = data.first_name;
    rmEl("profile-email").value = data.email;
    rmStatus(rmText("rm_profile_saved"));
  }
  await rmRun(save);
}
/** 显式刷新，不自动重放任何写操作；详情按原选择更新，外部删除错误仍明确展示。 */
async function rmRefresh() {
  if (!rmConfirmDiscard()) return;
  /** 刷新列表及已选详情，所有响应来自真实授权接口。 */
  async function refresh() { await rmLoad(); if (rmSelected) await rmPreview(rmSelected, true); rmStatus(rmText("rm_refreshed")); }
  await rmRun(refresh);
}
/** 向上一页移动；按钮与函数双重检查边界，失败保持当前页。 */
async function rmPrevious() {
  /** 使用当前页编号请求上一页，无写入。 */
  async function previous() { await rmLoad(rmPage - 1); }
  if (rmPage > 1) await rmRun(previous);
}
/** 仅服务端存在 next 时读取下一页，不跟随任意 URL。 */
async function rmNext() {
  /** 使用编号构造本地下一页查询，不依赖外部地址。 */
  async function next() { await rmLoad(rmPage + 1); }
  if (rmNextPage) await rmRun(next);
}
/** 输入解析事件，显示进度/必要疑点；错误抛出且不采用文本，result 返回疑点数组，其余返回 null。 */
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
/** 输入 uploaded 版本 ID；用当前模式解析，严格消费 UTF-8 NDJSON；取消/失败保持服务端失败语义。 */
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
/** 输入列表点击事件，预览显示编辑区且只操作已加载 ID；删除需明确确认，引用冲突由后端保持 409。 */
async function rmAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button || rmBusy) return;
  const { action, id } = button.dataset;
  let version = null;
  for (const item of rmVersions) if (item.id === id) version = item;
  if (!version) return;
  if ((action === "preview" || action === "current" || (action === "delete" && rmSelected === id)) && !rmConfirmDiscard()) return;
  if (action === "delete" && !window.confirm(rmText("rm_delete_confirm"))) return;
  /** 在操作锁内调用唯一明确动作；不引入自动选择、解析重试或删除绕过。 */
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
/** 取消唯一解析接收并禁用重复取消；服务端状态刷新在原操作结束后执行，无重试。 */
function rmCancel() { if (rmController) { rmController.abort(); rmEl("cancel-parse").disabled = true; } }
/** 界面语言变化后同步分区标题、卡片、分页与编辑/附件状态文字，不重新读取或改写简历内容。 */
function rmLanguage() {
  rmWorkspaceHash();
  rmRenderRecommendations();
  rmRender();
  rmEl("editor-state").textContent = rmSuggestedCount ? rmText("re_suggestions", { count: rmSuggestedCount }) : rmText(rmEditorDirty ? "re_unsaved" : rmEditorBase ? "re_editor_loaded" : "re_editor_empty");
  rmEl("uploaded-file-name").textContent = rmAttachment ? rmAttachment.original_name + " · " + rmText("rm_status_" + rmAttachment.status) : rmText("re_upload_empty");
}
/** 初始化上传显隐并加载列表/当前页 current；允许失败后显式刷新，不自动重试。 */
async function rmInit() {
  rmSource(); rmClearEditor(); rmLanguage();
  /** 初始读取已保存 current；没有 current 时保持空编辑提示，不静默选择其他版本。 */
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
