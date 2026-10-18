"""Offline completion observer tests with controlled time and inference boundaries.

Responsibilities: verify admission, revocation, latency overlap and explicit failures.
Implementation: inject a monotonic clock and AsyncMock classifier; no network or training.
Related Modules: agents.answer_completion; real model accuracy is outside these tests.

目录：
- make_observer：Create isolated classifier and mutable virtual clock.
- admit_snapshot：Advance virtual time to admit one settled snapshot and yield to inference.
- PendingDecision：Controllable classifier boundary for in-flight race tests.
- PendingDecision.__init__：Create an unresolved decision Future.
- PendingDecision.classify：Wait for the test to explicitly release a boolean result.
- PendingDecision.request：Hold an API response for total-deadline verification.
- test_any_wording_is_eligible_and_input_is_bounded：Verify no keyword filtering and <=400 chars.
- test_silence_requires_positive_intent_and_three_seconds：Verify silence and negative
  decisions.
- test_positive_overlaps_api_time_and_announces_once：Verify the timer includes inference
  waiting.
- test_supplement_invalidates_late_positive：Verify new text/audio invalidates in-flight
  output.
- test_duplicate_callbacks_do_not_repeat_calls：Verify repeated final callbacks retain timestamp.
- test_continuous_finals_coalesce_to_latest_snapshot：Verify rapid sentences share one request.
- test_voice_during_settling_discards_unfinished_snapshot：Verify audio postpones inference.
- test_new_candidates_wait_for_single_inflight_call：Verify bounded latest-candidate
  scheduling.
- test_failure_is_explicit_and_close_cancels：Verify propagation and resource release without
  retries.
- test_classifier_contract_and_output_rejection：Verify actual API options and strict output
  schema.
- test_classifier_total_deadline_cancels_request：Verify the adapter bounds the complete
  request.
- test_finalization_stops_admission_but_keeps_revocation：Verify flush never starts new calls.

关键变量：
（无模块级变量。）
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agents.answer_completion import (
    AnswerCompletionAgent,
    FlashCompletionClassifier,
)


def make_observer(decision=True):
    """Inputs: simulated binary result. Outputs: observer, classifier and mutable clock.
    Logic: explicitly mocked inference and clock; no SDK client construction or remote
    service.
    """
    clock = Mock(return_value=0.0)
    classifier = SimpleNamespace(classify=AsyncMock(return_value=decision), close=AsyncMock())
    return AnswerCompletionAgent(classifier, clock), classifier, clock


async def admit_snapshot(observer, clock, at=0.5):
    """Inputs: observer, virtual clock and admission time. Outputs: None.
    Logic: poll scheduling then yield once; only inference is mocked, production scheduling runs.
    """
    clock.return_value = at
    observer.poll()
    await asyncio.sleep(0)


class PendingDecision:
    """Functionality: hold inference at an async boundary. Logic: await a controlled Future.
    Constraints: no HTTP calls; the test explicitly decides result order.
    """

    def __init__(self):
        """Inputs: current loop. Outputs: unresolved boolean result Future."""
        self.result = asyncio.get_running_loop().create_future()

    async def classify(self, text):
        """Inputs: unused candidate text. Outputs: test-resolved boolean; cancellation
        propagates.
        """
        return await self.result

    async def request(self, **kwargs):
        """Inputs: ignored mocked SDK arguments. Outputs: unresolved test response.
        Logic: ignore SDK phase limits to exercise the adapter's total timeout.
        """
        return await self.result


@pytest.mark.parametrize(
    "text",
    [
        "我想表达的就是这些，没有别的想说的了。",
        "That covers everything I wanted to share.",
        "I implemented a parser and tested it.",
        "I am not finished with my explanation.",
        "The phrase answer complete appears on the screen.",
        "Thank you.",
    ],
)
async def test_any_wording_is_eligible_and_input_is_bounded(text):
    """Inputs: varied ordinary, negative, technical and paraphrased final text.
    Outputs: all reach the mocked LLM after settling, with a <=400-character context.
    Constraints: this verifies admission, not semantic accuracy of the mock decision.
    """
    observer, classifier, clock = make_observer(False)
    transcript = "context " * 200 + text
    observer.observe(transcript, True)
    classifier.classify.assert_not_called()
    clock.return_value = 0.499
    assert not observer.poll()
    classifier.classify.assert_not_called()
    await admit_snapshot(observer, clock)
    classifier.classify.assert_awaited_once_with(transcript[-400:])
    await observer.close()


async def test_silence_requires_positive_intent_and_three_seconds():
    """Verify ordinary text and a negative classifier result cannot end an answer even after
    silence.
    """
    observer, classifier, clock = make_observer(False)
    observer.observe("I built a parser.", False)
    clock.return_value = 10
    assert not observer.poll()
    classifier.classify.assert_not_called()
    clock.return_value = 0
    observer.observe("I built a parser.", True)
    await admit_snapshot(observer, clock)
    clock.return_value = 10
    assert not observer.poll()
    classifier.classify.assert_awaited_once_with("I built a parser.")
    observer.observe("I am not done.", True)
    await admit_snapshot(observer, clock, 10.5)
    clock.return_value = 20
    assert not observer.poll()
    await observer.close()


async def test_positive_overlaps_api_time_and_announces_once():
    """Verify three-second monotonic boundary, one announcement and client cleanup after positive
    intent.
    """
    observer, classifier, clock = make_observer()
    observer.observe("I tested it. That's all.", True)
    await admit_snapshot(observer, clock)
    clock.return_value = 2.999
    assert not observer.poll()
    clock.return_value = 3
    assert observer.poll()
    assert not observer.poll()
    classifier.classify.assert_awaited_once()
    await observer.close()
    classifier.close.assert_awaited_once()


@pytest.mark.parametrize("supplement", ["text", "voice"])
async def test_supplement_invalidates_late_positive(supplement):
    """Verify both text and audio activity revoke an unresolved positive; stale inference is
    consumed.
    """
    observer, classifier, clock = make_observer()
    pending = PendingDecision()
    classifier.classify.side_effect = pending.classify
    observer.observe("That's all.", True)
    await admit_snapshot(observer, clock)
    clock.return_value = 2
    if supplement == "text":
        observer.observe("That's all. One more detail", False)
    else:
        observer.activity()
    pending.result.set_result(True)
    await asyncio.sleep(0)
    clock.return_value = 4
    assert not observer.poll()
    assert observer.confirmed_at is None
    await observer.close()


async def test_duplicate_callbacks_do_not_repeat_calls():
    """Verify repeated final callbacks retain the first snapshot time and never spend extra calls.
    Preconditions: one isolated observer and mocked positive classification; no real ASR/API.
    """
    observer, classifier, clock = make_observer()
    observer.observe("That's all.", False)
    classifier.classify.assert_not_called()
    observer.observe("That's all.", True)
    clock.return_value = 0.25
    observer.observe("That's all.", True)
    assert observer.queued[1] == 0
    await admit_snapshot(observer, clock)
    observer.poll()
    observer.observe("That's all.", True)
    clock.return_value = 2
    observer.poll()
    await asyncio.sleep(0)
    classifier.classify.assert_awaited_once()
    await observer.close()


async def test_continuous_finals_coalesce_to_latest_snapshot():
    """Verify finalized sentences in a short speech burst become one latest-tail call.
    Logic: update virtual time before each actual final; pending older snapshots are overwritten.
    """
    observer, classifier, clock = make_observer(False)
    observer.observe("First detail.", True)
    clock.return_value = 0.25
    observer.observe("First detail. Second detail.", True)
    clock.return_value = 0.5
    observer.observe("First detail. Second detail. Last detail.", True)
    clock.return_value = 0.999
    observer.poll()
    classifier.classify.assert_not_called()
    await admit_snapshot(observer, clock, 1)
    classifier.classify.assert_awaited_once_with("First detail. Second detail. Last detail.")
    await observer.close()


async def test_voice_during_settling_discards_unfinished_snapshot():
    """Verify continued voice invalidates a queued snapshot before ASR catches up.
    Constraints: no request is sent for this unfinished revision; later final text can be admitted.
    """
    observer, classifier, clock = make_observer(False)
    observer.observe("Initial detail.", True)
    clock.return_value = 0.4
    observer.activity()
    clock.return_value = 3
    assert not observer.poll()
    classifier.classify.assert_not_called()
    observer.observe("Initial detail. A supplement.", True)
    await admit_snapshot(observer, clock, 3.5)
    classifier.classify.assert_awaited_once_with("Initial detail. A supplement.")
    await observer.close()


async def test_new_candidates_wait_for_single_inflight_call():
    """Verify at most one task while the newest candidate waits; stale queued candidates are
    discarded.
    """
    observer, classifier, clock = make_observer()
    pending = PendingDecision()
    classifier.classify.side_effect = pending.classify
    observer.observe("That's all.", True)
    await admit_snapshot(observer, clock)
    first = observer.task
    clock.return_value = 0.6
    observer.observe("That's all. An additional detail.", True)
    clock.return_value = 0.7
    observer.observe("That's all. An additional detail. I have said everything.", True)
    assert observer.task is first
    classifier.classify.assert_awaited_once()
    pending.result.set_result(True)
    await asyncio.sleep(0)
    clock.return_value = 1.499
    assert not observer.poll()
    classifier.classify.assert_awaited_once()
    await admit_snapshot(observer, clock, 1.5)
    classifier.classify.assert_awaited_with(
        "That's all. An additional detail. I have said everything."
    )
    assert classifier.classify.await_count == 2
    await asyncio.sleep(0)
    clock.return_value = 3.7
    assert observer.poll()
    await observer.close()


async def test_failure_is_explicit_and_close_cancels():
    """Verify API failure propagates and disconnect cancels local inference; no
    retry/fallback call.
    """
    observer, classifier, clock = make_observer()
    classifier.classify.side_effect = TimeoutError("fixture timeout")
    observer.observe("That's all.", True)
    await admit_snapshot(observer, clock)
    with pytest.raises(TimeoutError):
        observer.poll()
    classifier.classify.assert_awaited_once()
    await observer.close()


async def test_classifier_contract_and_output_rejection():
    """Mock HTTP boundary only; verify no retries, 2s deadline, non-thinking mode and boolean
    schema.
    """
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())), close=AsyncMock()
    )
    choice = SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content='{"finished":true}')
    )
    client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
    with (
        patch.dict(
            "os.environ",
            {
                "DASHSCOPE_API_KEY": "fixture",
                "ANSWER_COMPLETION_MODEL": "qwen-flash",
                "ANSWER_COMPLETION_TIMEOUT_SECONDS": "2",
            },
        ),
        patch("agents.answer_completion.AsyncOpenAI", return_value=client) as construct,
    ):
        classifier = FlashCompletionClassifier()
    assert construct.call_args.kwargs["max_retries"] == 0
    assert construct.call_args.kwargs["timeout"] == 2
    assert await classifier.classify("回答完毕")
    options = client.chat.completions.create.call_args.kwargs
    assert options["max_tokens"] == 32
    assert options["extra_body"] == {"enable_thinking": False}
    assert options["messages"][-1] == {"role": "user", "content": "回答完毕"}
    choice.message.content = '{"finished":"true"}'
    with pytest.raises(ValueError):
        await classifier.classify("回答完毕")
    await classifier.close()


async def test_classifier_total_deadline_cancels_request():
    """Verify the complete API call is bounded even if SDK phase timeouts are ignored.
    Inputs: pending offline SDK request and explicit 10ms test deadline. Outputs: TimeoutError
    and cancelled request; no retry is performed. Production defaults are not modified.
    """
    pending = PendingDecision()
    create = AsyncMock(side_effect=pending.request)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock()
    )
    with (
        patch.dict(
            "os.environ",
            {"DASHSCOPE_API_KEY": "fixture", "ANSWER_COMPLETION_TIMEOUT_SECONDS": "0.01"},
        ),
        patch("agents.answer_completion.AsyncOpenAI", return_value=client),
    ):
        classifier = FlashCompletionClassifier()
    with pytest.raises(TimeoutError):
        await classifier.classify("回答完毕")
    assert pending.result.cancelled()
    create.assert_awaited_once()
    await classifier.close()


async def test_finalization_stops_admission_but_keeps_revocation():
    """Verify stop freezes API admission while changed final text can still invalidate
    confirmation.
    Inputs: resolved offline positive decision and virtual time. Outputs: no extra inference
    for late finalized text; pending transcript supplements remain visible to the observer.
    """
    observer, classifier, clock = make_observer()
    observer.observe("That's all.", True)
    await admit_snapshot(observer, clock)
    clock.return_value = 3
    assert observer.poll()
    observer.stop_admission()
    observer.observe("That's all. I am done.", True)
    await asyncio.sleep(0)
    classifier.classify.assert_awaited_once()
    assert not observer.announced
    await observer.close()
