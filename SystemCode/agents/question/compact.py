"""Small, deduplicated question views; durable interview state is never truncated."""

import re


def terms(text):
    words = set(re.findall(r"[a-z][a-z0-9_+.-]{2,}", text.casefold())) - {
        "the",
        "and",
        "this",
        "that",
        "with",
        "for",
        "from",
        "have",
        "your",
        "you",
        "what",
        "which",
        "how",
        "did",
        "does",
        "could",
        "would",
        "can",
        "cannot",
        "not",
        "provide",
        "private",
        "specific",
        "precise",
        "exact",
        "detail",
        "details",
        "implementation",
        "mechanism",
        "about",
        "quote",
        "was",
        "were",
    }
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        words.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return words


def prepared_use_payload(item, plan, context, previous_questions=()):
    """Admit one already reviewed question; unrelated projects are not a second audit."""
    latest = context.question_history[-1] if context.question_history else None
    wanted = terms(item.information_goal + " " + item.text)
    entries = [
        e
        for e in context.question_history
        if e is not latest and e.answer and e.question.project_id == plan.project_id
    ]
    entries = sorted(
        entries,
        key=lambda e: -len(wanted & terms(e.question.information_goal + " " + e.answer.text)),
    )
    related = [
        e for e in entries if wanted & terms(e.question.information_goal + " " + e.answer.text)
    ][:2]
    payload = {
        "candidate_question": item.text,
        "question_kind": item.kind,
        "question_goal": item.information_goal,
        "current_target": plan.information_goal,
    }
    if latest and latest.answer:
        payload["latest_turn"] = {
            "source_id": latest.question.question_id,
            "question": latest.question.text,
            "answer": latest.answer.text,
        }
    if related:
        payload["related_answers"] = [
            {
                "source_id": e.question.question_id,
                "goal": e.question.information_goal,
                "answer": e.answer.text,
            }
            for e in related
        ]
    if context.introduction_answer:
        payload["introduction"] = context.introduction_answer.text
        payload["introduction_source_id"] = "introduction"
    asked = list(
        dict.fromkeys(
            q.text
            for q in [*previous_questions, *(e.question for e in context.question_history)]
            if q.project_id == plan.project_id
        )
    )
    if asked:
        payload["asked_questions"] = asked
    progress = context.topic_progress.get(plan.topic_key)
    blocked = [
        n.target for n in getattr(progress, "information_needs", []) if n.status == "blocked"
    ]
    if blocked:
        payload["unavailable_details"] = blocked
    return payload


def related_records(interview, project_id, *, thread_id=None, target=""):
    """Keep recent turns plus older matching, unavailable and accepted evidence."""
    entries = [e for e in interview.question_history if e.question.project_id == project_id]
    protected = set()
    for progress in interview.topic_progress.values():
        protected.update(getattr(progress, "evidence_answer_ids", []))
        protected.update(p["answer_id"] for p in progress.coverage_evidence)
    wanted = terms(target)
    selected = []
    for entry in entries:
        analysis = entry.feedback.analysis
        text = f"{entry.question.information_goal} {entry.question.text}"
        same_thread = thread_id and entry.question.thread_id == thread_id
        if (
            entry in entries[-3:]
            or same_thread
            or (entry.answer and entry.answer.answer_id in protected)
            or analysis.status in {"explicit_unknown", "refusal"}
            or analysis.limitations
            or analysis.contradictions
            or len(wanted & terms(text)) >= 2
        ):
            selected.append(entry)
    return selected


def resume_sources(project, target, limit):
    if project is None:
        return []
    sources = [
        {"source_id": f"resume:project:{project.project_id}:name", "text": project.name},
    ]
    if project.description:
        sources.append(
            {
                "source_id": f"resume:description:{project.project_id}",
                "text": project.description[:limit],
            }
        )
    wanted = terms(target)
    claims = sorted(project.claims, key=lambda c: -len(wanted & terms(c.text)))[:6]
    sources.extend(
        {"source_id": f"resume:claim:{c.claim_id}", "text": c.text[:limit]} for c in claims
    )
    if project.technologies:
        sources.append(
            {
                "source_id": f"resume:technologies:{project.project_id}",
                "text": ", ".join(project.technologies[:12]),
            }
        )
    if project.metrics:
        sources.append(
            {
                "source_id": f"resume:metrics:{project.project_id}",
                "text": "; ".join(project.metrics[:6])[:limit],
            }
        )
    return sources


def quality_payload(question, interview, settings, previous_questions=(), evidence=()):
    limit = settings.question_agent.text_char_limit
    project = next(
        (p for p in interview.candidate_profile.projects if p.project_id == question.project_id),
        None,
    )
    entries = related_records(
        interview,
        question.project_id,
        thread_id=question.thread_id,
        target=question.information_goal,
    )
    records = {}
    for e in entries:
        valid = getattr(e.feedback, "analysis_status", "valid") == "valid"
        records[e.question.question_id] = {
            "question_id": e.question.question_id,
            "text": e.question.text,
            "answer_id": e.answer.answer_id if e.answer else None,
            "answer": e.answer.text[:limit] if e.answer else None,
            "status": e.feedback.analysis.status if valid else "unassessed",
            "limitations": e.feedback.analysis.limitations if valid else [],
        }
    # Old questions without an answer are a compact index, never proof of coverage.
    index = {
        q.question_id: {"question_id": q.question_id, "text": q.text}
        for q in previous_questions
        if q.project_id == question.project_id and q.question_id not in records
    }
    index.update(
        {
            e.question.question_id: {"question_id": e.question.question_id, "text": e.question.text}
            for e in interview.question_history
            if e.question.project_id == question.project_id
            and e.question.question_id not in records
        }
    )
    return {
        "candidate_question": question.text,
        "target": question.information_goal,
        "topic": question.topic,
        "question_purpose": "technical_knowledge"
        if question.need_id.startswith("knowledge:")
        else "project_experience",
        "resume_sources": resume_sources(project, question.information_goal, limit),
        "reference_sources": [
            {"source_id": f"knowledge:{c.chunk_id}", "text": c.content[:limit]} for c in evidence
        ],
        "previous_questions": list(records.values()),
        "older_question_index": list(index.values()),
        "unavailable_information": [
            {"target": n.target, "evidence": n.blocked_evidence}
            for n in getattr(
                interview.topic_progress.get(question.topic_key), "information_needs", []
            )
            if n.status == "blocked"
        ],
    }


def writing_payload(payload, interview, settings):
    """Keep protocol IDs and read-only tools, but remove duplicated model views."""
    latest = payload.get("latest_turn")
    if latest:
        latest["question"] = {
            k: latest["question"][k]
            for k in ("question_id", "text", "project_id", "topic_key", "thread_id")
        }
        latest["analysis"] = {
            k: v
            for k, v in latest["analysis"].items()
            if k
            in {
                "status",
                "answer_scope",
                "thread_complete",
                "missing_information",
                "limitations",
                "contradictions",
                "new_information",
            }
        }
    if payload.get("followup_brief"):
        payload["followup_brief"].pop("latest_answer", None)
        payload["followup_brief"].pop("focus", None)
    intent = payload.get("locked_intent")
    if intent:
        payload.pop("question_plan", None)
        payload["locked_intent"] = {
            k: intent[k]
            for k in (
                "intent_id",
                "project_id",
                "topic_key",
                "topic",
                "information_goal",
                "answer_unit",
                "need_id",
                "dialogue_action",
                "difficulty",
            )
        }
    view = payload.get("dialogue_state")
    if view:
        view.pop("agenda", None)
        view.pop("previous_topics", None)
        preferred = {
            s.get("topic_key")
            for s in (payload.get("writing_brief") or {}).values()
            if isinstance(s, dict)
        }
        for project in view["projects"]:
            # Retain an executable route list; details can still be fetched by tools.
            project["topics"] = sorted(
                project["topics"], key=lambda t: t["topic_key"] not in preferred
            )[:3]
            for topic in project["topics"]:
                topic["information_needs"] = [
                    {k: need[k] for k in ("need_id", "target", "answer_unit")}
                    for need in topic["information_needs"][:2]
                ]
    scope = (
        (payload.get("writing_brief") or {}).get("active_scope")
        or (payload.get("writing_brief") or {}).get("next_available_scope")
        or intent
        or {}
    )
    project = next(
        (
            p
            for p in interview.candidate_profile.projects
            if p.project_id == scope.get("project_id")
        ),
        None,
    )
    payload["resume_sources"] = resume_sources(
        project, scope.get("topic", ""), settings.question_agent.text_char_limit
    )
    payload["asked_questions"] = [
        {"question_id": e.question.question_id, "text": e.question.text}
        for e in interview.question_history
    ]
    latest_id = ((latest or {}).get("answer") or {}).get("answer_id")
    payload["related_answers"] = [
        {
            "question_id": e.question.question_id,
            "answer_id": e.answer.answer_id,
            "text": e.answer.text[: settings.question_agent.text_char_limit],
            "limitations": e.feedback.analysis.limitations,
        }
        for e in related_records(
            interview,
            scope.get("project_id"),
            thread_id=scope.get("thread_id"),
            target=scope.get("topic", ""),
        )
        if e.answer and e.answer.answer_id != latest_id
    ]
    # Only the most recent rejected draft is actionable; the executor keeps all rejects.
    payload["rejected_attempts"] = [
        {"errors": a["errors"], "selection": a["selection"]}
        for a in payload["rejected_attempts"][-1:]
    ]
    return payload
