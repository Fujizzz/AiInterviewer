"""Immutable, allowlisted inputs; no candidate scores, job weights or resume claims."""

from collections.abc import Iterable
from typing import Self

from pydantic import Field, model_validator

from evaluation.contracts import EvaluationModel, EvidenceItem, Text, require_unique
from shared.contracts import CandidateAnswer, EvaluationRequest, PlannedQuestion
from shared.contracts.planning import TopicAllocation

MAX_HISTORY_ANSWERS = 10
MAX_EVIDENCE_INDEX = 50


class QuestionSnapshot(EvaluationModel):
    question_id: Text
    thread_id: Text
    project_id: Text | None
    topic_key: str
    text: Text
    information_goal: str

    @classmethod
    def from_question(cls, question: PlannedQuestion) -> Self:
        return cls(
            question_id=question.question_id,
            thread_id=question.thread_id or question.question_id,
            project_id=question.project_id,
            topic_key=question.topic_key,
            text=question.text,
            information_goal=question.information_goal,
        )


class AnswerSnapshot(EvaluationModel):
    interview_id: Text
    question_id: Text
    answer_id: Text
    text: str  # Preserve whitespace and Unicode for quote offsets, including empty answers.

    @classmethod
    def from_answer(cls, answer: CandidateAnswer) -> Self:
        return cls(**answer.model_dump(exclude={"contract_version"}))

    def as_candidate_answer(self) -> CandidateAnswer:
        return CandidateAnswer(**self.model_dump())


class ThreadTurn(EvaluationModel):
    question: QuestionSnapshot
    answer: AnswerSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.question.question_id != self.answer.question_id:
            raise ValueError("history answer must belong to its question")
        return self


class TopicObjective(EvaluationModel):
    project_id: Text | None
    topic_key: Text
    objective: Text
    completion_criteria: Text


class EvidenceIndexEntry(EvaluationModel):
    evidence_id: Text
    answer_id: Text
    thread_id: Text
    project_id: Text | None
    normalized_claim: Text


class EvaluationInput(EvaluationModel):
    request_id: Text
    interview_id: Text
    question: QuestionSnapshot
    answer: AnswerSnapshot
    topic: TopicObjective | None = None
    history: tuple[ThreadTurn, ...] = Field(default=(), max_length=MAX_HISTORY_ANSWERS)
    existing_evidence: tuple[EvidenceIndexEntry, ...] = Field(
        default=(), max_length=MAX_EVIDENCE_INDEX
    )

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if (self.answer.interview_id, self.answer.question_id) != (
            self.interview_id,
            self.question.question_id,
        ):
            raise ValueError("current answer must belong to the interview and question")
        if self.topic and (self.topic.project_id, self.topic.topic_key) != (
            self.question.project_id,
            self.question.topic_key,
        ):
            raise ValueError("Planner objective must belong to the current topic")
        for turn in self.history:
            if turn.answer.interview_id != self.interview_id or (
                turn.question.thread_id,
                turn.question.project_id,
            ) != (self.question.thread_id, self.question.project_id):
                raise ValueError("history must contain only the current interview thread")
        answer_ids = [self.answer.answer_id, *(turn.answer.answer_id for turn in self.history)]
        require_unique(answer_ids, "answer_ids")
        require_unique([item.evidence_id for item in self.existing_evidence], "evidence_ids")
        for item in self.existing_evidence:
            if (item.thread_id, item.project_id) != (
                self.question.thread_id,
                self.question.project_id,
            ) or item.answer_id == self.answer.answer_id:
                raise ValueError("evidence index must contain only previous thread evidence")
        return self

    @classmethod
    def from_request(
        cls,
        request: EvaluationRequest,
        *,
        topic: TopicAllocation | None = None,
        history: Iterable[tuple[PlannedQuestion, CandidateAnswer]] = (),
        existing_evidence: Iterable[EvidenceItem] = (),
    ) -> Self:
        """Copy shared models and retain the last bounded current-thread entries.

        Callers supply history in chronological order and evidence from this interview.
        Passing a stale/mismatched Planner allocation is an error, never a fallback.
        """
        question = QuestionSnapshot.from_question(request.question)
        turns = [
            ThreadTurn(
                question=QuestionSnapshot.from_question(prior_question),
                answer=AnswerSnapshot.from_answer(answer),
            )
            for prior_question, answer in history
            if answer.interview_id == request.interview_id
            and answer.answer_id != request.answer.answer_id
            and (prior_question.thread_id or prior_question.question_id) == question.thread_id
            and prior_question.project_id == question.project_id
        ]
        index = [
            EvidenceIndexEntry(**item.model_dump(include=set(EvidenceIndexEntry.model_fields)))
            for item in existing_evidence
            if item.thread_id == question.thread_id
            and item.project_id == question.project_id
            and item.answer_id != request.answer.answer_id
        ]
        return cls(
            request_id=request.request_id,
            interview_id=request.interview_id,
            question=question,
            answer=AnswerSnapshot.from_answer(request.answer),
            topic=TopicObjective(**topic.model_dump(include=set(TopicObjective.model_fields)))
            if topic
            else None,
            history=tuple(turns[-MAX_HISTORY_ANSWERS:]),
            existing_evidence=tuple(index[-MAX_EVIDENCE_INDEX:]),
        )

    def conversation_payload(self) -> dict:
        return self.model_dump(mode="json", include={"question", "answer", "topic", "history"})

    def extraction_payload(self) -> dict:
        # Planner completion decisions and analysis labels cannot bias extraction.
        return self.model_dump(
            mode="json", include={"question", "answer", "history", "existing_evidence"}
        )
