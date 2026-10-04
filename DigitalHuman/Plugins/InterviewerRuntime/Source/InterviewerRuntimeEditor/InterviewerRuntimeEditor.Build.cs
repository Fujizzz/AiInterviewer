using UnrealBuildTool;

public class InterviewerRuntimeEditor : ModuleRules
{
    public InterviewerRuntimeEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "InterviewerRuntime", "AnimGraph", "BlueprintGraph" });
        PrivateDependencyModuleNames.AddRange(new[] { "UnrealEd", "Kismet", "KismetCompiler" });
    }
}
