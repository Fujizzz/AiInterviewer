"""Exercise the real CLI and graph with canned model outputs and scripted answers."""
from pathlib import Path

from app.graph import build_graph
from main import run_interview
from tests.fixtures import FixtureLLM


def main():
    """Run five scripted answers through the real interview graph and check completion."""
    answers = iter([
        "I implemented item-based collaborative filtering using pandas and cosine similarity.",
        "I chose item similarity because it is explainable and can be precomputed for a small catalog.",
        "I held out interactions and checked recommendation precision against a popularity baseline.",
        "I wrote the log parser and aggregated hourly error counts, handling malformed records separately.",
        "I chose streaming line parsing to bound memory and tested malformed records with pytest.",
    ])
    def read_answer(prompt):
        """Print and return the next scripted candidate answer for the offline smoke test."""
        answer = next(answers)
        print(prompt + answer)
        return answer
    print("OFFLINE SMOKE TEST: canned model outputs, not a live LLM interview.")
    resume = (Path(__file__).resolve().parents[1] / "sample_resume.txt").read_text()
    result = run_interview(build_graph(FixtureLLM()), resume, read_answer=read_answer)
    assert result["interview_finished"] and len(result["question_history"]) == 5
    assert len({entry["topic"] for entry in result["question_history"]}) == 2


if __name__ == "__main__":
    main()
