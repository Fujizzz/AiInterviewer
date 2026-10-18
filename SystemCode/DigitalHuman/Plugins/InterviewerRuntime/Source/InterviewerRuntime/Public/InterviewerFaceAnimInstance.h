#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimInstance.h"
#include "InterviewerExpressionPlan.h"
#include "InterviewerFaceAnimInstance.generated.h"

class UInterviewerSpeechComponent;

/** Persistent Face animation; the existing MetaHuman post-process evaluates RigLogic. */
UCLASS(Transient, BlueprintType)
class INTERVIEWERRUNTIME_API UInterviewerFaceAnimInstance : public UAnimInstance
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category="Interview|Face")
    void SetPresentation(bool bInSpeaking, FName InSubject);

    UE::Interviewer::EExpressionApplyResult SetExpressionPlan(
        const UE::Interviewer::FExpressionPlan& Plan, FString& Detail);
    UE::Interviewer::EExpressionApplyResult ClearExpressionPlan(
        const FString& PresentationId, int32 Generation, FString& Detail);
    void SetExpressionState(const FString& State);
    void ExpressionPlaybackStarted(const FString& UtteranceId);
    void ExpressionPlaybackStopped(const FString& UtteranceId, bool bCompletedNormally = false);
    UE::Interviewer::FExpressionSnapshot GetExpressionSnapshot() const;
    float GetSpeechTransitionSeconds() const;

    /** Read fresh solved curves using the same clock as the audible question. */
    UPROPERTY(Transient)
    TObjectPtr<UInterviewerSpeechComponent> SpeechPlayback;

    int32 SpeechEvaluationCount = 0;
    float SpeechJawMinimum = 0.0f;
    float SpeechJawMaximum = 0.0f;
    double SpeechLastEvaluationTime = 0.0;
    double SpeechMaxEvaluationGapSeconds = 0.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="1.0"))
    float TransitionSeconds = 0.3f;

    /** Normal completion uses a slower release; entry and interruption keep TransitionSeconds. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="2.0"))
    float SpeechReleaseSeconds = 0.8f;

    // Stage-one loop assets already contain reduced expression strengths.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="1.0"))
    float RecordedExpressionStrength = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview|Face", meta=(ClampMin="0.0", ClampMax="1.0"))
    float RecordedUpperFaceWeight = 0.3f;

    UPROPERTY(BlueprintReadOnly, Category="Interview|Face")
    bool bSpeakingTarget = false;

    UPROPERTY(BlueprintReadOnly, Category="Interview|Face")
    FName SubjectName = "InterviewerAudio";

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    bool bTransitioning = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    bool bWaitingForSpeechFrame = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    float TransitionAlpha = 1.0f;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    int32 LastSpeechCurveCount = 0;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    float DisplayedJawOpen = 0.0f;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    bool bAgentFaceActive = false;

    UPROPERTY(Transient, BlueprintReadOnly, Category="Interview|Face|Diagnostics")
    float AgentFaceWeight = 0.0f;

protected:
    virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override;
    virtual void DestroyAnimInstanceProxy(FAnimInstanceProxy* InProxy) override;

private:
    UE::Interviewer::FExpressionPlanRuntime ExpressionRuntime;
    bool bSpeechCompletedNormally = false;
};
