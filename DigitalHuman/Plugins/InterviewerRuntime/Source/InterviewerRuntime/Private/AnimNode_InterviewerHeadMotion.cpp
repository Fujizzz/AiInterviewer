#include "AnimNode_InterviewerHeadMotion.h"
#include "InterviewerMotionComponent.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimInstanceProxy.h"
#include "GameFramework/Actor.h"

FAnimNode_InterviewerHeadMotion::FAnimNode_InterviewerHeadMotion()
{
    NeckOne.BoneName = TEXT("neck_01");
    NeckTwo.BoneName = TEXT("neck_02");
    Head.BoneName = TEXT("head");
}
void FAnimNode_InterviewerHeadMotion::Initialize_AnyThread(const FAnimationInitializeContext& Context)
{
    Source.Initialize(Context);
}
void FAnimNode_InterviewerHeadMotion::CacheBones_AnyThread(const FAnimationCacheBonesContext& Context)
{
    Source.CacheBones(Context);
    const auto& Bones = Context.AnimInstanceProxy->GetRequiredBones();
    NeckOne.Initialize(Bones);
    NeckTwo.Initialize(Bones);
    Head.Initialize(Bones);
}
void FAnimNode_InterviewerHeadMotion::Update_AnyThread(const FAnimationUpdateContext& Context)
{
    Source.Update(Context);
}
void FAnimNode_InterviewerHeadMotion::PreUpdate(const UAnimInstance* Instance)
{
    Rotation = FQuat::Identity;
    if (AActor* Owner = Instance->GetOwningActor())
        if (auto* Motion = Owner->FindComponentByClass<UInterviewerMotionComponent>())
        {
            Rotation = Motion->GetComponentHeadRotation();
            Motion->bBodyNodeBound = true;
        }
}
void FAnimNode_InterviewerHeadMotion::ApplyRotation(FCompactPose& Pose, const FQuat& InRotation,
    FCompactPoseBoneIndex N1, FCompactPoseBoneIndex N2, FCompactPoseBoneIndex H)
{
    if (!InRotation.IsNormalized() || InRotation.ContainsNaN() || InRotation.AngularDistance(FQuat::Identity) > FMath::DegreesToRadians(4.0f)) return;
    const auto& Bones = Pose.GetBoneContainer();
    const FCompactPoseBoneIndex Indices[] = {N1, N2, H};
    const float Weights[] = {0.25f, 0.3f, 0.45f};
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const auto Bone = Indices[Index];
        if (Bone.GetInt() < 0 || Bone.GetInt() >= Pose.GetNumBones()) continue;
        // Rebuild component space after each local edit so every descendant shares
        // the updated parent. Translation, scale and all other bones stay intact.
        FCSPose<FCompactPose> ComponentPose;
        ComponentPose.InitPose(Pose);
        const auto Parent = Bones.GetParentBoneIndex(Bone);
        const FQuat ParentRotation = Parent.GetInt() >= 0
            ? ComponentPose.GetComponentSpaceTransform(Parent).GetRotation() : FQuat::Identity;
        const FQuat Delta = FQuat::Slerp(FQuat::Identity, InRotation, Weights[Index]);
        FTransform& Local = Pose[Bone];
        Local.SetRotation((ParentRotation.Inverse() * Delta * ParentRotation * Local.GetRotation()).GetNormalized());
    }
}
void FAnimNode_InterviewerHeadMotion::Evaluate_AnyThread(FPoseContext& Output)
{
    Source.Evaluate(Output);
    const auto& Bones = Output.Pose.GetBoneContainer();
    if (Head.IsValidToEvaluate(Bones) && NeckOne.IsValidToEvaluate(Bones) && NeckTwo.IsValidToEvaluate(Bones))
        ApplyRotation(Output.Pose, Rotation, NeckOne.GetCompactPoseIndex(Bones), NeckTwo.GetCompactPoseIndex(Bones), Head.GetCompactPoseIndex(Bones));
}
void FAnimNode_InterviewerHeadMotion::GatherDebugData(FNodeDebugData& DebugData)
{
    DebugData.AddDebugItem(DebugData.GetNodeName(this));
    Source.GatherDebugData(DebugData);
}
