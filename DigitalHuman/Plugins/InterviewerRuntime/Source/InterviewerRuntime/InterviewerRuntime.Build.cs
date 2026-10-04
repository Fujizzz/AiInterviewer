using UnrealBuildTool;

public class InterviewerRuntime : ModuleRules
{
    public InterviewerRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] {
            "HTTP", "Json", "JsonUtilities", "LiveLink", "LiveLinkInterface", "AnimGraphRuntime",
            "MetaHumanLocalLiveLinkSource", "MetaHumanLiveLinkSource", "MetaHumanPipelineCore",
            "SpeechAnimationSolver", "AudioPlatformConfiguration", "NNE", "NNERuntimeORT", "Projects", "MetaHumanCoreTech"
        });
    }
}
