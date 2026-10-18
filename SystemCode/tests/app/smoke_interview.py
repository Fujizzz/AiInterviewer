"""Exercise the integrated CLI flow with canned provider outputs and scripted answers."""

from app.application import build_application
from app.cli import run_interview
from tests.app.fixtures import FixtureLLM
from tests.app.test_interview import RESUME


def main():
    answers = iter(
        [
            "I implemented collaborative filtering and measured held-out precision.",
            "I compared the design with a popularity baseline and documented tradeoffs.",
            "I isolated malformed records before aggregation and added regression tests.",
            "I profiled memory usage and changed the parser to stream input lines.",
            "I would add production monitoring and validate behavior under load.",
        ]
    )

    def read_answer(prompt):
        answer = next(answers)
        print(prompt + answer)
        return answer

    print("OFFLINE SMOKE TEST: canned outputs, not a real candidate assessment.")
    result = run_interview(
        build_application(FixtureLLM()),
        RESUME,
        read_answer=read_answer,
    )
    assert result["interview_finished"]
    assert len(result["question_history"]) == 5
    assert result["interview_state"]["status"] == "finished"


if __name__ == "__main__":
    main()
