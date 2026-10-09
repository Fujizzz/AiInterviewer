"""Report package dependencies without changing assets.

Run with UnrealEditor-Cmd -run=pythonscript -script=<this file> -nullrhi.
The report includes editor references: absence from a cooked build alone is not
proof that a source asset can be deleted. Recorded performances and identities
must also be retained for future edits.
"""

import json
from pathlib import Path

import unreal


def main():
    project = Path(unreal.Paths.project_dir()).resolve()
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.search_all_assets(True)
    registry.scan_paths_synchronous(["/Game", "/StreamingADA"], True)
    options = unreal.AssetRegistryDependencyOptions()
    for field in (
        "include_soft_package_references",
        "include_hard_package_references",
        "include_game_package_references",
        "include_editor_only_package_references",
        "include_soft_management_references",
        "include_hard_management_references",
    ):
        options.set_editor_property(field, True)

    packages = {}
    for asset in registry.get_assets_by_path("/Game", True, True):
        package = str(asset.package_name)
        packages.setdefault(package, {
            "class": str(asset.asset_class_path),
            "dependencies": sorted(
                str(value) for value in registry.get_dependencies(package, options)
            ),
        })
    for package, info in packages.items():
        info["referencers"] = sorted(
            str(value) for value in registry.get_referencers(package, options)
        )
        stem = project / "Content" / package.removeprefix("/Game/")
        files = [stem.with_suffix(suffix) for suffix in (".uasset", ".umap", ".uexp", ".ubulk")]
        info["files"] = [str(file.relative_to(project)) for file in files if file.is_file()]
        info["bytes"] = sum(file.stat().st_size for file in files if file.is_file())

    # Seed every other asset, including source Characters, identities, capture
    # data and editor skeletons. Only a wholly unreferenced part of the old
    # assembled character is eligible for cleanup.
    old_prefix = "/Game/MetaHumans/NewMetaHumanCharacter/"
    pending = [package for package in packages if not package.startswith(old_prefix)]
    reachable = set(pending)
    while pending:
        for dependency in packages.get(pending.pop(), {}).get("dependencies", []):
            if dependency in packages and dependency not in reachable:
                reachable.add(dependency)
                pending.append(dependency)
    candidates = sorted(
        package for package in packages
        if package.startswith(old_prefix) and package not in reachable
    )
    superseded = [
        package for package in (
            "/Game/MetaHumans/MHC_Hannah/Clothing/MHC_Hannah_Outfits",
            "/Game/MetaHumans/MHC_Hannah/Anims/Standing_Idle_Anim",
        ) if package in packages and not packages[package]["referencers"]
    ]

    scene = None
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if level.load_level("/Game/Maps/L_Interview"):
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        scene = []
        for actor in actors:
            if isinstance(actor, unreal.InterviewerController):
                avatar = actor.get_editor_property("avatar")
                camera = actor.get_editor_property("interview_camera")
                meshes = []
                if avatar:
                    for mesh in avatar.get_components_by_class(unreal.SkeletalMeshComponent):
                        anim_class = mesh.get_editor_property("anim_class")
                        skeletal_mesh = mesh.get_editor_property("skeletal_mesh_asset")
                        meshes.append({
                            "component": mesh.get_name(),
                            "anim_class": anim_class.get_path_name() if anim_class else None,
                            "mesh": skeletal_mesh.get_path_name() if skeletal_mesh else None,
                        })
                scene.append({
                    "controller": actor.get_path_name(),
                    "avatar_class": avatar.get_class().get_path_name() if avatar else None,
                    "camera": camera.get_path_name() if camera else None,
                    "meshes": meshes,
                })

    result = {
        "packages": packages,
        "old_assembled_cleanup_candidates": candidates,
        "unreferenced_superseded_assets": superseded,
        "candidate_bytes": sum(packages[package]["bytes"] for package in candidates),
        "scene": scene,
    }
    output = project / "Saved" / "AssetCleanup" / "asset-usage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    unreal.log(
        f"ASSET_USAGE_REPORT {output}: {len(packages)} packages, {len(candidates)} candidates"
    )


main()
