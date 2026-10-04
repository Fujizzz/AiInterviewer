"""Preview a loop boundary in each recorded facial state in an existing PIE session.

Run from the Unreal Python command field after Play. No speech or cloud calls.
The body remains Idle; Listening and Thinking run 24 seconds each, then Idle.
"""

import time

import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None:
    raise RuntimeError("Press Play in L_Interview before running the expression preview.")
controllers = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.load_class(None, "/Script/InterviewerRuntime.InterviewerController")
)
if len(controllers) != 1:
    raise RuntimeError(f"Expected one interviewer controller, found {len(controllers)}.")
controller = controllers[0]
for handle_name in ["_interviewer_expression_preview_handle", "_interviewer_preview_handle"]:
    prior_handle = getattr(unreal, handle_name, None)
    if prior_handle is not None:
        unreal.unregister_slate_post_tick_callback(prior_handle)
        setattr(unreal, handle_name, None)
progress = {"state": "listening", "deadline": time.monotonic() + 24.0}
controller.set_state("listening")
unreal.log("Expression preview: LISTENING for 24 seconds; THINKING is next.")


def finish_preview():
    handle = getattr(unreal, "_interviewer_expression_preview_handle", None)
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
    unreal._interviewer_expression_preview_handle = None


def update_preview(delta_seconds):
    try:
        if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() != world:
            finish_preview()
            return
        if time.monotonic() < progress["deadline"]:
            return
        if progress["state"] == "listening":
            progress["state"] = "thinking"
            progress["deadline"] = time.monotonic() + 24.0
            controller.set_state("thinking")
            unreal.log("Expression preview: THINKING for 24 seconds.")
        else:
            controller.set_state("idle")
            finish_preview()
            unreal.log("Expression preview complete; returned to IDLE.")
    except Exception:
        finish_preview()
        raise


unreal._interviewer_expression_preview_handle = unreal.register_slate_post_tick_callback(
    update_preview
)
