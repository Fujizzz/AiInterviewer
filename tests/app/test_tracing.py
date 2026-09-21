"""Verify concise, single-file interview records and cancellation handling."""

import pytest

from agents.question.react import QuestionAgentDecision
from agents.tracing import emit_trace, trace_sink
from app.application import build_application
from app.cli import run_interview
from app.tracing import FileTrace
from tests.app.fixtures import FixtureLLM
from tests.app.test_interview import RESUME


class ToolUsingLLM(FixtureLLM):
    def __call__(self, prompt, data, schema):
        if schema is QuestionAgentDecision and not data["observations"]:
            return schema(action="get_history", topic=None, limit=1, text=None)
        return super().__call__(prompt, data, schema)


def test_run_saves_only_one_concise_file(tmp_path):
    printed = []
    with FileTrace(tmp_path) as trace:
        emit_trace("react.input", payload={"secret_marker": "do not dump full input"})
        result = run_interview(
            build_application(ToolUsingLLM()),
            RESUME,
            max_questions=2,
            read_answer=lambda _: "I measured the cache hit rate.",
            write=printed.append,
        )
        trace.save_result(result)
    text = trace.path.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == [trace.path]
    assert trace.path.suffix == ".md"
    assert "返回 1 条历史" in text
    assert "get_history → final" in text
    assert "决策来源：model" in text
    assert "工具调用 1 次" in text
    reviewed_questions = sum(
        not log["fallback_used"]
        for log in result["decision_logs"]
        if log["question_type"] is not None
    )
    assert reviewed_questions > 0
    assert text.count("提问质量：通过") == reviewed_questions
    assert not any("提问质量" in line for line in printed)
    assert text.count("**最终问题**") == 2
    assert "I measured the cache hit rate." in text
    assert "评价：ownership=3" in text
    assert "## 最终结果" in text
    assert "completed" in text
    assert "secret_marker" not in text
    assert "output_schema" not in text
    assert "SECURITY RULES" not in text
    assert len(text) < 6000
    assert not any("get_history" in line for line in printed)
    assert trace_sink.get() is None


@pytest.mark.parametrize(
    "error,status",
    [
        (EOFError(), "cancelled"),
        (RuntimeError("not-for-trace"), "failed"),
    ],
)
def test_interrupted_run_keeps_partial_record(tmp_path, error, status):
    def stop(_):
        raise error

    with pytest.raises(type(error)):
        with FileTrace(tmp_path) as trace:
            run_interview(
                build_application(FixtureLLM()), RESUME, read_answer=stop, write=lambda _: None
            )
    text = trace.path.read_text(encoding="utf-8")
    assert "**最终问题**" in text
    assert status in text
    assert "## 最终结果" not in text
    assert "not-for-trace" not in text
    assert trace_sink.get() is None


def test_timeout_and_fallback_remain_visible(tmp_path):
    class TimeoutLLM(FixtureLLM):
        def __call__(self, prompt, data, schema):
            if schema is QuestionAgentDecision:
                raise TimeoutError("private provider error")
            return super().__call__(prompt, data, schema)

    with FileTrace(tmp_path) as trace:
        run_interview(
            build_application(TimeoutLLM()),
            RESUME,
            max_questions=1,
            read_answer=lambda _: "I tested it.",
            write=lambda _: None,
        )
    text = trace.path.read_text(encoding="utf-8")
    assert "TIMEOUT" in text
    assert "CANDIDATE_SPECIFIC_FALLBACK" in text
    assert "private provider error" not in text


def test_runs_are_unique_and_flush_before_exit(tmp_path):
    with FileTrace(tmp_path) as first:
        emit_trace("answer.received", answer={"text": "first answer"})
        assert "first answer" in first.path.read_text(encoding="utf-8")
    with FileTrace(tmp_path) as second:
        emit_trace("answer.received", answer={"text": "second answer"})
    assert first.path != second.path
    assert "first answer" not in second.path.read_text(encoding="utf-8")
    before = second.path.read_text(encoding="utf-8")
    emit_trace("answer.received", answer={"text": "outside"})
    assert second.path.read_text(encoding="utf-8") == before


def test_cli_saves_one_markdown_file(monkeypatch, tmp_path, capsys):
    from app import cli

    original_run = cli.run_interview

    def offline_run(*args, **kwargs):
        return original_run(*args, **kwargs, read_answer=lambda _: "I tested it.")

    monkeypatch.setattr(cli, "build_application", lambda: build_application(FixtureLLM()))
    monkeypatch.setattr(cli, "read_resume", lambda _: RESUME)
    monkeypatch.setattr(cli, "run_interview", offline_run)
    monkeypatch.setattr(
        "sys.argv", ["main.py", "resume.pdf", "--max-questions", "1", "--output-dir", str(tmp_path)]
    )
    assert cli.main() == 0
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".md"
    assert "Interview record:" in capsys.readouterr().err


def test_disk_failure_does_not_change_decision(tmp_path):
    with pytest.raises(OSError):
        with FileTrace(tmp_path) as trace:

            class BrokenWriter:
                def write(self, text):
                    raise OSError("disk full")

                def close(self):
                    pass

            trace._file.close()
            trace._file = BrokenWriter()
            result = run_interview(
                build_application(FixtureLLM()),
                RESUME,
                max_questions=1,
                read_answer=lambda _: "I wrote it.",
                write=lambda _: None,
            )
            assert not result["decision_logs"][0]["fallback_used"]
    assert trace_sink.get() is None


def test_direct_generation_log_does_not_imply_a_tool_loop(tmp_path):
    with FileTrace(tmp_path) as trace:
        run_interview(
            build_application(FixtureLLM()),
            RESUME,
            max_questions=1,
            read_answer=lambda _: "I implemented and tested the parser.",
            write=lambda _: None,
        )
    record = trace.path.read_text(encoding="utf-8")
    assert "直接生成；工具调用 0 次；轨迹：final" in record
    assert "决策来源：model" in record
    assert "工具循环" not in record
