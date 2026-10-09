/**
 * @module interview-progress
 * Responsibilities: Convert checked Agent snapshots into progress and anomaly display; shared by dashboard and personal center.
 * Implementation: Number of questions calculated as current assignment minus completed count; monotonic local clock advances remaining budget, no end command sent.
 * Related Modules: agent.js delivers only received business packages, interview-history.js reads read-only history API.
 * Declaration Index:
 * - reviewText: reads bilingual display resources and interpolates.
 * - reviewTopics: calculates topic display and total estimated question count based on remaining quota.
 * - InterviewProgress: coordinator between snapshot presentation and clock lifecycle.
 * - InterviewProgress.constructor: initializes snapshot, clock, and deduplication set.
 * - InterviewProgress.update: receives business snapshot, updates unique timer and anomalies.
 * - InterviewProgress.render: updates plain text progress area.
 * - InterviewProgress.renderClock: updates countdown only, avoids rebuilding scrollable topic list.
 * - InterviewProgress.freeze: stops timing and freezes estimated values.
 * - InterviewProgress.reset: clears page snapshot and anomalies.
 * - InterviewProgress.warn: deduplicates and appends fixed anomaly warning.
 * - InterviewProgress.anomalies: reads existing diagnostic flags.
 * Variable Index:
 * - REVIEW_TEXT: bilingual display resources, excludes model parameters or scoring rules.
 * Constraints:
 * No model calls, retries, or persistence; frozen last estimate upon disconnection; missing old data shows as unrecorded.
 *
 */
const REVIEW_TEXT = {
  zh: {
    remaining: "剩余时间",
    stage: "当前阶段",
    questions: "题目进度",
    topics: "话题进度",
    estimate: "第 {count} 题 / 预计约 {total} 题",
    ceiling: "安全上限 {max} 题 · 计划 v{version}",
    note: "预计题数随计划调整；时间包含回答和模型等待，到时由后端在提交后判断收尾。",
    frozen: "最后记录（计时已停止）",
    unknown: "未记录",
    intro: "开场",
    project_deep_dive: "项目深入",
    technical: "技术能力",
    behavioral: "行为面试",
    closing: "收尾",
    finished: "已结束",
    pending: "待开始",
    active: "进行中",
    completed: "已完成",
    skipped: "已跳过",
    asked: "已问 {count} 题 · 计划约 {total} 题",
    fallback: "Agent 记录了既有备用路径，请结合复盘记录查看。",
    retrieval: "知识检索部分失败或超时：{sources}。",
    narrative: "报告文字生成失败，当前显示既有的确定性摘要。",
    loading: "正在读取面试记录…",
    empty: "暂无面试记录",
    history: "面试复盘",
    refresh: "刷新",
    detail: "查看复盘",
    previous: "上一页",
    next: "下一页",
    failed: "读取失败，请点击刷新重试。",
    incomplete: "面试未完成，以下为已保存的过程；未评价回答不计为已评分证据。",
    unavailable: "暂无获准公开的模型内容。",
    dialogue: "对话过程",
    answer: "你的回答",
    unanswered: "尚未回答",
    unevaluated: "未获得可公开的评价",
    assessment_pending: "能力评价尚未完成",
    assessment_unavailable: "本次能力评价不可用",
    strengths: "优势",
    weaknesses: "待改进",
    difficulty: "难度",
    depth: "追问深度",
    summary: "面试总结",
    diagnostics: "计划修订与决策记录",
    preparing: "准备中",
    interrupted: "已中断",
    failed_status: "失败",
    review_completed: "已完成",
    review_active: "进行中",
    request_issue: "请求异常：{code}（{status}）",
    score: "综合评分",
    relevance: "回答相关度",
    evidence: "证据强度",
    dimensions: "能力评价",
    missing: "缺少的信息",
    contradictions: "矛盾信息",
    uncertainties: "待澄清信息"
  },
  en: {
    remaining: "Time remaining",
    stage: "Current stage",
    questions: "Question progress",
    topics: "Topic progress",
    estimate: "Question {count} / about {total} planned",
    ceiling: "Safety limit {max} · Plan v{version}",
    note: "Estimates change with the plan. Time includes answers and model processing; the backend checks closure after submission.",
    frozen: "Last recorded (timer stopped)",
    unknown: "Not recorded",
    intro: "Introduction",
    project_deep_dive: "Project deep dive",
    technical: "Technical",
    behavioral: "Behavioral",
    closing: "Closing",
    finished: "Finished",
    pending: "Pending",
    active: "In progress",
    completed: "Completed",
    skipped: "Skipped",
    asked: "Asked {count} · about {total} planned",
    fallback: "The Agent recorded an existing fallback path. Review the decision records for context.",
    retrieval: "Some retrieval sources failed or timed out: {sources}.",
    narrative: "Report narrative failed; the existing deterministic summary is shown.",
    loading: "Loading interview records…",
    empty: "No interview records yet",
    history: "Interview review",
    refresh: "Refresh",
    detail: "View review",
    previous: "Previous",
    next: "Next",
    failed: "Could not load records. Click Refresh to retry.",
    incomplete: "Interview incomplete. This is the saved conversation; unevaluated answers are not scored evidence.",
    unavailable: "No approved model content is available.",
    dialogue: "Conversation",
    answer: "Your answer",
    unanswered: "Not answered",
    unevaluated: "No approved evaluation available",
    assessment_pending: "Capability assessment pending",
    assessment_unavailable: "Capability assessment unavailable",
    strengths: "Strengths",
    weaknesses: "Areas to improve",
    difficulty: "Difficulty",
    depth: "Follow-up depth",
    summary: "Interview summary",
    diagnostics: "Plan revisions and decision records",
    preparing: "Preparing",
    interrupted: "Interrupted",
    failed_status: "Failed",
    review_completed: "Completed",
    review_active: "In progress",
    request_issue: "Request issue: {code} ({status})",
    score: "Overall score",
    relevance: "Answer relevance",
    evidence: "Evidence strength",
    dimensions: "Competency evaluation",
    missing: "Missing information",
    contradictions: "Contradictions",
    uncertainties: "Uncertainties"
  },
};
/**
 * Given resource key and interpolation, returns plain text in current interface language; does not translate model content.
 */
export function reviewText(key, values = {}) {
  let text = REVIEW_TEXT[window.AppI18n?.language() === "en" ? "en" : "zh"][key] ?? key;
  for (const [name, value] of Object.entries(values)) text = text.replaceAll(`{${name}}`, String(value));
  return text;
}
/**
 * Given inspected snapshot, outputs display line and total estimated question count; assignment is cumulative target, completed/skipped items have no remaining quota.
 * Do not treat plan estimate as guarantee; return null for old records without plan, do not read internal state or guess default quota.
 *
 */
export function reviewTopics(snapshot) {
  if (!snapshot?.interview_plan) return null;
  const plan = snapshot.interview_plan;
  const progress = snapshot.topic_progress ?? {};
  const rows = [];
  let remaining = 0;
  for (const item of plan.topics ?? []) {
    const state = progress[item.topic_key];
    const count = state?.questions_asked ?? null;
    const status = state?.status ?? "unknown";
    if (status === "pending" || status === "active") remaining += Math.max(0, item.expected_questions - count);
    rows.push({ key: item.topic_key, title: item.objective, status, count, total: item.expected_questions });
  }
  const count = snapshot.interview_state?.question_index ?? null;
  const total = count === null || !snapshot.topic_progress ? null : Math.min(plan.max_questions, count + remaining);
  return { rows, count, total, max: plan.max_questions, version: plan.version };
}
/**
 * Coordinates the display lifecycle of inspected business snapshots; does not control backend budget, recording, or scoring.
 */
export class InterviewProgress {
  /**
 * No external parameters; initializes snapshot, monotonic clock, timer, and set of displayed anomalies.
 */
  constructor() { this.snapshot = null; this.anchor = null; this.timer = null; this.running = false; this.seen = new Set(); }
  /**
 * Given inspected question or finished.result; replaces snapshot and starts unique timer, scans fixed diagnostic fields.
 */
  update(snapshot) {
    this.freeze(); this.snapshot = snapshot; this.anchor = performance.now(); this.running = !!snapshot.interview_state && snapshot.interview_state.status !== "finished" && Number.isFinite(snapshot.interview_state.remaining_seconds);
    this.render();
    if (this.running) this.timer = setInterval(this.renderClock.bind(this), 1000);
    this.anomalies(snapshot);
  }
  /**
 * Given instance snapshot and current monotonic time, updates plain text progress; does not subtract client time from server clock_started_at.
 */
  render() {
    if (!this.snapshot) return;
    const get = document.getElementById.bind(document);
    get("interview-progress").hidden = false;
    this.renderClock();
    const state = this.snapshot.interview_state;
    get("interview-stage").textContent = reviewText(state?.stage ?? "unknown");
    get("interview-clock-note").textContent = reviewText(this.running ? "note" : "frozen");
    const topics = reviewTopics(this.snapshot);
    get("interview-question-progress").textContent = topics?.total === null || !topics ? reviewText("unknown") : reviewText("estimate", topics);
    get("interview-plan-meta").textContent = topics ? reviewText("ceiling", topics) : reviewText("unknown");
    const list = get("interview-topics"); list.replaceChildren();
    for (const row of topics?.rows ?? []) {
      const item = document.createElement("li");
      item.textContent = `${reviewText(row.status)} · ${row.title} · ${row.count === null ? reviewText("unknown") : reviewText("asked", row)}`;
      list.append(item);
    }
  }
  /**
 * Given remaining seconds and monotonic clock at receipt; updates only countdown text, does not re-render topics or trigger network commands.
 */
  renderClock() {
    if (!this.snapshot) return;
    const seconds = this.snapshot.interview_state?.remaining_seconds;
    const remaining = Number.isFinite(seconds) ? Math.max(0, Math.ceil(seconds - (this.running ? (performance.now() - this.anchor) / 1000 : 0))) : null;
    document.getElementById("interview-remaining").textContent = remaining === null ? reviewText("unknown") : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  }
  /**
 * Clears unique timer and freezes estimated budget to snapshot copy; original response remains unchanged.
 */
  freeze() {
    if (this.timer !== null) clearInterval(this.timer); this.timer = null;
    if (this.running && this.snapshot?.interview_state) {
      const state = this.snapshot.interview_state;
      this.snapshot = { ...this.snapshot, interview_state: { ...state, remaining_seconds: Math.max(0, state.remaining_seconds - (performance.now() - this.anchor) / 1000) } };
    }
    this.running = false; this.render();
  }
  /**
 * Clears instance and DOM on new interview or reset; does not delete backend history records.
 */
  reset() { this.freeze(); this.snapshot = null; this.seen.clear(); document.getElementById("interview-progress").hidden = true; document.getElementById("interview-alerts").replaceChildren(); document.getElementById("interview-alert-panel").hidden = true; }
  /**
 * Given fixed diagnostic identifier and safe text; deduplicates and appends alert, does not record resume, answers, or vendor raw anomalies.
 */
  warn(key, text) {
    if (this.seen.has(key)) return; this.seen.add(key);
    const item = document.createElement("li"); item.textContent = text;
    document.getElementById("interview-alerts").append(item); document.getElementById("interview-alert-panel").hidden = false;
    console.info("Interview notice", { code: key });
  }
  /**
 * Given inspected snapshot; only prompts based on existing failure/timeout/backup flags, does not add backup strategy.
 */
  anomalies(snapshot) {
    for (const log of snapshot.decision_logs ?? []) {
      if (log.fallback_used) this.warn(`fallback:${log.decision_id}`, reviewText("fallback"));
      const sources = [...new Set([...(log.failed_retrieval_sources ?? []), ...(log.timeout_retrieval_sources ?? [])])];
      if (sources.length) this.warn(`retrieval:${log.decision_id}`, reviewText("retrieval", { sources: sources.join(", ") }));
    }
    if (snapshot.report_narrative_status === "fallback") this.warn("report_narrative", reviewText("narrative"));
  }
}
