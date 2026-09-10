"""Verify interview graph transitions, checkpoints, CLI behavior, and OpenAI handling."""
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from langgraph.types import Command

from app.agents.answer_analyzer import AnswerAnalysis
from app.agents.evaluator import FinalReport
from app.agents.question_agent import QuestionOutput
from app.agents.resume_parser import ResumeOutput
from app.graph import build_graph
from app.llm import LLMError, OpenAILLM
from main import run_interview
from tests.fixtures import FixtureLLM

RESUME = (Path(__file__).resolve().parents[1] / "sample_resume.txt").read_text()


class InterviewTests(unittest.TestCase):
    def test_interrupt_follow_up_next_topic_and_finish(self):
        """Verify five pause/resume cycles, topic counters, saved answers, and final completion."""
        llm = FixtureLLM()
        graph = build_graph(llm)
        config = {"configurable": {"thread_id": "complete"}}
        state = graph.invoke({"resume_text": RESUME, "max_questions": 5,
                              "max_follow_up_per_topic": 2}, config)
        self.assertEqual(state["candidate_profile"]["name"], "Alex Chen")
        self.assertIn("Book Recommendation System", state["current_question"])
        self.assertEqual(state["question_history"], [])
        self.assertEqual([schema for schema, _ in llm.calls], [ResumeOutput, QuestionOutput])
        self.assertEqual(graph.get_state(config).next, ("ask_candidate",))
        positions = []
        for i in range(5):
            self.assertTrue(state["__interrupt__"])
            positions.append((state["topic_index"], state["follow_up_count"]))
            state = graph.invoke(Command(resume=f"My specific answer {i}"), config)
            self.assertEqual(len(state["question_history"]), i + 1)
            self.assertEqual(state["question_history"][-1]["answer"], f"My specific answer {i}")
        self.assertEqual(positions, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)])
        self.assertTrue(state["interview_finished"])
        self.assertEqual(state["decision"], "finish")
        self.assertFalse(state.get("__interrupt__"))
        self.assertEqual(graph.get_state(config).next, ())
        self.assertEqual(sum(s is FinalReport for s, _ in llm.calls), 1)
        self.assertEqual(sum(s is AnswerAnalysis for s, _ in llm.calls), 5)
        self.assertEqual(sum(s is QuestionOutput for s, _ in llm.calls), 5)
        FinalReport.model_validate(state["final_report"])
        opening_calls = [d for s, d in llm.calls if s is QuestionOutput and d["follow_up_count"] == 0]
        self.assertTrue(all(d["previous_answer_analysis"] == {} for d in opening_calls))

    def test_limits_and_topic_exhaustion(self):
        """Check termination for total limits, follow-up limits, and exhausted topics."""
        for follow_up, limit, cap, expected in [(True, 1, 2, 1), (True, 10, 0, 2),
                                                (False, 10, 2, 2), (True, 10, 2, 6)]:
            with self.subTest(follow_up=follow_up, limit=limit, cap=cap):
                state = run_interview(build_graph(FixtureLLM(follow_up)), RESUME,
                    max_questions=limit, max_follow_up_per_topic=cap,
                    read_answer=lambda _: "I implemented and tested this.", write=lambda _: None)
                self.assertEqual(len(state["question_history"]), expected)
                self.assertTrue(state["interview_finished"])

    def test_threads_do_not_share_answers(self):
        """Ensure separate thread IDs keep pending questions and answer histories isolated."""
        graph = build_graph(FixtureLLM())
        a = {"configurable": {"thread_id": "a"}}
        b = {"configurable": {"thread_id": "b"}}
        for config in (a, b):
            graph.invoke({"resume_text": RESUME, "max_questions": 1}, config)
        graph.invoke(Command(resume="Answer A"), a)
        self.assertEqual(graph.get_state(b).values["question_history"], [])
        self.assertEqual(graph.get_state(b).next, ("ask_candidate",))
        graph.invoke(Command(resume="Answer B"), b)
        self.assertEqual(graph.get_state(a).values["question_history"][0]["answer"], "Answer A")

    def test_cli_reprompts_empty_answer_and_prints_json(self):
        """Confirm the CLI rejects blank input and prints a final JSON report."""
        answers = iter(["   ", "I built it.", "I chose it for simplicity.", "I tested held-out data."])
        lines = []
        state = run_interview(build_graph(FixtureLLM()), RESUME, max_questions=3,
                              read_answer=lambda _: next(answers), write=lines.append)
        self.assertEqual(len(state["question_history"]), 3)
        self.assertIn("Please enter an answer", "\n".join(lines))
        self.assertIn('"overall_score": 3.0', lines[-1])

    def test_invalid_resume_and_limits_fail_before_llm(self):
        """Verify invalid initial inputs fail before any model request."""
        for state in [{"resume_text": " "}, {"resume_text": RESUME, "max_questions": 0},
                      {"resume_text": RESUME, "max_follow_up_per_topic": -1}]:
            llm = FixtureLLM()
            with self.assertRaises(ValueError):
                build_graph(llm).invoke(state, {"configurable": {"thread_id": "invalid"}})
            self.assertEqual(llm.calls, [])

    def test_no_topics_has_clear_error(self):
        """Check that a parsed resume without topics produces an explicit error."""
        def no_topics(prompt, data, schema):
            """Return an empty profile and topic list to simulate an unusable resume."""
            return ResumeOutput(candidate_profile={"name": "", "skills": [],
                                "experiences": [], "projects": []}, topics=[])
        with self.assertRaisesRegex(ValueError, "No interview topics"):
            build_graph(no_topics).invoke({"resume_text": "Hello"},
                                         {"configurable": {"thread_id": "empty"}})

    def test_duplicate_question_retried_then_rejected(self):
        """Verify repeated duplicate questions raise an error instead of continuing the interview."""
        fixture = FixtureLLM()
        def duplicate(prompt, data, schema):
            """Always repeat the same question while delegating other outputs to the fixture."""
            if schema is QuestionOutput:
                return QuestionOutput(current_question="What did you build?")
            return fixture(prompt, data, schema)
        graph = build_graph(duplicate)
        config = {"configurable": {"thread_id": "duplicate"}}
        graph.invoke({"resume_text": RESUME}, config)
        with self.assertRaisesRegex(LLMError, "duplicate"):
            graph.invoke(Command(resume="A recommender."), config)

    @patch("app.llm.load_dotenv")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model",
                               "OPENAI_TEMPERATURE": "0"}, clear=True)
    @patch("app.llm.OpenAI")
    def test_wrapper_structured_output_and_refusal(self, client, dotenv):
        """Check OpenAI structured-output options and handling of a missing parsed response."""
        client.return_value.responses.parse.return_value = Mock(
            output_parsed=QuestionOutput(current_question="What did you build?"))
        llm = OpenAILLM()
        self.assertEqual(llm("Prompt", {}, QuestionOutput).current_question, "What did you build?")
        kwargs = client.return_value.responses.parse.call_args.kwargs
        self.assertIs(kwargs["text_format"], QuestionOutput)
        self.assertEqual(kwargs["temperature"], 0)
        self.assertFalse(kwargs["store"])
        client.return_value.responses.parse.return_value = Mock(output_parsed=None)
        with self.assertRaises(LLMError):
            llm("Prompt", {}, QuestionOutput)


if __name__ == "__main__":
    unittest.main()
