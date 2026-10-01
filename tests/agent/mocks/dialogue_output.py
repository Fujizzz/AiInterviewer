"""Deterministic model selection fixture; never used by production policy."""


def selection_for(payload):
    view = payload["dialogue_state"]
    thread = view["active_thread"]
    latest = payload["latest_turn"]
    analysis = latest["analysis"] if latest else {}
    goal = (analysis.get("missing_information") or ["Explain one concrete implementation step"])[0]
    if analysis.get("contradictions"):
        goal = "Clarify these differing accounts: " + analysis["contradictions"][0]
    incomplete = (
        analysis.get("status") in {"partial", "non_answer"}
        or analysis.get("missing_information")
        or analysis.get("contradictions")
        or not analysis.get("thread_complete", False)
    )
    if (
        thread
        and latest
        and not view["followup_block"]
        and incomplete
        and goal not in thread["goals"]
    ):
        action = (
            "clarify"
            if (
                analysis.get("status") in {"partial", "non_answer"}
                or analysis.get("contradictions")
            )
            else "probe"
        )
        return dict(
            dialogue_action=action,
            project_id=thread["project_id"],
            topic_key=thread["topic_key"],
            information_goal=goal,
            decision_summary="The previous answer leaves a specific detail unresolved.",
        )
    projects = [p for p in view["projects"] if p["topics"]]
    if view["followup_block"] in {"NO_NEW_INFORMATION", "CANDIDATE_STOPPED_THREAD"}:
        alternatives = [
            p for p in projects if not thread or p["project_id"] != thread["project_id"]
        ]
        projects = alternatives or projects
    project = projects[0] if projects else None
    project_id = project["project_id"] if project else None
    return dict(
        dialogue_action="new_project"
        if thread and project_id != thread["project_id"]
        else "new_topic",
        project_id=project_id,
        topic_key=project["topics"][0]["topic_key"] if project else view["general_topic_key"],
        information_goal="Describe one concrete implementation for this topic",
        decision_summary="Explore the next available resume topic.",
    )


def plan_for(payload, selection):
    view = payload["dialogue_state"]
    topic = next(
        (
            t["label"]
            for p in view["projects"]
            for t in p["topics"]
            if t["topic_key"] == selection["topic_key"]
        ),
        None,
    )
    topic = topic or (view["active_thread"] or {}).get("topic") or "your experience"
    if selection["dialogue_action"] in {"clarify", "probe"}:
        topic = selection["information_goal"]
    return {"topic": topic, "question_type": "implementation"}
