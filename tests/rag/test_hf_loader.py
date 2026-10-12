import pytest

# Optional rag-extra dependencies: skip the whole module when they are not installed.
pytest.importorskip("requests")
from rag.loaders.hf_loader import _row_to_rawdoc


def test_benchmark_style_row_uses_problem_slug_tags_and_never_solution_or_tests():
    row = {
        "slug": "add-two-numbers",
        "difficulty": "Medium",
        "problem": "You are given two linked lists...",
        "tags": "['Recursion', 'Linked List', 'Math']",
        "solution": "class Solution: SECRET_SOLUTION",
        "tests": "[{'input': 'SECRET_TEST'}]",
        "source": "newfacade",
        "starter_code": "def f(): pass",
    }
    d = _row_to_rawdoc(row, "o/r", "rev", "benchmark.jsonl", 0, "https://u")
    assert d.title == "Add Two Numbers" and d.extra["difficulty"] == "Medium"
    assert d.extra["tags"] == ["Recursion", "Linked List", "Math"]
    assert d.extra["metadata"] == {"slug": "add-two-numbers", "origin": "newfacade"}
    assert (
        "SECRET" not in d.text
        and "starter" not in d.text
        and d.text.startswith("Add Two Numbers\n\nYou are given")
    )


def test_original_leetcodedataset_row_keeps_slug_and_question_id():
    row = {
        "task_id": "two-sum",
        "question_id": 1,
        "difficulty": "Easy",
        "tags": ["Array"],
        "problem_description": "Given nums...",
    }
    d = _row_to_rawdoc(row, "o/r", "rev", "t.jsonl", 3, "https://u")
    assert d.extra["metadata"] == {"slug": "two-sum", "question_id": 1} and d.extra["tags"] == [
        "Array"
    ]
