"""Preview interviewer states in an existing PIE session without cloud requests.

Run from Unreal Editor's Python command field after pressing Play.
Optionally set INTERVIEWER_PREVIEW_STATE to a supported state before execution.
The default demo cycles listening/thinking/speaking, then restores idle.
Speaking here checks the Live Link binding; it does not generate or play speech.
"""

import os
import time

import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None:
    raise RuntimeError("Press Play in L_Interview before running this preview.")

controller_class = unreal.load_class(None, "/Script/InterviewerRuntime.InterviewerController")
controllers = unreal.GameplayStatics.get_all_actors_of_class(world, controller_class)
if len(controllers) != 1:
    raise RuntimeError(f"Expected one interviewer controller; found {len(controllers)}.")
controller = controllers[0]
requested_state = os.environ.get("INTERVIEWER_PREVIEW_STATE", "").lower()
supported = {"idle", "listening", "thinking", "speaking", "interrupted"}

# Cancel a prior preview callback if this utility is run twice in the same editor.
previous_handle = getattr(unreal, "_interviewer_preview_handle", None)
if previous_handle is not None:
    unreal.unregister_slate_post_tick_callback(previous_handle)
    unreal._interviewer_preview_handle = None

if requested_state:
    if requested_state not in supported:
        raise ValueError(f"Unsupported preview state: {requested_state}")
    controller.set_state(requested_state)
    unreal.log(f"Interviewer preview: {requested_state}")
else:
    sequence = ["listening", "thinking", "speaking", "thinking", "listening", "idle"]
    progress = {"index": -1, "next_time": 0.0}

    def update_preview(delta_seconds):
        try:
            current_world = unreal.get_editor_subsystem(
                unreal.UnrealEditorSubsystem
            ).get_game_world()
            if current_world != world:
                unreal.unregister_slate_post_tick_callback(unreal._interviewer_preview_handle)
                unreal._interviewer_preview_handle = None
                return
            now = time.monotonic()
            if now < progress["next_time"]:
                return
            progress["index"] += 1
            current_state = sequence[progress["index"]]
            controller.set_state(current_state)
            unreal.log(f"Interviewer preview: {current_state}")
            if current_state == "idle":
                unreal.unregister_slate_post_tick_callback(unreal._interviewer_preview_handle)
                unreal._interviewer_preview_handle = None
            else:
                progress["next_time"] = now + 5.0
        except Exception:
            handle = getattr(unreal, "_interviewer_preview_handle", None)
            if handle is not None:
                unreal.unregister_slate_post_tick_callback(handle)
                unreal._interviewer_preview_handle = None
            raise

    unreal._interviewer_preview_handle = unreal.register_slate_post_tick_callback(update_preview)
