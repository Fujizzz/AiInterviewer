/** Render an approved final report as safe text. No scoring, fetching or model calls. */
const REPORT_NAMES = {
  technical_depth: ["技术深度", "Technical depth"], ownership: ["个人贡献", "Ownership"],
  decision_making: ["决策与权衡", "Decision making"], debugging: ["问题排查", "Debugging"],
  evaluation: ["评估与验证", "Evaluation"], adaptability: ["适应与复盘", "Adaptability"],
};
const REPORT_PRACTICE = {
  technical_depth: ["选择一个核心技术点，说明原理、约束、实现细节与边界。", "Explain a core mechanism, its constraints, implementation and limits."],
  ownership: ["用一个具体事例区分自己的行动、团队分工与交付结果。", "Separate your actions, team responsibilities and delivered outcomes in one example."],
  decision_making: ["对比可选方案，说明取舍依据、代价和最终效果。", "Compare alternatives, trade-offs, costs and the eventual outcome."],
  debugging: ["按现象、假设、排查证据、根因和回归验证复述一次排障。", "Walk through symptoms, hypotheses, diagnostic evidence, root cause and verification."],
  evaluation: ["说明基线、指标、测试样本、误差分析及上线后的验证方式。", "Describe a baseline, metrics, test samples, error analysis and post-release validation."],
  adaptability: ["说明新信息如何改变原方案，以及复盘后采取了什么行动。", "Describe how new information changed your approach and what you did after reflection."],
};
const REPORT_REASONS = {
  evaluation_incomplete: ["部分回答未完成有效评分", "Some answers lack a valid evaluation"],
  evaluation_failure: ["评分处理出现异常", "An evaluation processing error occurred"],
  no_scoring_evidence: ["暂无可用于评分的证据", "No scoreable evidence is available"],
  no_scoreable_criterion: ["暂无达到发布条件的观察项", "No observation is ready for publication"],
  no_scoreable_competency: ["暂无可以评分的能力项", "No competency can be scored"],
  insufficient_overall_coverage: ["总体证据覆盖不足", "Overall evidence coverage is incomplete"],
  insufficient_competency_coverage: ["该能力的证据覆盖不足", "This competency needs broader evidence"],
  insufficient_independent_evidence: ["独立事例不足", "Too few independent examples"],
  insufficient_effective_weight: ["有效证据支持不足", "Insufficient effective evidence"],
  insufficient_criterion_reliability: ["观察项证据的可靠程度不足", "Observation evidence is not sufficiently reliable"],
  insufficient_competency_reliability: ["该能力证据的可靠程度不足", "Competency evidence is not sufficiently reliable"],
  unresolved_contradiction: ["回答之间仍有待澄清的矛盾", "Conflicting statements need clarification"],
};
const REPORT_FACTORS = {
  personal_action: ["本人行动", "Personal action"], technical_explanation: ["技术解释", "Technical explanation"],
  decision: ["决策", "Decision"], outcome: ["结果", "Outcome"], reflection: ["复盘", "Reflection"],
  personal: ["个人贡献", "Personal contribution"], shared: ["共同贡献", "Shared contribution"],
  team_only: ["团队层面的描述", "Team-level account"], unclear: ["尚不明确", "Unclear"],
  concrete: ["具体细节", "Concrete detail"], partial: ["部分细节", "Partial detail"], vague: ["细节有限", "Limited detail"],
  reported_experience: ["自述经历", "Self-reported experience"], hypothetical: ["假设情境", "Hypothetical situation"],
  opinion: ["观点", "Opinion"],
};
function reportElement(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

/** Both the live completion view and saved history consume this identical projection. */
export function renderEvaluationReport(container, report, { compact = false } = {}) {
  container.replaceChildren();
  if (!report) return;
  const english = window.AppI18n?.language() === "en";
  const t = (zh, en) => english ? en : zh;
  const label = (table, key) => table[key]?.[english ? 1 : 0] ?? key;
  const score = value => Number.isFinite(value) ? value.toFixed(2) : "—";
  const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  const status = value => ({ published: t("正式评分", "Published"), provisional: t("暂定评分", "Provisional"), unavailable: t("暂无法评分", "Unavailable") })[value] ?? t("历史评分", "Legacy score");
  const reason = code => REPORT_REASONS[code] ? label(REPORT_REASONS, code)
    : code.startsWith("unassessed_feedback:") ? t("有回答未纳入本次评分", "An answer was not scored")
      : t("存在其他评分限制", "Additional scoring limitations apply");
  const root = reportElement("article", undefined, "evaluation-report");
  const hero = reportElement("header", undefined, "er-hero");
  hero.append(reportElement("p", "EVALUATION / INTERVIEW", "er-eyebrow"), reportElement("h3", t("面试能力评估报告", "Interview evaluation report")));
  hero.append(reportElement("span", status(report.score_status), `er-badge er-${["published", "provisional", "unavailable"].includes(report.score_status) ? report.score_status : "legacy"}`));
  const stats = reportElement("div", undefined, "er-stats");
  for (const [title, value] of [[t("综合得分 / 5", "Overall score / 5"), score(report.overall_score)], [t("证据覆盖率", "Evidence coverage"), percent(report.overall_coverage)], [t("未完成评分的回答", "Unscored answers"), report.unscored_answer_count ?? "—"]]) {
    const item = reportElement("div"); item.append(reportElement("span", title), reportElement("strong", String(value))); stats.append(item);
  }
  hero.append(stats); root.append(hero);
  if (compact) { container.append(root); return; }
  const formal = report.scoring_source === "formal_evaluation";
  const explanation = report.explanation;
  const intro = !formal ? report.summary : report.score_status === "published"
    ? t("以下是你本次面试的能力表现与练习建议。", "Here are your competency results and practice suggestions from this interview.")
    : report.score_status === "provisional"
      ? t("当前分数基于已完成的评估，仅供本次复盘参考。", "These provisional scores reflect the completed assessments and can guide your interview review.")
      : t("本次暂未获得有效评分，你仍可查看已记录的回答与练习建议。", "A score is not available yet. You can still review your recorded answers and practice suggestions.");
  root.append(reportElement("p", intro, "er-intro"));
  if (report.unscored_answer_count > 0) root.append(reportElement("p", t(`有 ${report.unscored_answer_count} 轮回答暂未完成评分，未计入本次结果。`, `${report.unscored_answer_count} answer(s) have not been scored and are not included in these results.`), "er-notice"));
  const scored = Object.entries(report.competencies ?? {}).filter(([, c]) => Number.isFinite(c.score)).sort((a, b) => b[1].score - a[1].score);
  if (scored.length) {
    const overview = reportElement("div", undefined, "er-overview");
    const strongest = scored.filter(([, c]) => Math.abs(c.score - scored[0][1].score) < 0.005).map(([name]) => label(REPORT_NAMES, name));
    overview.append(reportElement("h4", t("本次表现概览", "At a glance")), reportElement("p", t("相对较高的已评分能力：", "Highest scored competencies in this interview: ") + strongest.join(t("、", ", ")) + t("。这是本次各项之间的比较，不是人群排名。", ". This compares competencies within this interview, not candidates.")));
    root.append(overview);
  }
  const section = reportElement("section", undefined, "er-dimensions");
  section.append(reportElement("h4", t("能力评分与证据", "Competencies and evidence")));
  for (const [name, dimension] of Object.entries(report.competencies ?? {})) {
    const detail = explanation?.competencies?.[name];
    const card = reportElement("section", undefined, "er-card");
    const heading = reportElement("div", undefined, "er-card-heading");
    heading.append(reportElement("h5", label(REPORT_NAMES, name)), reportElement("strong", `${score(dimension.score)} / 5`)); card.append(heading);
    card.append(reportElement("p", `${status(dimension.status)} · ${t("覆盖", "Coverage")} ${percent(dimension.coverage)} · ${dimension.evidence_count ?? 0} ${t("个独立事例", "independent examples")}`, "er-meta"));
    if (detail && Number.isFinite(dimension.score)) {
      card.append(reportElement("p", t(`依据 ${detail.selected_evidence_count} 段已采纳证据，${detail.scored_observation_count}/${detail.total_observation_count} 个观察项获得有效评分。以下展示实际纳入当前分数的原文示例。`, `${detail.selected_evidence_count} accepted evidence item(s); ${detail.scored_observation_count}/${detail.total_observation_count} observations scored. The excerpts below actually contributed to this score.`)));
    } else card.append(reportElement("p", Number.isFinite(dimension.score)
      ? t("此记录未保存可展示的评分依据，请结合下方问答复盘。", "This record has no displayable score basis; review the interview dialogue below.")
      : t("当前证据不足以形成此项分数，不作低能力判断。", "Available evidence does not support a score for this competency; no low-ability conclusion is made."), "er-meta"));
    const limitations = [...new Set([...(dimension.reason_codes ?? []), ...(detail?.limitation_codes ?? [])].map(reason))];
    if (limitations.length) card.append(reportElement("p", t("限制：", "Limitations: ") + limitations.join(t("；", "; ")), "er-limitation"));
    if (detail?.examples?.length) {
      const evidence = reportElement("details", undefined, "er-evidence");
      evidence.append(reportElement("summary", t(`查看评分依据 · ${detail.examples.length} 段原文`, `Inspect evidence · ${detail.examples.length} excerpts`)));
      for (const item of detail.examples) {
        const entry = reportElement("div", undefined, "er-source");
        entry.append(reportElement("h6", item.round_number ? t(`第 ${item.round_number} 轮`, `Round ${item.round_number}`) : t("来源回答", "Source answer")));
        entry.append(reportElement("p", t("问题：", "Question: ") + item.question, "er-question"));
        for (const quote of item.quotes ?? []) entry.append(reportElement("blockquote", quote));
        entry.append(reportElement("p", t("证据特征：", "Evidence characteristics: ") + [item.evidence_kind, item.ownership_scope, item.specificity, item.factuality].map(key => label(REPORT_FACTORS, key)).join(" · "), "er-meta"));
        entry.append(reportElement("p", t("该证据关联的观察等级：", "Observation levels associated with this evidence: ") + (item.levels ?? []).join(" / ") + t("（满分 5）。这是参与汇总的观察等级，并非本轮回答的独立总分。", " (out of 5). These levels contribute to aggregation; they are not a standalone answer score."), "er-meta"));
        evidence.append(entry);
      }
      if (detail.omitted_evidence_count) evidence.append(reportElement("p", t(`另有 ${detail.omitted_evidence_count} 段证据参与评分，本报告仅选取部分原文展示。`, `${detail.omitted_evidence_count} more evidence item(s) contributed; only a selection is shown.`), "er-meta"));
      card.append(evidence);
    }
    if (detail?.source_unavailable_count) card.append(reportElement("p", t("部分证据来源未通过展示校验，已省略原文。", "Some sources could not be verified for display; their excerpts were omitted."), "er-notice"));
    if (REPORT_PRACTICE[name]) {
      const practice = reportElement("p", t("练习建议：", "Practice suggestion: ") + label(REPORT_PRACTICE, name), "er-practice"); card.append(practice);
    }
    section.append(card);
  }
  root.append(section);
  if (!formal) for (const key of ["strengths", "weaknesses"]) {
    if (!report[key]?.length) continue;
    const findings = reportElement("section");
    findings.append(reportElement("h4", key === "strengths" ? t("本次表现", "Your strengths") : t("改进建议", "Areas to improve")));
    const list = reportElement("ul"); for (const statement of report[key]) list.append(reportElement("li", statement)); findings.append(list);
    root.append(findings);
  }
  const download = reportElement("button", t("下载可阅读报告（HTML）", "Download readable report (HTML)"), "secondary er-download");
  download.type = "button";
  download.addEventListener("click", () => {
    const copy = root.cloneNode(true); copy.querySelector(".er-download")?.remove();
    for (const details of copy.querySelectorAll("details")) details.open = true;
    const content = `<!doctype html><html lang="${english ? "en" : "zh-CN"}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${t("面试评估报告", "Interview evaluation")}</title><style>body{font:16px/1.7 system-ui,sans-serif;color:#203c35;max-width:900px;margin:32px auto;padding:0 24px}h3{font-size:28px}h4{font-size:21px}h5{font-size:18px;margin:0}h6{font-size:16px}section,header{margin-bottom:24px}blockquote{border-left:3px solid #679887;padding-left:16px;white-space:pre-wrap}p{overflow-wrap:anywhere}.er-stats,.er-card-heading{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap}.er-stats strong{display:block;font-size:32px}.er-card{border:1px solid #cbdcd4;border-radius:12px;padding:24px}.er-meta{color:#50675e;font-size:14px}.er-notice{background:#fff4d8;padding:16px}.er-badge{font-weight:bold}.er-source{break-inside:avoid}.er-practice{background:#f3f7f4;padding:12px}@media print{body{margin:0}details{display:block}}</style><body>${copy.outerHTML}</body></html>`;
    const url = URL.createObjectURL(new Blob([content], { type: "text/html;charset=utf-8" }));
    const link = reportElement("a"); link.href = url; link.download = "interview-evaluation-report.html"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  root.append(download); container.append(root);
}
