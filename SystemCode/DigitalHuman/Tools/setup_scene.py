"""Create the fixed interview set and bind an already assembled MetaHuman.

Run from Unreal's Python console with exec(open(<absolute path>).read()), or with
UnrealEditor-Cmd -run=pythonscript -script=<path> -AllowCommandletRendering.
Existing maps and actor assets are reused. Rigging and texture downloads require
the owner's Epic login and are deliberately left to MetaHuman Creator.
"""

import json
import os
from pathlib import Path

import unreal

MAP = "/Game/Maps/L_Interview"
CONTROLLER = "/Game/Blueprints/BP_InterviewerController"


def make_actor(actor_class, label, position, rotation=(0, 0, 0)):
    """Create one editor actor with explicit presentation coordinates."""
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        actor_class, unreal.Vector(*position), unreal.Rotator(*rotation)
    )
    actor.set_actor_label(label)
    return actor


def cube(label, location, scale):
    """Add simple engine geometry without marketplace dependencies."""
    actor = make_actor(unreal.StaticMeshActor, label, location)
    actor.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
    actor.set_actor_scale3d(unreal.Vector(*scale))
    return actor


def choose_avatar():
    """Use an explicit asset or a unique assembled actor Blueprint, never the Character asset."""
    explicit = os.environ.get("INTERVIEWER_AVATAR_ASSET", "")
    if explicit:
        return explicit
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.search_all_assets(True)
    candidates = []
    for data in registry.get_assets_by_path("/Game", recursive=True):
        if str(data.asset_name).startswith("BP_") and "MetaHuman" in str(data.package_path):
            if str(data.asset_class_path.asset_name) == "Blueprint":
                candidates.append(str(data.package_name))
    return candidates[0] if len(candidates) == 1 else None


def main():
    """Save a reproducible scene and a machine-readable setup result."""
    assets = unreal.EditorAssetLibrary
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if assets.does_asset_exist(MAP):
        level.load_level(MAP)
    else:
        if not level.new_level(MAP):
            raise RuntimeError("Could not create L_Interview")
        cube("InterviewFloor", (0, 0, -12), (8, 8, 0.2))
        cube("InterviewBackdrop", (-150, 0, 160), (0.1, 8, 4))
        camera = make_actor(unreal.CineCameraActor, "InterviewCamera", (215, 0, 158), (-2, 180, 0))
        camera.get_cine_camera_component().set_editor_property("current_focal_length", 50.0)
        camera.get_cine_camera_component().set_editor_property("current_aperture", 8.0)
        key = make_actor(unreal.RectLight, "InterviewKeyLight", (120, -100, 210), (-25, 140, 0))
        key.light_component.set_editor_property("intensity", 8.0)
        key.light_component.set_editor_property("source_width", 100.0)
        key.light_component.set_editor_property("source_height", 100.0)
        fill = make_actor(unreal.RectLight, "InterviewFillLight", (110, 110, 180), (-10, -140, 0))
        fill.light_component.set_editor_property("intensity", 3.0)
        make_actor(unreal.SkyLight, "InterviewAmbientLight", (0, 0, 250))

    if not assets.does_asset_exist(CONTROLLER):
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", unreal.InterviewerController)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            "BP_InterviewerController", "/Game/Blueprints", unreal.Blueprint, factory
        )
        assets.save_loaded_asset(blueprint)

    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    control = next((a for a in actors if isinstance(a, unreal.InterviewerController)), None)
    if control is None:
        control = make_actor(
            assets.load_blueprint_class(CONTROLLER), "InterviewerController", (0, 0, 0)
        )
    camera = next(a for a in actors if a.get_actor_label() == "InterviewCamera")
    control.set_editor_property("interview_camera", camera)

    avatar_path = choose_avatar()
    avatar = control.get_editor_property("avatar")
    if avatar is None and avatar_path:
        avatar_class = assets.load_blueprint_class(avatar_path)
        if not avatar_class:
            raise RuntimeError("INTERVIEWER_AVATAR_ASSET must be an assembled actor Blueprint")
        avatar = make_actor(avatar_class, "InterviewerAvatar", (0, 0, 0))
        control.set_editor_property("avatar", avatar)

    # Rig and high-resolution texture state are available only after loading the
    # character into its editor subsystem. This read does not issue cloud requests.
    character = unreal.load_asset("/Game/MetaHuman/NewMetaHumanCharacter")
    character_ready = False
    if character:
        subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
        was_editing = subsystem.is_object_added_for_editing(character)
        if was_editing or subsystem.try_add_object_to_edit(character):
            character_ready = subsystem.can_build_meta_human(character, False)
            if not was_editing:
                subsystem.remove_object_to_edit(character)

    if not level.save_current_level():
        raise RuntimeError("Could not save L_Interview")
    result = {
        "map": MAP,
        "controller": CONTROLLER,
        "avatar_asset": avatar_path,
        "avatar_bound": avatar is not None,
        "character_ready_for_assembly": bool(character_ready),
        "next_step": "Run Play and verify framing"
        if avatar
        else "Rig, download textures and assemble the MetaHuman; rerun this script",
    }
    output = Path(unreal.Paths.project_saved_dir()) / "InterviewSetup.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    unreal.log("INTERVIEW_SETUP " + json.dumps(result))


main()
