"""One concise Markdown record per CLI interview, flushed at important milestones."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4

from agents.model_calls import current_model_call, safe_error_details
from agents.tracing import emit_trace, trace_sink


class TracedLLM:
    """Record application-level model requests/results, not private provider internals."""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, prompt, data, schema):
        # Scoped async calls own their diagnostics; late worker results stay silent.
        if current_model_call.get() is not None or trace_sink.get() is None:
            return self.llm(prompt, data, schema)
        call_id = str(uuid4())
        started = perf_counter()
        try:
            result = self.llm(prompt, data, schema)
        except BaseException as error:
            # Exception messages may contain credentials or HTTP bodies.
            emit_trace(
                "model.error",
                call_id=call_id,
                **safe_error_details(error),
                operation=schema.__name__,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            raise
        emit_trace(
            "model.response",
            call_id=call_id,
            output=result.model_dump(mode="json"),
            duration_ms=round((perf_counter() - started) * 1000),
        )
        return result


class FileTrace:
    """Keep only milestones in one Markdown file per interview."""

    def __init__(self, output_dir: Path):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = output_dir.resolve() / f"interview_{stamp}_{uuid4().hex[:8]}.md"
        self._lock = Lock()
        self._closed = False
        self._failure: OSError | None = None
        self._attempt = 0
        self._question_numbers = {}
        self._followup_limit = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        self._token = trace_sink.set(self.emit)
        self.emit("run.started", {})
        return self

    def emit(self, event, data):
        with self._lock:
            if self._closed or self._failure is not None:
                return
            text = self._render(event, data)
            if not text:
                return
            try:
                self._file.write(text + "\n\n")
                self._file.flush()
            except OSError as error:
                self._failure = error

    @staticmethod
    def _brief(value, limit=1200):
        text = str(value or "").strip()
        return text if len(text) <= limit else text[:limit] + "…（已截断）"

    def _render(self, event, data):
        if event == "run.started":
            return "# 面试关键节点记录\n\n开始时间（UTC）：" + datetime.now(UTC).isoformat()
        if event == "replay.started":
            return (
                "**真实模型完整回放测试**：简历解析、出题、质量审查、回答评价和报告均走正式程序，"
                "调用当前配置的真实模型。候选人回答为预设测试输入，不代表新的真人面试。"
            )
        if event == "simulation.started":
            return (
                "**离线模拟**：模型回答与工具选择使用预设脚本；"
                "策略、工具执行、评价聚合和日志走正式代码。未调用真实模型。"
            )
        if event == "verification.started":
            return (
                "**真实模型出题验证**：项目、回答和评价使用预设数据；"
                "出题及工具选择调用当前配置的真实模型。此记录用于检查生成流程，"
                "不代表真实候选人的面试评价。"
            )
        if event == "interview.started":
            topic_limit = data.get("max_questions_per_topic")
            if topic_limit is None:
                topic_limit = data.get("max_follow_up_per_topic", 2) + 1
            self._followup_limit = topic_limit - 1
            return (
                f"岗位：{self._brief(data['job_title'], 200)}；"
                f"计划题数：{data['max_questions']}；"
                f"每项目最多 {data.get('max_questions_per_project', 4)} 题；"
                f"每话题最多 {topic_limit} 题（均包含主问题和追问）"
            )
        if event == "model.response" and "projects" in data.get("output", {}):
            return f"简历解析完成：识别到 {len(data['output']['projects'])} 个项目。"
        if event == "question.started":
            self._attempt += 1
            if data.get("question_id"):
                self._question_numbers[data["question_id"]] = self._attempt
            return f"## 出题 {self._attempt}"
        if event == "question.planned":
            plan = data["plan"]
            self._question_numbers[plan["question_id"]] = self._attempt
            parent = self._question_numbers.get(plan.get("parent_question_id"))
            parent_label = f"第 {parent} 题" if parent else "无"
            root = self._question_numbers.get(plan.get("thread_id"))
            root_label = f"第 {root} 题" if root else "未知"
            followups = max(0, plan["probe_depth"] - 1)
            limit = self._followup_limit if self._followup_limit is not None else "未知"
            followup_label = (
                f"本话题第 {followups} 次追问 / 上限 {limit}" if followups else "主问题"
            )
            budget = data.get("budget")
            usage = (
                f"项目：{self._brief(data.get('project_name'), 250)}；"
                f"项目题数：{budget['project_questions'] + 1}/{budget['project_limit']}；"
                f"话题题数：{budget['topic_questions'] + 1}/{budget['topic_limit']}\n\n"
                if budget
                else ""
            )
            return (
                usage + f"动作：{plan['dialogue_action']}；"
                f"话题：{self._brief(plan.get('topic'), 200)}；"
                f"难度：{plan['difficulty']}；{followup_label}\n\n"
                f"所属主问题：{root_label}；关联上一题：{parent_label}\n\n"
                f"信息目标：{self._brief(plan.get('information_goal'), 300)}\n\n"
                f"选题依据：{data['selection']['reason_code']}；"
                f"追问判断：{data['probe']['reason_code']}\n\n"
                f"决策来源：{data.get('decision_source', 'policy')}；"
                f"决策摘要：{self._brief(data.get('decision_summary'), 300) or '规则兜底'}"
            )
        if event == "react.observation":
            result = data["result"]
            outcome = result.get("error") or (
                f"返回 {len(result.get('entries', []))} 条历史"
                if data["action"] == "get_history"
                else (
                    f"已读取项目 {result.get('project', {}).get('name', '')}"
                    if data["action"] == "get_project"
                    else "已读取面试计划"
                )
            )
            return f"工具：{data['action']} → {outcome}"
        if event in {"react.validation", "react.invalid_decision"}:
            errors = data.get("errors", [])
            if not errors or data.get("quality_reported"):
                return None
            text = "校验未通过：" + ", ".join(errors)
            if data.get("text"):
                text += f"\n\n待修复草稿：{self._brief(data['text'], 800)}"
            return text
        if event == "question.quality":
            labels = {"PASS": "通过", "REVISE": "需要修复", "UNAVAILABLE": "检查不可用"}
            issues = ", ".join(data.get("issues", []))
            text = f"提问质量：{labels[data['status']]}" + (f"；{issues}" if issues else "")
            if data.get("overload_discarded"):
                text += "；已忽略与单一回答要求不一致的多问判定"
            if "OVERLOADED_QUESTION" in data.get("issues", []):
                text += "\n\n独立回答要求：" + self._brief(
                    "；".join(data.get("answer_requests", [])), 500
                )
            if data.get("draft"):
                text += f"\n\n待修复草稿：{self._brief(data['draft'], 800)}"
                text += f"\n\n修复建议：{self._brief(data.get('guidance'), 700)}"
            if data.get("retrying"):
                text += "；重试同一草稿的审查"
            return text
        if event == "react.finished":
            steps = " → ".join(step["action"] for step in data["steps"]) or "无完成步骤"
            calls = sum(
                step["action"].startswith("get_")
                and step.get("status") not in {"TOOL_LIMIT", "REPEATED_TOOL_CALL"}
                for step in data["steps"]
            )
            mode = "直接生成" if calls == 0 else "工具循环"
            if data["stop_reason"] != "FINAL":
                mode = "生成未完成，转入兜底"
            return (
                f"生成过程：{mode}；工具调用 {calls} 次；轨迹：{steps}；"
                f"结束原因：{data['stop_reason']}；耗时：{data.get('duration_ms', 0)} ms"
            )
        if event == "model.error":
            issues = data.get("validation_issues", [])
            validation = "；结构问题：" + ", ".join(issues) if issues else ""
            number = self._question_numbers.get(data.get("question_id"))
            location = f"第 {number} 题" if number else data.get("operation", "模型调用")
            return (
                f"模型异常：{location} / {data.get('operation', 'unknown')} "
                f"/ 调用 {data.get('step') or 1}；"
                f"类别：{data.get('category', 'model_error')}；"
                f"原因类型：{data.get('cause_type', data['error_type'])}；"
                f"HTTP：{data.get('status_code') or '无'}；"
                f"位置：{data.get('scope', 'provider')}；"
                f"耗时：{data.get('duration_ms', 0)} ms；"
                f"请求标识：{data.get('call_id', '')}{validation}"
            )
        if event == "turn.conflict":
            return "提交冲突：本次尝试未提交，将按外层重试规则处理。"
        if event == "turn.committed":
            action = data["action"]
            if action["type"] == "ask_question":
                reason = data["decision_log"]["decision_reasons"].get("generation")
                return (
                    f"**最终问题**：{self._brief(action['question']['text'])}\n\n生成方式：{reason}"
                )
            return f"已提交动作：{action['type']}"
        if event == "answer.received":
            return "**回答**：" + self._brief(data["answer"]["text"])
        if event == "answer.evaluated":
            feedback = data["feedback"]
            dimensions = (
                "；".join(
                    f"{item['competency']}={item.get('rubric_level') or '证据不足'}"
                    for item in feedback.get("dimensions", [])
                )
                or "本轮无可评分证据"
            )
            completed = "已充分" if feedback["analysis"].get("thread_complete") else "尚未充分"
            return (
                f"回答分析：{feedback['analysis']['status']}；"
                f"话题展开：{completed}；评价：{dimensions}"
            )
        if event == "interview.result":
            report = data["report"]
            score = report["overall_score"]
            score_text = f"{score:.2f} / 5" if score is not None else "证据不足，暂不评分"
            return f"## 最终结果\n\n总分：{score_text}\n\n" + self._brief(
                report.get("summary"), 1500
            )
        if event == "run.finished":
            error = f"；异常类型：{data['error_type']}" if data.get("error_type") else ""
            return f"运行状态：{data['status']}{error}"
        return None

    def save_result(self, result):
        self.emit("interview.result", {"report": result["final_report"]})

    def __exit__(self, exc_type, exc, traceback):
        status = (
            "completed"
            if exc_type is None
            else (
                "cancelled"
                if exc_type.__name__ in {"KeyboardInterrupt", "EOFError", "CancelledError"}
                else "failed"
            )
        )
        self.emit(
            "run.finished",
            {"status": status, "error_type": exc_type.__name__ if exc_type else None},
        )
        trace_sink.reset(self._token)
        with self._lock:
            self._closed = True
            self._file.close()
        if self._failure is not None and exc_type is None:
            raise self._failure
