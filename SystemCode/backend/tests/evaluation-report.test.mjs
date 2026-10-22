import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const source = readFileSync(new URL("../frontend/evaluation-report.js", import.meta.url), "utf8");
class Node {
  constructor(tag) { this.tag = tag; this.textContent = ""; this.children = []; this.listeners = {}; this.attributes = {}; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  set innerHTML(value) { throw new Error("Never interpret report content as HTML"); }
}
const all = node => [node, ...node.children.flatMap(all)];
const text = node => all(node).map(n => n.textContent).join("\n");
function render(report, lang = "zh", options = {}) {
  const root = new Node("main");
  const context = vm.createContext({ document: { createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag) }, window: { AppI18n: { language: () => lang } }, root, report, options });
  vm.runInContext(source.replace("export function", "function") + ";renderEvaluationReport(root, report, options);", context);
  return { root, context };
}
function fixture() {
  return { scoring_source: "formal_evaluation", score_status: "provisional", overall_score: 3.125,
    overall_coverage: .8, unscored_answer_count: 1, score_reasons: ["unassessed_feedback:private-id"],
    competencies: { ownership: { score: 3.125, coverage: .8, evidence_count: 2, status: "provisional" }, evaluation: { score: null, coverage: 0, evidence_count: 0 } },
    explanation: { answer_count: 3, competencies: { ownership: { selected_evidence_count: 2, scored_observation_count: 2, total_observation_count: 3,
      examples: [{ round_number: 2, question: "What did you do?", quotes: ["<img onerror=evil()> & I fixed it."], levels: [3], evidence_kind: "personal_action", ownership_scope: "personal", specificity: "concrete", factuality: "reported_experience" }] } } } };
}
test("formal report links partial scores to actual excerpts without HTML interpretation", () => {
  const { root } = render(fixture()); const body = text(root);
  for (const expected of ["3.13", "80.0%", "暂定评分", "第 2 轮", "What did you do?", "<img onerror=evil()>", "观察等级", "未计入本次结果", "练习建议"]) {
    assert.ok(body.includes(expected), expected);
  }
  assert.ok(!body.includes("private-id"));
  assert.equal(all(root).filter(n => n.tag === "blockquote").length, 1);
  assert.equal(all(root).filter(n => n.tag === "pre").length, 0);
  assert.match(body, /不作低能力判断/);
});
test("published, unavailable and legacy reports never invent missing evidence", () => {
  const published = fixture(); published.score_status = "published"; published.unscored_answer_count = 0;
  assert.match(text(render(published).root), /本次面试的能力表现/);
  const unavailable = { score_status: "unavailable", scoring_source: "formal_evaluation", overall_score: null, competencies: {} };
  assert.match(text(render(unavailable).root), /本次暂未获得有效评分/);
  const legacy = { overall_score: 4, competencies: {}, summary: "Saved summary", strengths: ["Saved finding"] };
  assert.match(text(render(legacy).root), /Saved finding/);
  assert.equal(all(render(legacy).root).filter(n => n.tag === "blockquote").length, 0);
});
test("switching report language replaces the view without fetching or recalculating", () => {
  const data = fixture(); const before = JSON.stringify(data); const { root, context } = render(data, "en");
  assert.match(text(root), /Provisional/); assert.match(text(root), /Round 2/);
  vm.runInContext('window.AppI18n.language = () => "zh"; renderEvaluationReport(root, report);', context);
  assert.match(text(root), /暂定评分/); assert.equal(root.children.length, 1);
  assert.equal(JSON.stringify(data), before);
});
test("early numeric assessment does not claim completed evidence or offer export", () => {
  const body = render(fixture(), "zh", { compact: true }).root;
  assert.match(text(body), /3.13/);
  assert.equal(all(body).filter(n => n.tag === "blockquote" || n.tag === "button").length, 0);
});

test("radar plots only assessed scores and never closes a polygon over missing axes", () => {
  const partial = render(fixture()).root;
  assert.equal(all(partial).filter(n => n.attributes.class === "er-radar-area").length, 0);
  assert.equal(all(partial).filter(n => n.attributes.class === "er-radar-point").length, 1);
  const full = fixture();
  for (const key of ["technical_depth", "ownership", "decision_making", "debugging", "evaluation", "adaptability"]) {
    full.competencies[key] = { score: 4, coverage: .7, status: "published" };
  }
  const before = JSON.stringify(full);
  const nodes = all(render(full, "en").root);
  assert.equal(nodes.filter(n => n.attributes.class === "er-radar-point").length, 6);
  assert.equal(nodes.filter(n => n.attributes.class === "er-radar-area").length, 1);
  const radar = nodes.find(n => n.attributes.class === "er-radar");
  assert.equal(radar.attributes.role, "img");
  assert.match(radar.attributes["aria-label"], /Technical depth 4.00/);
  const firstPoint = nodes.find(n => n.attributes.class === "er-radar-point");
  assert.ok(Math.abs(Number(firstPoint.attributes.cy) - (174 - 108 * .8)) < .0001);
  assert.equal(JSON.stringify(full), before);
});

test("missing and zero coverage remain distinct and invalid scores cannot escape chart bounds", () => {
  const data = fixture(); data.overall_coverage = null;
  data.competencies.ownership.score = null;
  data.competencies.evaluation.score = 999;
  const nodes = all(render(data).root);
  assert.equal(nodes.filter(n => n.attributes.class === "er-radar-point").length, 0);
  assert.equal(nodes.filter(n => n.tag === "circle" && n.attributes.pathLength).length, 0);
  const meters = nodes.filter(n => n.attributes.class === "er-meter");
  assert.ok(meters.every(meter => meter.children.length === 1));
  data.overall_coverage = 0;
  assert.equal(all(render(data).root).find(n => n.attributes.pathLength)?.attributes["stroke-dasharray"], "0 100");
});

test("HTML export embeds the local chart stylesheet and opens evidence without another request", async () => {
  const { root, context } = render(fixture(), "en");
  const details = [{ open: false }]; let removed = false; let exported;
  root.children[0].cloneNode = () => ({
    querySelector: () => ({ remove() { removed = true; } }), querySelectorAll: () => details,
    outerHTML: '<article class="evaluation-report"><svg class="er-radar"></svg></article>',
  });
  context.document.styleSheets = [
    { href: "http://localhost/stream-demo/evaluation-report.css", cssRules: [{ cssText: ".er-radar { width: 100%; }" }] },
    { href: "http://localhost/other.css", cssRules: [{ cssText: ".private-unrelated-style {}" }] },
  ];
  const create = context.document.createElement;
  context.document.createElement = tag => { const node = create(tag); node.click = () => {}; return node; };
  context.Blob = Blob; context.URL = { createObjectURL(blob) { exported = blob; return "blob:report"; } };
  context.setTimeout = () => {};
  all(root).find(node => node.tag === "button").listeners.click();
  const html = await exported.text();
  assert.match(html, /\.er-radar \{ width: 100%; \}/);
  assert.match(html, /<svg class="er-radar">/);
  assert.doesNotMatch(html, /private-unrelated-style/);
  assert.ok(removed); assert.ok(details.every(detail => detail.open));
});
