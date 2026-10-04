#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InterviewerController.generated.h"

class UInterviewerSpeechComponent;
class USkeletalMeshComponent;
class UInterviewerMotionComponent;

/** Blueprint-extensible presentation controller, independent of interview scoring. */
UCLASS(Blueprintable)
class INTERVIEWERRUNTIME_API AInterviewerController : public AActor
{
    GENERATED_BODY()
public:
    AInterviewerController();
    virtual void BeginPlay() override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Interview")
    TObjectPtr<UInterviewerSpeechComponent> Speech;

    UPROPERTY(EditInstanceOnly, BlueprintReadWrite, Category="Interview")
    TObjectPtr<AActor> Avatar;

    UPROPERTY(EditInstanceOnly, BlueprintReadWrite, Category="Interview")
    TObjectPtr<AActor> InterviewCamera;

    UPROPERTY(BlueprintReadOnly, Category="Interview")
    FString State = "idle";

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="1.0"))
    float FaceTransitionSeconds = 0.3f;

    /** Relax the displayed speech face after normal completion without delaying audio. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="2.0"))
    float FaceSpeechReleaseSeconds = 0.8f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="1.0"))
    float SpeakingRecordedUpperFaceWeight = 0.3f;

    UFUNCTION(BlueprintCallable, Category="Interview")
    void SetState(const FString& NewState);

    UFUNCTION(BlueprintImplementableEvent, Category="Interview")
    void OnStateChanged(const FString& NewState);

private:
    UPROPERTY(Transient) TObjectPtr<UActorComponent> PixelInput;
    UFUNCTION() void HandleInput(const FString& Descriptor);
    UFUNCTION() void HandlePlayback(FString Event, FString UtteranceId);
    void Respond(const FString& Event, const FString& UtteranceId, const FString& Detail = "");
    void BindAvatar(bool bEnabled);
    TWeakObjectPtr<AActor> LastBoundAvatar;
    bool bLastLiveLinkEnabled = false;
    TWeakObjectPtr<USkeletalMeshComponent> BoundFace;
    UPROPERTY(Transient) TObjectPtr<UClass> IdleFaceAnimationClass;
    UPROPERTY(Transient) TObjectPtr<UInterviewerMotionComponent> PresentationMotion;
};
