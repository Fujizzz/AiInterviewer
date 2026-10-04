"""Keep recorded face curves available to the persistent speaking face instance.

Run in Unreal's Python commandlet. Inspection is the default; pass
-ApplySpeakingExpressionBlend to save ABP_InterviewerBody. The body pose,
head-control fix, original recordings and face post-process are retained.
"""

import json
import shutil
from pathlib import Path

import unreal

PATH = "/Game/Blueprints/ABP_InterviewerBody"
OUTPUT = Path(unreal.Paths.project_saved_dir()) / "FaceTransitionFix"
OUTPUT.mkdir(parents=True, exist_ok=True)
APPLY = "-ApplySpeakingExpressionBlend" in unreal.SystemLibrary.get_command_line()
report = {"applied": APPLY, "saved": False}

try:
    bp = unreal.load_asset(PATH)
    assert bp is not None, PATH
    graph = unreal.BlueprintGraphEditor.get_graph_editor_by_name(bp, "AnimGraph")
    layers = [
        node
        for node in graph.list_all_nodes()
        if isinstance(node, unreal.AnimGraphNode_LayeredBoneBlend)
    ]
    assert len(layers) == 1, "Review the changed graph before updating its blend weight"
    weight = layers[0].find_input_pin("BlendWeights_0")
    assert weight.is_valid()
    report["previous_connections"] = [
        str(pin.get_owning_node().get_name()) for pin in weight.list_connected_pins()
    ]
    report["previous_value"] = weight.get_pin_value()

    clips = []
    for node in graph.list_all_nodes():
        if isinstance(node, unreal.AnimGraphNode_SequencePlayer):
            sequence = node.get_editor_property("node").get_editor_property("sequence")
            if sequence:
                clips.append(sequence.get_path_name().split(".")[0])
    assert "/Game/MetaHuman/FaceAnim/AS_MHP_Listening_Loop" in clips, clips
    assert "/Game/MetaHuman/FaceAnim/AS_MHP_Thinking_Loop" in clips, clips
    report["clips"] = clips

    if APPLY:
        backup = OUTPUT / "BeforeStage2" / "ABP_InterviewerBody.uasset"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(
                Path(unreal.Paths.project_content_dir())
                / "Blueprints"
                / "ABP_InterviewerBody.uasset",
                backup,
            )
        weight.break_pin_links()
        assert weight.set_pin_value("1.0")
        settings = layers[0].get_editor_property("node")
        settings.set_editor_property("blend_weights", [1.0])
        layers[0].set_editor_property("node", settings)
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        assert not graph.list_nodes_with_errors()
        report["saved"] = unreal.EditorAssetLibrary.save_loaded_asset(bp, only_if_is_dirty=False)
        assert report["saved"]

    report["connections"] = len(weight.list_connected_pins())
    report["value"] = weight.get_pin_value()
    if APPLY:
        assert report["connections"] == 0
        assert abs(float(report["value"]) - 1.0) < 0.0001
    unreal.log("SPEAKING_EXPRESSION_BLEND_COMPLETE")
finally:
    (OUTPUT / "body-blend.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
