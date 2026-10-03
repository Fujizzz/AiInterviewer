import json
from pathlib import Path

import pytest


@pytest.fixture
def example():
    return json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("evidence_to_assessment.json")
        .read_text(encoding="utf-8")
    )
