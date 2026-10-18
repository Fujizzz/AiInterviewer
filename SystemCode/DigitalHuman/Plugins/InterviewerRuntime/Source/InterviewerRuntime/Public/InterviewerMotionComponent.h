#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "InterviewerPresentationMotion.h"
#include "InterviewerMotionComponent.generated.h"

class UInterviewerSpeechComponent;
class USkeletalMeshComponent;

/** One game-thread motion clock coordinates Body head motion and Face eye/brow curves. */
UCLASS()
class INTERVIEWERRUNTIME_API UInterviewerMotionComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UInterviewerMotionComponent();
    void Configure(USkeletalMeshComponent* Body, USkeletalMeshComponent* Face,
        UInterviewerSpeechComponent* Speech, AActor* Camera);
    virtual void TickComponent(float Delta, ELevelTick TickType, FActorComponentTickFunction* Tick) override;
    UE::Interviewer::EExpressionApplyResult ApplyListeningActivity(
        const UE::Interviewer::FListeningActivity& Activity, FString& Detail);
    const UE::Interviewer::FPresentationMotion& GetMotion() const { return Motion; }
    FQuat GetComponentHeadRotation() const { return ComponentHeadRotation; }
    UPROPERTY(BlueprintReadOnly, Category="Interview|Motion") bool bBodyNodeBound = false;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Motion") FRotator DisplayedHeadMotion;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Motion") float AudibleRms = 0;
private:
    TWeakObjectPtr<USkeletalMeshComponent> BodyMesh;
    TWeakObjectPtr<USkeletalMeshComponent> FaceMesh;
    TWeakObjectPtr<UInterviewerSpeechComponent> SpeechComponent;
    TWeakObjectPtr<AActor> InterviewCamera;
    UE::Interviewer::FListeningActivityRuntime Listening;
    UE::Interviewer::FPresentationMotionRuntime Runtime;
    UE::Interviewer::FPresentationMotion Motion;
    FQuat ComponentHeadRotation = FQuat::Identity;
};
