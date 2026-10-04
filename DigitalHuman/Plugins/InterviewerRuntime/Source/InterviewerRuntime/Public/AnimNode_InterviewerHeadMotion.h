#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimNodeBase.h"
#include "BoneContainer.h"
#include "AnimNode_InterviewerHeadMotion.generated.h"

/** Append after the existing Body graph, before its unchanged MetaHuman post-process. */
USTRUCT(BlueprintInternalUseOnly)
struct INTERVIEWERRUNTIME_API FAnimNode_InterviewerHeadMotion : public FAnimNode_Base
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category=Links) FPoseLink Source;
    FAnimNode_InterviewerHeadMotion();
    virtual void Initialize_AnyThread(const FAnimationInitializeContext& Context) override;
    virtual void CacheBones_AnyThread(const FAnimationCacheBonesContext& Context) override;
    virtual void Update_AnyThread(const FAnimationUpdateContext& Context) override;
    virtual void Evaluate_AnyThread(FPoseContext& Output) override;
    virtual bool HasPreUpdate() const override { return true; }
    virtual void PreUpdate(const UAnimInstance* Instance) override;
    virtual void GatherDebugData(FNodeDebugData& DebugData) override;
    static void ApplyRotation(FCompactPose& Pose, const FQuat& Rotation,
        FCompactPoseBoneIndex NeckOne, FCompactPoseBoneIndex NeckTwo, FCompactPoseBoneIndex Head);
private:
    FBoneReference NeckOne;
    FBoneReference NeckTwo;
    FBoneReference Head;
    FQuat Rotation = FQuat::Identity;
};
