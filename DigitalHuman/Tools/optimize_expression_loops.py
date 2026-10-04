"""Create calmer, loopable copies of the recorded Listening/Thinking expressions.

Run with Unreal Editor's Python plugin. The default is read-only inspection;
pass -ApplyFaceClipOptimization to an Unreal Python commandlet to save copies
and bind them to ABP_InterviewerBody. Original recordings/bone tracks are kept.
Only expression float curves are changed. Rig/head-control curves are excluded.
"""

import json
import math
from contextlib import contextmanager
from pathlib import Path

import unreal

OUTPUT = Path(unreal.Paths.project_saved_dir()) / "ExpressionOptimization"
OUTPUT.mkdir(parents=True, exist_ok=True)
APPLY = "-ApplyFaceClipOptimization" in unreal.SystemLibrary.get_command_line()
SEAM = 0.35
ASSETS = [
    (
        "/Game/MetaHuman/FaceAnim/AS_MHP_Listening_01",
        "/Game/MetaHuman/FaceAnim/AS_MHP_Listening_Loop",
    ),
    (
        "/Game/MetaHuman/FaceAnim/AS_MHP_Thinking_01",
        "/Game/MetaHuman/FaceAnim/AS_MHP_Thinking_Loop",
    ),
]
REPORT = {"applied": APPLY, "clips": [], "body_saved": False}


@contextmanager
def curve_batch(sequence):
    """Defer derived-data rebuilds until all curve keys have been updated."""
    controller = sequence.get_editor_property("controller") if sequence else None
    if controller:
        controller.open_bracket("Optimize expression loop curves", False)
    try:
        yield
    finally:
        if controller:
            controller.close_bracket(False)


def strength(name):
    """Keep complete blinks; soften brows, idle mouth motion and eye travel separately."""
    name = str(name).lower()
    if name.startswith("ctrl_expressions_brow"):
        return 0.8
    if name.startswith(("ctrl_expressions_mouth", "ctrl_expressions_jaw")):
        return 0.75
    if name.startswith("ctrl_expressions_eyelook"):
        return 0.8
    return 1.0


def curve_value(sequence, name, time):
    return unreal.AnimationLibrary.get_float_value_at_time(sequence, name, time)


def bridge(sequence, name, time, length, minimum, maximum):
    """A wrap-aware Hermite bridge joins the final/initial 0.35 s continuously."""
    start, end = length - SEAM, SEAM
    a, b = curve_value(sequence, name, start), curve_value(sequence, name, end)
    h = 1.0 / 120.0
    da = (curve_value(sequence, name, start + h) - curve_value(sequence, name, start - h)) / (
        2.0 * h
    )
    db = (curve_value(sequence, name, end + h) - curve_value(sequence, name, end - h)) / (2.0 * h)
    wrapped = time if time >= start else time + length
    t = (wrapped - start) / (2.0 * SEAM)
    value = (
        (2 * t**3 - 3 * t**2 + 1) * a
        + (t**3 - 2 * t**2 + t) * (2 * SEAM) * da
        + (-2 * t**3 + 3 * t**2) * b
        + (t**3 - t**2) * (2 * SEAM) * db
    )
    # Signed gaze channels retain their original observed range.
    return min(maximum, max(minimum, value))


def optimize_clip(source_path, target_path):
    source = unreal.load_asset(source_path)
    if source is None:
        raise RuntimeError(f"Missing recording: {source_path}")
    length = source.get_editor_property("sequence_length")
    names = unreal.AnimationLibrary.get_animation_curve_names(
        source, unreal.RawCurveTrackTypes.RCT_FLOAT
    )
    selected = [name for name in names if str(name).lower().startswith("ctrl_expressions_")]
    metrics = {
        "source": source_path,
        "target": target_path,
        "length": length,
        "curve_count": len(selected),
        "maximum_endpoint_error_before": 0.0,
        "maximum_endpoint_error_after": 0.0,
        "blink_seam_peaks": {},
        "interior_blink_max_error": 0.0,
    }
    REPORT["clips"].append(metrics)
    if APPLY and unreal.EditorAssetLibrary.does_asset_exist(target_path):
        raise RuntimeError(f"Output already exists; inspect it before replacing: {target_path}")
    target = unreal.EditorAssetLibrary.duplicate_asset(source_path, target_path) if APPLY else None
    if APPLY and target is None:
        raise RuntimeError(f"Could not create derived recording: {target_path}")
    with curve_batch(target):
        for name in selected:
            times, values = unreal.AnimationLibrary.get_float_keys(source, name)
            if not values:
                continue
            start_value, end_value = (
                curve_value(source, name, 0.0),
                curve_value(source, name, length),
            )
            metrics["maximum_endpoint_error_before"] = max(
                metrics["maximum_endpoint_error_before"], abs(start_value - end_value)
            )
            is_blink = "eyeblink" in str(name).lower()
            if is_blink:
                seam_values = [
                    curve_value(source, name, t) for t in times if t <= SEAM or t >= length - SEAM
                ]
                metrics["blink_seam_peaks"][str(name)] = max(seam_values, default=0.0)
            if not APPLY:
                continue
            multiplier = strength(name)
            minimum, maximum = min(values), max(values)
            sample_times = set(
                float(t) for t in times if multiplier != 1.0 or t <= SEAM or t >= length - SEAM
            )
            sample_times.update([0.0, length, SEAM, length - SEAM])
            steps = math.ceil(SEAM * 60)
            for index in range(steps + 1):
                offset = min(SEAM, index / 60.0)
                sample_times.add(offset)
                sample_times.add(length - offset)
            new_times = sorted(sample_times)
            new_values = []
            for time in new_times:
                value = (
                    bridge(source, name, time, length, minimum, maximum)
                    if time <= SEAM or time >= length - SEAM
                    else curve_value(source, name, time)
                )
                new_values.append(value * multiplier)
            unreal.AnimationLibrary.add_float_curve_keys(target, name, new_times, new_values)
            error = abs(curve_value(target, name, 0.0) - curve_value(target, name, length))
            metrics["maximum_endpoint_error_after"] = max(
                metrics["maximum_endpoint_error_after"], error
            )
            if is_blink:
                for time in times:
                    if SEAM < time < length - SEAM:
                        error = abs(
                            curve_value(target, name, time) - curve_value(source, name, time)
                        )
                        metrics["interior_blink_max_error"] = max(
                            metrics["interior_blink_max_error"], error
                        )
    if APPLY:
        assert metrics["maximum_endpoint_error_after"] < 0.0001, metrics
        assert metrics["interior_blink_max_error"] < 0.0001, metrics
        assert target.get_editor_property("skeleton") == source.get_editor_property("skeleton")
        assert unreal.EditorAssetLibrary.save_loaded_asset(target, only_if_is_dirty=False)
    return target


try:
    targets = {}
    for source_path, target_path in ASSETS:
        targets[source_path] = optimize_clip(source_path, target_path)
    if APPLY:
        bp = unreal.load_asset("/Game/Blueprints/ABP_InterviewerBody")
        graph = unreal.BlueprintGraphEditor.get_graph_editor_by_name(bp, "AnimGraph")
        bound = []
        for node in graph.list_all_nodes():
            if isinstance(node, unreal.AnimGraphNode_SequencePlayer):
                settings = node.get_editor_property("node")
                sequence = settings.get_editor_property("sequence")
                if sequence and sequence.get_path_name().split(".")[0] in targets:
                    path = sequence.get_path_name().split(".")[0]
                    settings.set_editor_property("sequence", targets[path])
                    settings.set_editor_property("loop_animation", True)
                    node.set_editor_property("node", settings)
                    bound.append(path)
        assert len(bound) == 2, bound
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        assert not graph.list_nodes_with_errors()
        REPORT["body_saved"] = unreal.EditorAssetLibrary.save_loaded_asset(
            bp, only_if_is_dirty=False
        )
        assert REPORT["body_saved"]
    unreal.log("EXPRESSION_LOOP_OPTIMIZATION_COMPLETE")
finally:
    (OUTPUT / "report.json").write_text(
        json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8"
    )
