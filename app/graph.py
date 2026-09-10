"""Build the interview graph and control input pauses, history, and routing."""
from functools import partial

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.answer_analyzer import analyze_answer
from app.agents.evaluator import evaluate_interview
from app.agents.question_agent import generate_question
from app.agents.resume_parser import parse_resume
from app.llm import OpenAILLM, StructuredLLM
from app.state import InterviewState


def ask_candidate(state: InterviewState) -> dict:
    """Pause the graph for candidate input and return a validated nonempty answer."""
    answer = interrupt({"question": state["current_question"]})
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Answer must be nonempty text.")
    return {"current_answer": answer.strip()}


def save_history(state: InterviewState) -> dict:
    """Append the current topic, question, answer, and analysis without mutating prior history."""
    entry = {
        "topic": state["topics"][state["topic_index"]],
        "question": state["current_question"], "answer": state["current_answer"],
        "analysis": state["answer_analysis"],
    }
    return {"question_history": [*state["question_history"], entry]}


def decide_next_step(state: InterviewState) -> str:
    """Choose finish, follow-up, or the next topic using question and follow-up limits."""
    if len(state["question_history"]) >= state["max_questions"]:
        return "finish"
    if (state["answer_analysis"]["suggest_follow_up"]
            and state["follow_up_count"] < state["max_follow_up_per_topic"]):
        return "follow_up"
    if state["topic_index"] < len(state["topics"]) - 1:
        return "next_topic"
    return "finish"


def decision(state: InterviewState) -> dict:
    """Record the routing decision and update the topic or follow-up counter."""
    route = decide_next_step(state)
    update = {"decision": route}
    if route == "follow_up":
        update["follow_up_count"] = state["follow_up_count"] + 1
    elif route == "next_topic":
        update.update(topic_index=state["topic_index"] + 1, follow_up_count=0)
    return update


def build_graph(llm: StructuredLLM | None = None):
    """Bind the model to four LLM nodes and compile the graph with in-memory checkpoints."""
    model = llm if llm is not None else OpenAILLM()
    builder = StateGraph(InterviewState)
    for name, node in (
        ("resume_parser", parse_resume), ("question_agent", generate_question),
        ("answer_analyzer", analyze_answer), ("evaluator", evaluate_interview),
    ):
        builder.add_node(name, partial(node, llm=model))
    builder.add_node("ask_candidate", ask_candidate)
    builder.add_node("save_history", save_history)
    builder.add_node("decision", decision)
    for source, target in (
        (START, "resume_parser"), ("resume_parser", "question_agent"),
        ("question_agent", "ask_candidate"), ("ask_candidate", "answer_analyzer"),
        ("answer_analyzer", "save_history"), ("save_history", "decision"),
        ("evaluator", END),
    ):
        builder.add_edge(source, target)
    builder.add_conditional_edges("decision", lambda state: state["decision"], {
        "follow_up": "question_agent", "next_topic": "question_agent", "finish": "evaluator",
    })
    return builder.compile(checkpointer=InMemorySaver())
