"""Inspect or append the native head layer to the existing interviewer Body graph.

Run using Unreal's Python commandlet after building InterviewerRuntimeEditor.
Inspection is the default. -ApplyAgentHeadMotion backs up the single Body ABP,
appends one native node, compiles, validates its connections, and saves it.
Existing recordings, common Idle, main Body class and post-process are retained.
"""

import json
import shutil
from pathlib import Path

import unreal

PATH = "/Game/Blueprints/ABP_InterviewerBody"
OUTPUT = Path(unreal.Paths.project_saved_dir()) / "AgentHeadMotion"
OUTPUT.mkdir(parents=True, exist_ok=True)
APPLY = "-ApplyAgentHeadMotion" in unreal.SystemLibrary.get_command_line()
report = {"applied": APPLY, "saved": False}


def append_motion(bp, apply):
    # UE Python may expose bool + one output as Optional[str], with None on
    # failure, instead of the Blueprint-style (bool, detail) tuple.
    result = unreal.InterviewerMotionEditorLibrary.append_body_head_motion(bp, apply)
    if isinstance(result, str):
        return True, result
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), result[1]
    return False, "Native Body graph validation failed"


try:
    bp = unreal.load_asset(PATH)
    assert bp is not None, PATH
    success, detail = append_motion(bp, False)
    report["inspection"] = detail
    assert success, detail
    if APPLY:
        backup = OUTPUT / "Before" / "ABP_InterviewerBody.uasset"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(
                Path(unreal.Paths.project_content_dir()) / "Blueprints" / backup.name, backup
            )
        success, detail = append_motion(bp, True)
        report["result"] = detail
        assert success, detail
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        graph = unreal.BlueprintGraphEditor.get_graph_editor_by_name(bp, "AnimGraph")
        assert not graph.list_nodes_with_errors(), "Body animation compile failed"
        success, detail = append_motion(bp, False)
        assert success, detail
        report["saved"] = unreal.EditorAssetLibrary.save_loaded_asset(bp, only_if_is_dirty=False)
        assert report["saved"]
    unreal.log("AGENT_HEAD_MOTION_COMPLETE")
finally:
    (OUTPUT / "head-motion-graph.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
