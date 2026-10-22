"""Responsibilities: Verify the remote MVP shell delegates to the canonical local Agent core.
Implementation: Execute typed provider or application paths with explicit validation boundaries.
Related Modules: shared.resume_contract and app.parsing.resume define canonical resume data.
Declaration Index:
- InterviewTests: Group isolated contract and lifecycle regression cases.
- InterviewTests.test_integrated_flow_uses_local_contracts_and_finishes: Verify integrated flow
  uses local contracts and finishes.
- InterviewTests.test_question_safety_limit_does_not_determine_time_budget: Verify question
  safety limit does not determine time budget.
- InterviewTests.test_application_reuse_keeps_interviews_isolated: Verify application reuse
  keeps interviews isolated.
- InterviewTests.test_cli_reprompts_empty_answer_and_prints_json: Verify cli reprompts empty
  answer and prints json.
- InterviewTests.test_invalid_inputs_fail_before_provider_call: Verify invalid inputs fail
  before provider call.
- InterviewTests.test_empty_extraction_has_clear_error: Verify empty extraction has clear error.
- InterviewTests.test_empty_extraction_has_clear_error.empty_extraction: Model a valid canonical
  document with no interviewable facts.
- InterviewTests.test_wrapper_structured_output_and_refusal: Verify wrapper structured output
  and refusal.
Variable Index:
- RESUME: Synthetic original text used only by offline integration tests.
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents.question.react import QuestionAgentDecision
from app.adapters.llm import GeneratedText
from app.application import build_application
from app.cli import run_interview
from app.parsing.resume import ResumeExtraction
from app.providers.llm import LLMError, OpenAILLM
from tests.app.fixtures import FixtureLLM
from tests.app.resume_fixture import resume_output

RESUME = """
Alex Chen
Book Recommendation System: implemented item-based collaborative filtering with Python,
pandas and scikit-learn; evaluated precision against a popularity baseline.
Log Analysis Pipeline: streamed logs with Python and SQL, isolated malformed records,
and tested the parser with pytest.
"""


class InterviewTests(unittest.TestCase):
    """Functionality: Group isolated contract and lifecycle regression cases.
    Logic: Test setup supplies local state and explicit external-service substitutes.
    Constraints: Test assertions do not establish live provider availability.
    """

    def test_integrated_flow_uses_local_contracts_and_finishes(self):
        """Functionality: Verify integrated flow uses local contracts and finishes.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        llm = FixtureLLM()
        result = run_interview(
            build_application(llm),
            RESUME,
            max_questions=5,
            read_answer=lambda _: "I implemented it, compared alternatives, and measured results.",
            write=lambda _: None,
        )

        self.assertTrue(result["interview_finished"])
        self.assertEqual(result["candidate_name"], "Alex Chen")
        self.assertEqual(len(result["question_history"]), 6)
        self.assertIsNone(result["question_history"][0]["evaluation"])
        self.assertEqual(result["question_history"][0]["unscored_reason"], "self_introduction")
        self.assertGreater(result["interview_state"]["remaining_seconds"], 0)
        self.assertEqual(result["interview_plan"]["duration_seconds"], 1800)
        self.assertEqual(result["interview_state"]["status"], "finished")
        self.assertEqual(result["final_report"]["overall_score"], 3.0)
        self.assertEqual(len(result["decision_logs"]), 7)
        self.assertTrue(
            all(entry["evaluation"]["evidence_ids"] for entry in result["question_history"][1:])
        )
        profile = result["candidate_profile"]
        self.assertEqual(profile["contract_version"], "2.0")
        self.assertEqual(len(profile["projects"]), 2)
        question_calls = [data for schema, data in llm.calls if schema is QuestionAgentDecision]
        self.assertEqual(
            len({data["state_summary"]["question_index"] for data in question_calls}), 5
        )
        self.assertIsNone(question_calls[0]["latest_turn"])
        latest = question_calls[1]["latest_turn"]
        self.assertEqual(latest["answer"]["text"], result["question_history"][1]["answer"])
        self.assertEqual(latest["question"]["question_id"], latest["answer"]["question_id"])

    def test_question_safety_limit_does_not_determine_time_budget(self):
        """Functionality: Verify question safety limit does not determine time budget.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        for limit in (1, 3):
            with self.subTest(limit=limit):
                result = run_interview(
                    build_application(FixtureLLM()),
                    RESUME,
                    max_questions=limit,
                    read_answer=lambda _: "A grounded technical answer with concrete evidence.",
                    write=lambda _: None,
                )
                self.assertEqual(len(result["question_history"]), limit + 1)
                self.assertLess(result["interview_state"]["elapsed_seconds"], 120)
                self.assertEqual(result["interview_plan"]["duration_seconds"], 1800)
                self.assertEqual(
                    result["decision_logs"][-1]["reason_code"], "QUESTION_SAFETY_LIMIT"
                )
                self.assertTrue(result["interview_finished"])

    def test_application_reuse_keeps_interviews_isolated(self):
        """Functionality: Verify application reuse keeps interviews isolated.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        application = build_application(FixtureLLM())
        first = run_interview(
            application,
            RESUME,
            max_questions=1,
            read_answer=lambda _: "Answer A with sufficient technical detail.",
            write=lambda _: None,
        )
        second = run_interview(
            application,
            RESUME,
            max_questions=1,
            read_answer=lambda _: "Answer B with different technical detail.",
            write=lambda _: None,
        )
        self.assertNotEqual(first["interview_id"], second["interview_id"])
        self.assertEqual(len(application.repository.contexts), 2)
        self.assertEqual(
            first["question_history"][0]["answer"], "Answer A with sufficient technical detail."
        )

    def test_cli_reprompts_empty_answer_and_prints_json(self):
        """Functionality: Verify cli reprompts empty answer and prints json.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        answers = iter(
            ["   ", "I work on Python projects.", "I built and validated the implementation."]
        )
        lines = []
        result = run_interview(
            build_application(FixtureLLM()),
            RESUME,
            max_questions=1,
            read_answer=lambda _: next(answers),
            write=lines.append,
        )
        self.assertEqual(len(result["question_history"]), 2)
        self.assertIn("Please enter an answer", "\n".join(lines))
        self.assertIn('"overall_score": 3.0', lines[-1])

    def test_invalid_inputs_fail_before_provider_call(self):
        """Functionality: Verify invalid inputs fail before provider call.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        for resume, limit, follow_up in [(" ", 1, 2), (RESUME, 0, 2), (RESUME, 1, -1)]:
            llm = FixtureLLM()
            with self.subTest(resume=resume, limit=limit, follow_up=follow_up):
                with self.assertRaises(ValueError):
                    run_interview(
                        build_application(llm),
                        resume,
                        max_questions=limit,
                        max_follow_up_per_topic=follow_up,
                        read_answer=lambda _: "unused",
                        write=lambda _: None,
                    )
                self.assertEqual(llm.calls, [])

    def test_empty_extraction_has_clear_error(self):
        """Functionality: Verify empty extraction has clear error.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """

        def empty_extraction(prompt, data, schema):
            """Functionality: Model a valid canonical document with no interviewable facts.
            Inputs: prompt, source data and schema. Outputs: Empty semantic fixture.
            Logic: Supply source coverage while leaving skills and projects unknown or empty.
            Constraints: Any downstream model call fails the test.
            """
            if schema is ResumeExtraction:
                return resume_output(data, candidate_name=None, skills=None, projects=[])
            raise AssertionError("Provider should stop after resume extraction")

        with self.assertRaisesRegex(ValueError, "No interviewable"):
            run_interview(
                build_application(empty_extraction),
                "Candidate with no technical facts.",
                read_answer=lambda _: "unused",
                write=lambda _: None,
            )

    @patch("app.providers.llm.load_dotenv")
    @patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "test-model",
            "OPENAI_TEMPERATURE": "0",
        },
        clear=True,
    )
    @patch("app.providers.llm.OpenAI")
    def test_wrapper_structured_output_and_refusal(self, client, dotenv):
        """Functionality: Verify wrapper structured output and refusal.
        Inputs: Isolated test fixtures and explicit provider substitutes. Outputs: Assertions or
        test failure.
        Logic: Exercise the real contract under the stated boundary conditions.
        Constraints: This test does not measure live model quality or modify production records.
        """
        client.return_value.responses.parse.return_value = Mock(
            output_parsed=GeneratedText(text="What did you build and why?")
        )
        llm = OpenAILLM()
        dotenv.assert_called_once_with(Path(__file__).resolve().parents[2] / ".env")
        self.assertEqual(
            llm("Prompt", {}, GeneratedText).text,
            "What did you build and why?",
        )
        kwargs = client.return_value.responses.parse.call_args.kwargs
        self.assertIs(kwargs["text_format"], GeneratedText)
        self.assertEqual(kwargs["temperature"], 0)
        self.assertFalse(kwargs["store"])
        client.return_value.responses.parse.return_value = Mock(output_parsed=None)
        with self.assertRaises(LLMError):
            llm("Prompt", {}, GeneratedText)


if __name__ == "__main__":
    unittest.main()
