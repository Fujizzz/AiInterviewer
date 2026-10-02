/**
 * @module resumes-client-test
 * 职责：验证真实简历管理客户端的数据流和失败边界，不请求外部服务或生产数据库。
 * 实现：实际模板 ID、最小 DOM、真实 Response/ReadableStream/UTF-8 解码与显式 REST 替身。
 * 关联：frontend/resumes.js、resumes.html；不能替代真实浏览器布局、供应商或权限测试。
 * 目录：
 * - Element：仅实现客户端使用的 DOM 接口。
 * - Element.constructor：初始化控件与可观察子节点。
 * - Element.constructor.toggle：维护模拟 CSS class 集合。
 * - Element.addEventListener：登记事件。
 * - Element.click：记录原生选择器触发，不读取磁盘或创建文件。
 * - Element.focus：记录客户端主动焦点目标，不模拟浏览器布局。
 * - Element.scrollIntoView：记录客户端导航目标，不模拟滚动距离。
 * - Element.append：追加纯 DOM 节点。
 * - Element.replaceChildren：清除旧卡片。
 * - Element.querySelectorAll：递归返回按钮。
 * - Element.setAttribute：保存 aria 状态。
 * - Element.closest：测试按钮返回自身。
 * - makePage：执行实际客户端并返回隔离页面与 REST 状态。
 * - makePage.getElement：拒绝查询模板以外的元素。
 * - makePage.createElement：创建模拟 DOM 节点。
 * - makePage.fetch：记录请求、断言 CSRF 并执行模拟版本接口。
 * - makePage.fetch.byOriginal：按来源读取合成原件。
 * - makePage.fetch.byId：按 URL 版本 ID 匹配合成记录。
 * - makePage.sectionNavigation：返回合成侧栏节点。
 * - makePage.selectors：语言选择器为空，避免依赖国际化模块。
 * - makePage.t：返回可断言的翻译 key 与参数。
 * - makePage.confirm：显式批准模拟删除确认，无真实删除。
 * - makePage.ignore：替代窗口事件和日志，不访问用户设备。
 * - makePage.run：调用实际客户端函数。
 * - json：生成 JSON HTTP 响应。
 * - stream：构建逐字节 NDJSON，验证跨字节中文解码。
 * - stream.start：推送字节并结束流。
 * - record：生成虚构的完整版本元数据。
 * - textUpload：验证文本保存不调用解析且正文安全显示。
 * - textUpload.isParse：筛选解析请求。
 * - pdfUpload：验证 PDF 大小边界、multipart 上传和保存后不自动解析。
 * - pdfModes：验证 PDF 保存与默认/高级两种显式解析模式。
 * - pdfModes.isParse：定位显式提取请求。
 * - failedStream：验证失败不采用模型中间正文、不自动重试。
 * - failedStream.isParse：统计提取次数。
 * - incompleteStream：验证缺失终态不能宣布成功。
 * - currentAndDelete：验证当前选择、保护冲突、确认删除与清理预览。
 * - editionSave：验证单元保存的独立性及推荐值类型。
 * - dirtyCancel.page.context.window.confirm：拒绝合成丢弃确认。
 * - dirtyCancel：验证取消离开保留输入且不发送当前选择请求。
 * - nearbyParse：验证附件就近按钮只在上传后解析一次。
 * - singleUploadEntry：验证选择上传、取消、忙碌锁及按状态显示的解析工具。
 * - fileUploadFailure：上传失败仍保留未保存编辑，且不隐式重试或解析。
 * - fileUploadFailure.rejectUpload：模拟仅原件 POST 失败，其他读取仍走 REST 替身。
 * - preventDefault：替代文件选择测试事件的默认行为取消，不操作浏览器。
 * - suggestedSlots：建议回填待保存草稿，确认值不覆盖，人工清空不重新提取。
 * - failedSave：保存失败保留单元输入与离开保护，显式空数组不变成未知。
 * - failedSave.rejectEdition：模拟保存 HTTP 400，不重试其他写操作。
 * - invalidSlots：非法数值不发送保存请求。
 * - profileSave：本人资料只用 PATCH 和 CSRF 保存，回填服务端结果。
 * - savedRecommendations：显式请求保存版本，安全展示岗位，不自动推荐；编辑后清除结果。
 * - recommendationFailure：来源故障不生成替代卡片，不自动重试，并恢复操作控件。
 * - recommendationReasons：双语理由安全展示，语言切换不再次请求，最终顺序不改变。
 * - makePage.language：读取合成语言状态，不调用真实浏览器语言。
 * - recommendationSelection：跨历史分页完整选择 ready 版本，不自动推荐或修改 current。
 * - recommendationGuidance：无简历、待解析和缺少推荐字段时显示对应下一步动作。
 * - recommendationUnsaved：取消版本切换保留输入/选择，显示就近保存按钮。
 * - recommendationUnsaved.rejectSwitch：模拟用户拒绝丢弃未保存内容。
 * 关键变量：
 * - SCRIPT：实际客户端源码。
 * - HTML：实际模板，用于验证元素契约。
 * 约束：
 * makePage 的 items/requests/errors 仅保存合成数据；真实服务权限由 Django 测试覆盖。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const SCRIPT = readFileSync(new URL("../frontend/resumes.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/resumes.html", import.meta.url), "utf8");
/** 无参数、无返回值；文件选择测试事件的无副作用替身，不模拟浏览器导航。 */
function preventDefault() {}

/** 功能：最小 DOM 状态；逻辑：只实现真实脚本依赖；约束：无布局或浏览器权限模拟。 */
class Element {
  /** 输入标签名，输出空节点状态；classList 使用真实 Set 以观察错误标记。 */
  constructor(tag = "div") {
    this.tagName = tag; this.value = ""; this.textContent = ""; this.disabled = false;
    this.files = []; this.dataset = {}; this.children = []; this.listeners = {}; this.attributes = {};
    this.classList = new Set();
    /** 输入类名和布尔值，以调用者 Set 为状态；无浏览器副作用。 */
    function toggle(key, enabled) { if (enabled) this.add(key); else this.delete(key); }
    this.classList.toggle = toggle;
  }
  /** 输入事件与处理器，保存不自行执行。 */
  addEventListener(name, handler) { this.listeners[name] = handler; }
  /** 无输入；记录 input.click 的选择器请求，不模拟系统选中文件或触发 change。 */
  click() { this.clicked = true; }
  /** 记录主动焦点请求；输入可选浏览器参数，无真实页面副作用。 */
  focus(options) { this.focused = options; }
  /** 记录滚动定位；输入可选浏览器参数，不模拟尺寸或动画。 */
  scrollIntoView(options) { this.scrolled = options; }
  /** 输入节点列表，保存子节点，不解释字符串为 HTML。 */
  append(...nodes) { this.children.push(...nodes); }
  /** 清除全部子节点；不删除模板元素。 */
  replaceChildren() { this.children = []; }
  /** 输入选择器，仅本脚本使用 button；输出递归按钮集合。 */
  querySelectorAll(selector) {
    assert.equal(selector, "button");
    const result = [];
    for (const child of this.children) { if (child.tagName === "button") result.push(child); result.push(...child.querySelectorAll(selector)); }
    return result;
  }
  /** 保存属性字符串，以检查 aria-busy。 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /** 返回本节点供动作委托与建议面板观察；不模拟真实祖先选择或布局。 */
  closest() { return this; }
}
/** 输入数据与状态码，输出真实 Response；模拟接口 JSON 编码。 */
function json(data, status = 200) { return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } }); }
/** 输入事件数组，输出真实逐字节 UTF-8 流；不访问网络。 */
function stream(events) {
  const bytes = new TextEncoder().encode(events.map(JSON.stringify).join("\n") + "\n");
  /** 输入流控制器，逐字节入队以验证 TextDecoder 边界处理。 */
  function start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); }
  return new Response(new ReadableStream({ start }));
}
/** 输入 ID/status，输出仅供测试的元数据与正文；不使用真实候选人数据。 */
function record(id, status = "ready") {
  return { id, status, label: "<script>label</script>", original_name: status === "uploaded" ? "sample.pdf" : "", extraction_mode: "", error_code: "", is_current: false, created_at: "2026-10-01T00:00:00Z", text: "中文 <img src=x> 简历" };
}
/** 输入初始记录、流事件与可选分页大小，输出真实脚本 VM 和请求；分页仅为明确接口替身。
 * pageSize 为 null 时保留旧单页 fixture；正数模拟服务端分段，不改变生产分页参数。
 */
async function makePage(initial = [], events = [{ type: "result", text: "中文提取", pages: [] }], pageSize = null) {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  elements.get("csrf-token").content = "synthetic-csrf";
  elements.get("parse-mode").value = "traditional"; elements.get("upload-kind").value = "pdf";
  const items = initial; const requests = []; const errors = [];
  /** 输入 ID，只允许真实模板元素，缺失明确失败。 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /** 输入标签，输出无窗口的 DOM 节点。 */
  function createElement(tag) { return new Element(tag); }
  /** 输入路径/参数，验证写操作带 CSRF；模拟版本 CRUD、提取及显式个人推荐，不运行模型。 */
  async function fetch(url, options) {
    requests.push({ url, ...options });
    const method = options.method || "GET";
    if (method !== "GET") assert.equal(options.headers.get("X-CSRFToken"), "synthetic-csrf");
    assert.equal(options.credentials, "same-origin");
    if (url === "/api/profile/") {
      assert.equal(method, "PATCH");
      return json({ username: "synthetic", ...JSON.parse(options.body) });
    }
    const path = url.replace("/api/resume-versions/", "");
    if (method === "GET" && path.startsWith("?")) {
      const page = Number(new URLSearchParams(path).get("page"));
      const size = pageSize || items.length || 1;
      return json({ count: items.length, results: items.slice((page - 1) * size, page * size), next: page * size < items.length ? "?page=" + (page + 1) : null });
    }
    if (method === "POST" && path === "") {
      const item = record("new", options.body instanceof FormData ? "uploaded" : "ready");
      if (!(options.body instanceof FormData)) item.text = JSON.parse(options.body).text;
      items.unshift(item); return json(item, 201);
    }
    const item = items.find(byId);
    /** 输入元数据，按请求的第一段 ID 精确匹配。 */
    function byId(value) { return value.id === path.split("/")[0]; }
    assert.ok(item, path);
    if (method === "POST" && path.endsWith("recommendations/")) return json(item.recommendation_response, item.recommendation_status || 200);
    if (method === "GET" && path.endsWith("editor/")) {
      const original = items.find(byOriginal) || item;
      /** 输入合成记录，只按编辑稿来源匹配原件。 */
      function byOriginal(value) { return value.id === item.source_version; }
      return json({ ...item, units: item.units || { other: item.text }, slots: item.slots || {}, slot_suggestions: item.slot_suggestions || null, slot_units: {skills:"skills",gpa:"education",commit_to_summer:"preferences",months_experience:"experience",num_publications:"publications"}, source_version: original.id, original_name: original.original_name, original_text: original.text });
    }
    if (method === "GET") return json(item);
    if (path.endsWith("editions/")) {
      const input = JSON.parse(options.body);
      const edition = { ...record("edition-" + items.length), ...input, source_version: item.source_version || item.id, edited_from: item.id };
      items.unshift(edition); return json(edition, 201);
    }
    if (path.endsWith("parse/")) {
      item.status = events.at(-1)?.type === "result" ? "ready" : "failed";
      if (item.status === "ready") item.text = events.at(-1).text;
      return stream(events);
    }
    if (path.endsWith("current/")) { for (const value of items) value.is_current = value.id === item.id; return json(item); }
    if (method === "DELETE") {
      if (item.protected) return json({ code: "resume_in_use" }, 409);
      items.splice(items.indexOf(item), 1); return new Response(null, { status: 204 });
    }
    assert.fail(path);
  }
  /** 输入 key/参数，返回可断言的纯文本；不证明英文资源质量。 */
  function t(key, values = {}) { return key + JSON.stringify(values); }
  /** 批准模拟页面删除对话框；不向真实服务发请求。 */
  function confirm() { return true; }
  /** 接收模拟日志/监听器注册，不操作真实窗口。 */
  function ignore(...args) { errors.push(args); }
  /** 无语言模块，返回空选择器集合；不模拟 HTML 布局。 */
  function selectors() { return []; }
  /** 输入侧栏选择器；返回单一合成节点以验证事件注册。 */
  function sectionNavigation(selector) { assert.equal(selector, ".section-navigation"); return new Element(); }
  /** 无外部参数；读取合成页面语言，用于验证双语理由，无网络副作用。 */
  function language() { return context.document.documentElement.lang.startsWith("en") ? "en" : "zh"; }
  const context = vm.createContext({ document: { getElementById: getElement, createElement, querySelectorAll: selectors, querySelector: sectionNavigation, documentElement: { lang: "zh-CN" } }, window: { AppI18n: { t, language }, confirm, addEventListener: ignore }, console: { error: ignore }, fetch, Headers, FormData, Response, ReadableStream, TextEncoder, TextDecoder, AbortController, DOMException });
  await vm.runInContext(SCRIPT, context);
  /** 输入本地函数调用表达式，输出其返回值或 Promise；仅使用测试固定源码。 */
  function run(code) { return vm.runInContext(code, context); }
  return { elements, requests, items, run, errors, context };
}
/** 文本保存真实走 JSON，之后只预览持久化详情；HTML 字符保留为 textarea 文本，无提取调用。 */
async function textUpload() {
  const page = await makePage();
  page.elements.get("upload-kind").value = "text";
  page.elements.get("resume-text").value = "中文 <img src=x> 简历";
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.items[0].status, "ready");
  assert.equal(page.elements.get("preview-text").value, "中文 <img src=x> 简历");
  assert.equal(page.requests.filter(isParse).length, 0);
  /** 输入请求，判断是否显式提取调用。 */
  function isParse(request) { return request.url.endsWith("parse/"); }
  assert.equal(page.elements.get("upload-submit").disabled, false);
}
/** 空文件不发送请求；有效 PDF 仅创建 uploaded 版本，不隐式调用解析。 */
async function pdfUpload() {
  const page = await makePage();
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.requests.length, 1);
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"], { type: "application/pdf" })];
  await page.run("rmUpload({preventDefault(){}})");
  const request = page.requests[1];
  assert.ok(request.body instanceof FormData);
  assert.equal(await request.body.get("file").text(), "%PDF-synthetic");
  assert.equal(page.items[0].status, "uploaded");
  assert.equal(page.elements.get("preview-text").value, "");
}
/** 两种 PDF 模式均只在显式 parse 时提交 mode；跨字节中文结果需完整终态。 */
async function pdfModes() {
  for (const mode of ["traditional", "advanced"]) {
    const page = await makePage([record("pdf", "uploaded")]);
    assert.equal(page.elements.get("parse-mode").value, "traditional");
    page.elements.get("parse-mode").value = mode;
    await page.run("rmRun(function parse(){return rmParse('pdf')})");
    const request = page.requests.find(isParse);
    /** 输入已记录请求，定位唯一解析写入。 */
    function isParse(value) { return value.url.endsWith("parse/"); }
    assert.equal(JSON.parse(request.body).mode, mode);
    assert.equal(page.elements.get("preview-text").value, "中文提取");
    assert.match(page.elements.get("operation-status").textContent, /^rm_parsed/);
  }
}
/** 显式服务端错误不采用任何文本，不自动请求第二次 parse，并恢复按钮。 */
async function failedStream() {
  const page = await makePage([record("pdf", "uploaded")], [{ type: "progress", detail: "处理中" }, { type: "error", detail: "视觉校对失败" }]);
  await page.run("rmRun(function parse(){return rmParse('pdf')})");
  assert.equal(page.elements.get("preview-text").value, "");
  assert.equal(page.elements.get("operation-status").textContent, "视觉校对失败");
  assert.equal(page.elements.get("refresh-versions").disabled, false);
  assert.equal(page.requests.filter(isParse).length, 1);
  /** 输入请求，定位写入提取动作，不包括后续 GET 刷新。 */
  function isParse(value) { return value.url.endsWith("parse/"); }
}
/** HTTP 200 但缺失 result 的流不能变成成功文案。 */
async function incompleteStream() {
  const page = await makePage([record("pdf", "uploaded")], [{ type: "progress", detail: "处理中" }]);
  await page.run("rmRun(function parse(){return rmParse('pdf')})");
  assert.match(page.elements.get("operation-status").textContent, /^rm_incomplete/);
  assert.equal(page.elements.get("preview-text").value, "");
}
/** 当前选择真实发 POST；409 不清空数据，确认删除成功才清除所选正文。 */
async function currentAndDelete() {
  const item = record("text"); const page = await makePage([item]);
  await page.run("rmAction({target:{closest(){return {dataset:{action:'current',id:'text'}}}}})");
  assert.equal(item.is_current, true);
  assert.equal(page.elements.get("preview-text").value, item.text);
  item.protected = true;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'delete',id:'text'}}}}})");
  assert.equal(page.items.length, 1);
  assert.match(page.elements.get("operation-status").textContent, /^rm_in_use/);
  item.protected = false;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'delete',id:'text'}}}}})");
  assert.equal(page.items.length, 0);
  assert.equal(page.elements.get("preview-text").value, "");
}
/** 姓名与邮箱在个人中心保存；不修改用户名，不自动创建/解析简历。 */
async function profileSave() {
  const page = await makePage();
  page.elements.get("profile-name").value = " 姓名 ";
  page.elements.get("profile-email").value = " person@example.test ";
  await page.run("rmSaveProfile({preventDefault(){}})");
  assert.equal(page.requests[1].url, "/api/profile/");
  assert.equal(page.requests[1].method, "PATCH");
  assert.deepEqual(JSON.parse(page.requests[1].body), { first_name: "姓名", email: "person@example.test" });
  assert.equal(page.elements.get("profile-name").value, "姓名");
  assert.match(page.elements.get("operation-status").textContent, /^rm_profile_saved/);
  assert.equal(page.items.length, 0);
}
test("text upload saves and previews without implicit parsing", textUpload);
test("PDF upload validates size and saves without implicit extraction", pdfUpload);
test("PDF parsing preserves default and explicit advanced modes", pdfModes);
test("parse error never adopts text or retries", failedStream);
test("incomplete stream never announces success", incompleteStream);
test("current selection and protected deletion preserve version state", currentAndDelete);
test("profile information is maintained through the unified personal center", profileSave);

/** 保存真实客户端输入；REST 为替身，验证单元、严格槽位、来源、原文和下载路径，不验证服务写库。 */
async function editionSave() {
  const original = record("original");
  original.original_name = "original.pdf";
  const page = await makePage([original]);
  await page.run("rmPreview('original')");
  page.elements.get("unit-projects").value = "缓存项目\n改进 API <script>";
  page.elements.get("slot-skills").value = "Python, Django";
  page.elements.get("slot-months_experience").value = "0";
  page.elements.get("slot-num_publications").value = "0";
  page.elements.get("slot-commit_to_summer").value = "false";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  assert.equal(page.elements.get("edition-current").disabled, true);
  await page.run("rmSaveEdition({preventDefault(){}})");
  const saved = page.items[0];
  assert.equal(saved.units.projects, "缓存项目\n改进 API <script>");
  assert.deepEqual(saved.slots.skills, ["Python", "Django"]);
  assert.equal(saved.slots.months_experience, 0);
  assert.equal(saved.slots.commit_to_summer, false);
  assert.equal(saved.slots.academic_level, null);
  assert.equal(saved.source_version, "original");
  assert.equal(original.text, "中文 <img src=x> 简历");
  assert.equal(page.elements.get("preview-text").value, original.text);
  assert.equal(page.elements.get("unit-projects").value, saved.units.projects);
  assert.equal(page.elements.get("edition-download").href, "/api/resume-versions/" + saved.id + "/export/");
  assert.equal(page.run("rmAttachment.id"), original.id);
  assert.match(page.elements.get("uploaded-file-name").textContent, /^original.pdf/);
}
/** 用户拒绝丢弃修改时不切换当前版本或刷新，编辑内容和 dirty 状态保留。 */
async function dirtyCancel() {
  const page = await makePage([record("one"), record("two")]);
  await page.run("rmPreview('one')");
  page.elements.get("unit-projects").value = "unsaved";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  /** 拒绝合成对话框，不向真实窗口发送请求。 */
  page.context.window.confirm = function reject() { return false; };
  const count = page.requests.length;
  await page.run("rmAction({target:{closest(){return {dataset:{action:'current',id:'two'}}}}})");
  await page.run("rmRefresh()");
  assert.equal(page.requests.length, count);
  assert.equal(page.elements.get("unit-projects").value, "unsaved");
  assert.equal(page.run("rmEditorDirty"), true);
}
/** 附件上传后启用邻近解析，成功后禁用；不重复解析已完成附件。 */
async function nearbyParse() {
  const page = await makePage();
  assert.equal(page.elements.get("uploaded-parse").disabled, true);
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"])];
  await page.run("rmUpload({preventDefault(){}})");
  assert.equal(page.elements.get("uploaded-parse").disabled, false);
  await page.run("rmParseUploaded()");
  assert.equal(page.elements.get("uploaded-parse").disabled, true);
  const count = page.requests.length;
  await page.run("rmParseUploaded()");
  assert.equal(page.requests.length, count);
}
/** 模拟明确 change 和取消选择；真实客户端应一次保存、显式解析，并按附件和流状态展示操作。 */
async function singleUploadEntry() {
  const page = await makePage();
  assert.equal(page.elements.get("upload-submit").hidden, true);
  assert.equal(page.elements.get("attachment-toolbar").hidden, true);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  const file = page.elements.get("resume-file");
  await page.run("rmChooseFile()");
  assert.equal(file.clicked, true);
  const count = page.requests.length;
  await file.listeners.change({ preventDefault });
  assert.equal(page.requests.length, count);
  file.files = [new Blob(["%PDF-synthetic"])];
  await file.listeners.change({ preventDefault });
  assert.equal(page.items.length, 1);
  assert.equal(page.items[0].status, "uploaded");
  assert.equal(page.elements.get("attachment-toolbar").hidden, false);
  assert.equal(page.elements.get("uploaded-file-name").hidden, false);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  assert.equal(page.elements.get("parse-mode-hint").hidden, true);
  page.elements.get("parse-mode").value = "advanced";
  await page.run("rmControls()");
  assert.equal(page.elements.get("parse-mode-hint").hidden, false);
  await page.run("rmBusy = true; rmControls()");
  await file.listeners.change({ preventDefault });
  assert.equal(page.items.length, 1);
  assert.equal(page.elements.get("choose-file").disabled, true);
  await page.run("rmBusy = false; rmController = new AbortController(); rmControls()");
  assert.equal(page.elements.get("cancel-parse").hidden, false);
  await page.run("rmController = null; rmControls(); rmParseUploaded()");
  assert.equal(page.elements.get("attachment-toolbar").hidden, true);
  assert.equal(page.elements.get("cancel-parse").hidden, true);
  page.elements.get("upload-kind").value = "text";
  await page.run("rmSource()");
  assert.equal(page.elements.get("upload-submit").hidden, false);
  assert.equal(page.elements.get("pdf-input").hidden, true);
}
/** 模拟原件保存失败；验证选中文件后只请求一次，并保留当前在线稿和 dirty，不向模型发请求。 */
async function fileUploadFailure() {
  const page = await makePage([record("one")]);
  await page.run("rmPreview('one')");
  page.elements.get("unit-projects").value = "keep my draft";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  const originalFetch = page.context.fetch;
  let attempts = 0;
  /** 仅拒绝新原件 POST，保留既有授权读取；不请求实际本地服务或生产库。 */
  async function rejectUpload(url, options) {
    if (url === "/api/resume-versions/" && options.method === "POST") { attempts += 1; return json({ code: "invalid" }, 400); }
    return originalFetch(url, options);
  }
  page.context.fetch = rejectUpload;
  page.elements.get("resume-file").files = [new Blob(["%PDF-synthetic"])];
  await page.elements.get("resume-file").listeners.change({ preventDefault });
  assert.equal(attempts, 1);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.elements.get("unit-projects").value, "keep my draft");
  assert.equal(page.items.length, 1);
  assert.equal(page.elements.get("choose-file").disabled, false);
  assert.match(page.elements.get("operation-status").textContent, /^rm_http_error/);
}
/** 使用替身 DOM 中的非法数字，真实客户端必须在发请求前拒绝，保留当前输入。 */
async function invalidSlots() {
  const page = await makePage([record("one")]);
  await page.run("rmPreview('one')");
  page.elements.get("slot-num_publications").value = "1.5";
  const count = page.requests.length;
  await page.run("rmSaveEdition({preventDefault(){}})");
  assert.equal(page.requests.length, count);
  assert.match(page.elements.get("operation-status").textContent, /^re_number_error/);
}
test("edition save retains originals and typed recommendation fields", editionSave);
test("cancelled navigation preserves unsaved edits", dirtyCancel);
test("uploaded attachment has a one-shot nearby extract action", nearbyParse);
test("one upload entry saves selected files and exposes only available actions", singleUploadEntry);
test("failed file selection upload preserves the unsaved online draft", fileUploadFailure);
test("invalid recommendation count is rejected before saving", invalidSlots);

/** REST 替身拒绝新稿；真实客户端仍须保留输入/dirty，并保持 untouched 显式空数组语义。 */
async function failedSave() {
  const item = record("one"); item.slots = { interests: [] };
  const page = await makePage([item]);
  await page.run("rmPreview('one')");
  assert.equal(page.run("rmReadSlots().interests.length"), 0);
  page.elements.get("unit-projects").value = "keep draft";
  await page.run("rmDirty({currentTarget:rmEl('unit-projects')})");
  const fetchOriginal = page.context.fetch;
  /** 拒绝 editions POST；其他请求通过既有合成 REST，不访问外部服务。 */
  async function rejectEdition(url, options) {
    if (url.endsWith("editions/")) return json({ code: "invalid" }, 400);
    return fetchOriginal(url, options);
  }
  page.context.fetch = rejectEdition;
  await page.run("rmSaveEdition({preventDefault(){}})");
  assert.equal(page.elements.get("unit-projects").value, "keep draft");
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.items.length, 1);
  assert.match(page.elements.get("operation-status").textContent, /^rm_http_error/);
}
test("failed save retains unsaved text and explicit empty keywords", failedSave);

/** REST 替身给出建议；真实回填/保存代码必须区分确认与待确认，保持 0/false 并允许清空后保存。 */
async function suggestedSlots() {
  const original = record("suggested");
  original.slots = {gpa: 0};
  original.slot_suggestions = { values: {skills:["Python"],gpa:3.8,months_experience:0,num_publications:0,commit_to_summer:false}, evidence: {skills:[{text:"Python"}]} };
  const page = await makePage([original]);
  await page.run("rmPreview('suggested')");
  assert.equal(page.elements.get("slot-skills").value, "Python");
  assert.equal(page.elements.get("slot-gpa").value, "0");
  assert.equal(page.elements.get("slot-months_experience").value, "0");
  assert.equal(page.elements.get("slot-commit_to_summer").value, "false");
  assert.equal(page.elements.get("slot-num_publications").value, "0");
  assert.match(page.elements.get("slot-skills").attributes.title, /^re_suggestion_source/);
  assert.equal(page.run("rmSuggestedCount"), 4);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.equal(page.elements.get("edition-current").disabled, true);
  page.elements.get("slot-skills").value = "";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')}); rmSaveEdition({preventDefault(){}})");
  assert.equal(page.items[0].slots.skills, null);
  assert.equal(page.items[0].slots.commit_to_summer, false);
  assert.equal(page.elements.get("slot-skills").value, "");
  assert.equal(page.run("rmSuggestedCount"), 0);
  assert.equal(page.run("rmEditorDirty"), false);
  assert.equal(original.slot_suggestions.values.skills[0], "Python");
}
test("extracted recommendation suggestions require saving and preserve manual confirmation", suggestedSlots);

/** 合成 REST 提供岗位，真实客户端仅点击才请求保存 ID；原样文本不执行，编辑清空结果并禁用推荐。 */
async function savedRecommendations() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  item.recommendation_response = {source_name: "Synthetic jobs", source_kind: "experience", results: [
    {job_id: "test", status: "scored", rank: 1, recommendation_reason: {zh:"已保存 Python 技能。", en:"Python is recorded."}, available_feature_count: 1, matched_skills: ["Python"],
      job: {title: "<img src=x> Backend", company: "Synthetic", location: "Test city", description: "Test only",
        requirements: {required_skills: ["Python"], job_in_person_commitment: "Online"}}},
  ]};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')");
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.equal(page.requests.length, 3);
  await page.run("rmRecommend()");
  const request = page.requests.at(-1);
  assert.equal(request.url, "/api/resume-versions/saved/recommendations/");
  assert.equal(request.method, "POST");
  assert.equal(request.body, undefined);
  const cards = page.elements.get("recommendation-results").children;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].children[0].children[1].textContent, "<img src=x> Backend");
  assert.match(page.elements.get("recommendation-state").textContent, /rj_experience/);
  page.elements.get("slot-skills").value = "Java";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')}); rmRecommend()");
  assert.equal(page.requests.at(-1), request);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
  await page.run("rmPreview('saved')");
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
}
test("recommendations use only saved features and clear results on edits", savedRecommendations);

/** 来源错误明确展示且无假岗位/自动重试，单操作锁释放后允许用户显式再次请求。 */
async function recommendationFailure() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  item.recommendation_status = 502; item.recommendation_response = {code: "recommendation_llm_invalid_output"};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')");
  await page.run("rmRecommend()");
  assert.equal(page.requests.length, 4);
  assert.equal(page.elements.get("recommendation-state").textContent, "rj_llm_output{}");
  assert.equal(page.elements.get("recommendation-results").children.length, 0);
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.match(page.elements.get("operation-status").textContent, /^rj_llm_output/);
}
test("recommendation LLM failure stays explicit without fabricated jobs", recommendationFailure);

/** 模拟精排返回五岗；理由与标题中的 HTML 只作文本，切换语言不请求 API 或重排。 */
async function recommendationReasons() {
  const item = record("saved"); item.slots = {skills: ["Python"]};
  const results = [];
  for (let rank = 1; rank <= 5; rank += 1) results.push({status: "scored", rank, available_feature_count: 1, matched_skills: [], recommendation_reason: {zh:"<script>理由 " + rank, en:"<img src=x> Reason " + rank}, job: {title: "Job " + rank, requirements: {required_skills: ["Python"]}}});
  item.recommendation_response = {source_name: "Test", source_kind: "experience", results};
  const page = await makePage([item]);
  await page.run("rmPreview('saved')"); await page.run("rmRecommend()");
  const list = page.elements.get("recommendation-results");
  assert.equal(list.children.length, 5);
  assert.equal(list.hidden, false);
  assert.equal(list.children[0].children[1].children[1].textContent, "<script>理由 1");
  page.context.document.documentElement.lang = "en";
  await page.run("rmLanguage()");
  assert.equal(list.children[4].children[1].children[1].textContent, "<img src=x> Reason 5");
  assert.equal(list.children[4].children[0].children[1].textContent, "Job 5");
  assert.equal(page.requests.length, 4);
  assert.match(HTML, /role="region"[^>]*tabindex="0"/);
}
test("LLM reasons stay safe and switch language without another API call", recommendationReasons);

/** 合成 API 分三页，实际客户端选择器列出全部版本；显式选择只读详情，保留历史分页与 current。 */
async function recommendationSelection() {
  const ready = record("older"); ready.slots = { skills: ["Python"] };
  const page = await makePage([record("pdf", "uploaded"), record("recent"), ready], [], 1);
  const picker = page.elements.get("recommendation-resume");
  assert.equal(picker.children.length, 4);
  assert.match(picker.children[1].textContent, /rm_status_uploaded/);
  assert.match(picker.children[3].textContent, /<script>label<\/script>/);
  assert.equal(page.elements.get("versions-list").children.length, 1);
  picker.value = "older";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.run("rmEditorBase"), "older");
  assert.equal(page.elements.get("recommend-jobs").disabled, false);
  assert.equal(page.requests.at(-1).url, "/api/resume-versions/older/editor/");
  assert.equal(page.elements.get("job-recommendations").scrolled.block, "start");
  assert.equal(picker.focused.preventScroll, true);
  for (const request of page.requests) assert.equal(request.method || "GET", "GET");
  await page.run("rmNext()");
  assert.equal(page.run("rmPage"), 2);
  assert.equal(picker.value, "older");
  assert.equal(picker.children.length, 4);
}
test("recommendation picker includes older pages and selects without automatic writes", recommendationSelection);

/** 真实状态分支显示上传/解析/核对动作；解析仍显式，空槽位不能请求推荐，核对只导航不写入。 */
async function recommendationGuidance() {
  const empty = await makePage();
  assert.equal(empty.elements.get("recommendation-resume").disabled, true);
  assert.equal(empty.elements.get("recommendation-upload").hidden, false);
  assert.match(empty.elements.get("recommendation-state").textContent, /^rj_upload_first/);
  const page = await makePage([record("pdf", "uploaded")]);
  page.elements.get("recommendation-resume").value = "pdf";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.elements.get("recommendation-parse").hidden, false);
  assert.match(page.elements.get("recommendation-state").textContent, /^rj_parse_first/);
  const parseEvent = { currentTarget: { id: "recommendation-parse" } };
  const parsing = page.elements.get("recommendation-parse").listeners.click(parseEvent);
  parseEvent.currentTarget = null; // 模拟原生事件在异步处理返回后清空 currentTarget。
  await parsing;
  assert.equal(page.elements.get("job-recommendations").scrolled.block, "start");
  assert.equal(page.run("rmEditorBase"), "pdf");
  assert.equal(page.elements.get("recommendation-parse").hidden, true);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  assert.match(page.elements.get("recommendation-state").textContent, /^rj_empty_profile/);
  const before = page.requests.length;
  await page.run("rmReviewRecommendation(); rmRecommend()");
  assert.equal(page.elements.get("section-skills").open, true);
  assert.equal(page.elements.get("slot-skills").open, true);
  assert.equal(page.elements.get("slot-skills").focused.preventScroll, true);
  assert.equal(page.requests.length, before);
}
test("recommendation empty and unparsed states expose actionable next steps", recommendationGuidance);

/** 跨版本切换取消时不读写服务端，选择器还原已载入版本，未保存值及就近保存提示保持。 */
async function recommendationUnsaved() {
  const original = record("saved"); original.slots = { skills: ["Python"] };
  const page = await makePage([original, record("other")]);
  await page.run("rmPreview('saved')");
  page.elements.get("slot-skills").value = "Java";
  await page.run("rmDirty({currentTarget:rmEl('slot-skills')})");
  assert.equal(page.elements.get("recommendation-save").hidden, false);
  assert.equal(page.elements.get("recommend-jobs").disabled, true);
  /** 模拟取消丢弃，仅作用于本次隔离窗口。 */
  function rejectSwitch() { return false; }
  page.context.window.confirm = rejectSwitch;
  const before = page.requests.length;
  page.elements.get("recommendation-resume").value = "other";
  await page.run("rmChooseResume({currentTarget:rmEl('recommendation-resume')})");
  assert.equal(page.elements.get("recommendation-resume").value, "saved");
  assert.equal(page.elements.get("slot-skills").value, "Java");
  assert.equal(page.requests.length, before);
  assert.equal(page.run("rmEditorDirty"), true);
  assert.match(HTML, /id="recommendation-save"[^>]*form="edition-form"/);
}
test("recommendation switching protects pending details and offers nearby saving", recommendationUnsaved);
