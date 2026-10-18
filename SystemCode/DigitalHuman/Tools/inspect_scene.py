"""Read the currently open interview scene without changing assets or presentation.

Run from the UE Python console. The report is saved under Saved/SceneValidation.
"""

import json
from pathlib import Path

import unreal


def actor_reference(actor):
    """Return a compact reference to an editor actor, including its class and transform."""
    if actor is None:
        return None
    return {
        "label": actor.get_actor_label(),
        "path": actor.get_path_name(),
        "class": actor.get_class().get_path_name(),
        "location": str(actor.get_actor_location()),
        "rotation": str(actor.get_actor_rotation()),
    }


def main():
    """Write a read-only report of camera, controller, avatar and skeletal mesh bindings."""
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    controllers = [actor for actor in actors if isinstance(actor, unreal.InterviewerController)]
    result = {
        "world": world.get_path_name(),
        "actors": [actor_reference(actor) for actor in actors],
        "controllers": [],
    }
    for controller in controllers:
        avatar = controller.get_editor_property("avatar")
        meshes = []
        if avatar:
            for mesh in avatar.get_components_by_class(unreal.SkeletalMeshComponent):
                anim_class = mesh.get_editor_property("anim_class")
                skeletal_mesh = mesh.get_editor_property("skeletal_mesh_asset")
                meshes.append(
                    {
                        "name": mesh.get_name(),
                        "anim_class": anim_class.get_path_name() if anim_class else None,
                        "mesh": skeletal_mesh.get_path_name() if skeletal_mesh else None,
                        "animation_mode": str(mesh.get_editor_property("animation_mode")),
                    }
                )
        result["controllers"].append(
            {
                "actor": actor_reference(controller),
                "camera": actor_reference(controller.get_editor_property("interview_camera")),
                "avatar": actor_reference(avatar),
                "meshes": meshes,
            }
        )
    output_dir = Path(unreal.Paths.project_saved_dir()) / "SceneValidation"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scene.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    unreal.log("INTERVIEW_SCENE " + json.dumps(result))


main()
