"""Pin the native speech solver's current model for cooking.

The solver loads its model by a C++ string path, so a map dependency scan cannot
find it. A PrimaryAssetLabel includes that one model without cooking older
StreamingADA models. Run after an engine update and before packaging.
"""

import re
from pathlib import Path

import unreal


def save_label(name, paths, cook_rule):
    assets = [unreal.load_asset(path) for path in paths]
    if any(asset is None for asset in assets):
        raise RuntimeError(f"Required cook-policy assets are unavailable: {paths}")
    label = unreal.load_asset(f"/Game/RuntimeAssets/{name}")
    if label is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.PrimaryAssetLabel)
        label = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, "/Game/RuntimeAssets", unreal.PrimaryAssetLabel, factory
        )
    if label is None:
        raise RuntimeError(f"Could not create cook label: {name}")
    rules = unreal.PrimaryAssetRules()
    rules.set_editor_property("cook_rule", cook_rule)
    label.set_editor_property("rules", rules)
    label.set_editor_property("label_assets_in_my_directory", False)
    label.set_editor_property("is_runtime_label", False)
    label.set_editor_property("explicit_assets", assets)
    if not unreal.EditorAssetLibrary.save_loaded_asset(label, False):
        raise RuntimeError(f"Could not save cook label: {name}")


def main():
    engine = Path(unreal.Paths.engine_dir()).resolve()
    solver = (
        engine / "Plugins/Animation/AudioDrivenAnimation/StreamingADA"
        / "Source/SpeechAnimationSolver/Private/ISpeechAnimationSolver.cpp"
    )
    match = re.search(r'return\s+"(/StreamingADA/[^\"]+)"', solver.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(
            "Cannot identify this engine's native speech model; "
            "retain the existing cook configuration."
        )
    model_path = match.group(1)
    save_label("PAL_InterviewerSpeechModel", [model_path], unreal.PrimaryAssetCookRule.ALWAYS_COOK)
    # Video face-capture inference is an editor workflow, not part of the
    # deployed TTS interviewer. Exclude only its three neural models; shared
    # DefaultSmoothing/HeavySmoothing remain available to Live Link.
    save_label("PAL_EditorVideoTrackingModels", [
        "/MetaHumanCoreTech/RealtimeMono/HeadPoseTracker.HeadPoseTracker",
        "/MetaHumanCoreTech/RealtimeMono/GenericRigSolver.GenericRigSolver",
        "/MetaHumanCoreTech/RealtimeMono/StereoHMCRigSolver.StereoHMCRigSolver",
    ], unreal.PrimaryAssetCookRule.NEVER_COOK)
    unreal.log(f"INTERVIEWER_COOK_MODEL {model_path}")


main()
