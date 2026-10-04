#include "InterviewerFaceAnimInstance.h"

#include "InterviewerFaceCurveTransition.h"
#include "InterviewerExpressionPlanHelpers.h"
#include "InterviewerMotionComponent.h"
#include "InterviewerSpeechComponent.h"
#include "GameFramework/Actor.h"
#include "Animation/AnimInstanceProxy.h"
#include "AnimNodes/AnimNode_CopyPoseFromMesh.h"
#include "Features/IModularFeatures.h"
#include "ILiveLinkClient.h"
#include "LiveLinkTypes.h"
#include "Roles/LiveLinkBasicRole.h"

namespace
{
/** All UObject and Live Link reads occur in PreUpdate on the game thread. */
class FInterviewerFaceAnimInstanceProxy : public FAnimInstanceProxy
{
public:
    explicit FInterviewerFaceAnimInstanceProxy(UAnimInstance* Instance)
        : FAnimInstanceProxy(Instance)
    {
        CopyBody.bUseAttachedParent = true;
        CopyBody.bCopyCurves = true;
        CopyBody.bCopyCustomAttributes = true;
    }

protected:
    virtual FAnimNode_Base* GetCustomRootNode() override { return &CopyBody; }

    virtual void GetCustomNodes(TArray<FAnimNode_Base*>& OutNodes) override
    {
        OutNodes.Add(&CopyBody);
    }

    virtual void PreUpdate(UAnimInstance* Instance, float DeltaSeconds) override
    {
        FAnimInstanceProxy::PreUpdate(Instance, DeltaSeconds);
        const auto* Face = CastChecked<UInterviewerFaceAnimInstance>(Instance);
        bSpeaking = Face->bSpeakingTarget;
        Subject = Face->SubjectName;
        Duration = Face->GetSpeechTransitionSeconds();
        Strength = FMath::Clamp(Face->RecordedExpressionStrength, 0.0f, 1.0f);
        UpperFaceWeight = FMath::Clamp(Face->RecordedUpperFaceWeight, 0.0f, 1.0f);
        StepDelta = DeltaSeconds;
        ExpressionSnapshot = Face->GetExpressionSnapshot();
        CoordinatedMotion = UE::Interviewer::FPresentationMotion();
        if (const AActor* Owner = Face->GetOwningActor())
            if (const auto* Motion = Owner->FindComponentByClass<UInterviewerMotionComponent>())
                CoordinatedMotion = Motion->GetMotion();
        SpeechCurves.Reset();

        if (bSpeaking && Face->SpeechPlayback)
        {
            Face->SpeechPlayback->GetSpeechCurves(SpeechCurves, FPlatformTime::Seconds());
        }
        else if (bSpeaking && !Subject.IsNone()
            && IModularFeatures::Get().IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName))
        {
            auto& Client = IModularFeatures::Get().GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
            FLiveLinkSubjectFrameData Frame;
            if (Client.EvaluateFrame_AnyThread(FLiveLinkSubjectName(Subject), ULiveLinkBasicRole::StaticClass(), Frame))
            {
                const auto* Static = Frame.StaticData.Cast<FLiveLinkBaseStaticData>();
                const auto* Data = Frame.FrameData.Cast<FLiveLinkBaseFrameData>();
                if (Static && Data && Static->PropertyNames.Num() == Data->PropertyValues.Num())
                {
                    for (int32 Index = 0; Index < Static->PropertyNames.Num(); ++Index)
                    {
                        const FName Name = Static->PropertyNames[Index];
                        const float Value = Data->PropertyValues[Index];
                        if (UE::Interviewer::IsExpressionCurve(Name) && FMath::IsFinite(Value))
                            SpeechCurves.Add(Name, Value);
                    }
                }
            }
        }
    }

    virtual bool Evaluate(FPoseContext& Output) override
    {
        CopyBody.Evaluate_AnyThread(Output);
        UE::Interviewer::FFaceCurveMap Recorded;
        Output.Curve.ForEachElement([&Recorded, this](const UE::Anim::FCurveElement& Element)
        {
            if (UE::Interviewer::IsExpressionCurve(Element.Name) && FMath::IsFinite(Element.Value))
            {
                // Recorded curves are retained only as the fallback input.
                const float Scale = UE::Interviewer::IsRecordedEyeControl(Element.Name) ? 1.0f : Strength;
                Recorded.Add(Element.Name, Element.Value * Scale);
            }
        });
        const auto& EffectiveFace = ExpressionPrimary.Step(ExpressionSnapshot, Recorded, bSpeaking, StepDelta, &CoordinatedMotion);
        const float Ownership = ExpressionPrimary.GetOwnershipAlpha();
        // Primary plans replace the entire recorded face. During speech the native
        // solver still owns every lower-face control; the Agent owns the upper face.
        const auto& Curves = Transition.Step(EffectiveFace, SpeechCurves.IsEmpty() ? nullptr : &SpeechCurves,
            bSpeaking, Subject, StepDelta, Duration, FMath::Lerp(UpperFaceWeight, 1.0f, Ownership), Ownership);
        StepDelta = 0.0f; // A repeated evaluation within this update must not advance the blend twice.
        for (const auto& Curve : Curves) Output.Curve.Set(Curve.Key, Curve.Value);
        // Keep the current body head/neck pose, including during speech and interruptions.
        Output.Curve.Set(TEXT("HeadControlSwitch"), 0.0f);
        Output.Curve.Set(TEXT("MHFDSVersion"), 1.0f);
        Output.Curve.Set(TEXT("DisableFaceOverride"), 1.0f);
        return true;
    }

    virtual void PostEvaluate(UAnimInstance* Instance) override
    {
        FAnimInstanceProxy::PostEvaluate(Instance);
        auto* Face = CastChecked<UInterviewerFaceAnimInstance>(Instance);
        Face->bTransitioning = Transition.IsTransitioning();
        Face->bWaitingForSpeechFrame = Transition.IsWaitingForSpeech();
        Face->TransitionAlpha = Transition.GetAlpha();
        Face->LastSpeechCurveCount = Transition.GetSpeechCurveCount();
        Face->DisplayedJawOpen = Transition.GetDisplayed().FindRef(TEXT("CTRL_expressions_jawopen"));
        if (bSpeaking)
        {
            const double Now = FPlatformTime::Seconds();
            if (Face->SpeechLastEvaluationTime > 0.0)
                Face->SpeechMaxEvaluationGapSeconds = FMath::Max(Face->SpeechMaxEvaluationGapSeconds,
                    Now - Face->SpeechLastEvaluationTime);
            Face->SpeechLastEvaluationTime = Now;
            if (Face->SpeechEvaluationCount++ == 0)
                Face->SpeechJawMinimum = Face->SpeechJawMaximum = Face->DisplayedJawOpen;
            else
            {
                Face->SpeechJawMinimum = FMath::Min(Face->SpeechJawMinimum, Face->DisplayedJawOpen);
                Face->SpeechJawMaximum = FMath::Max(Face->SpeechJawMaximum, Face->DisplayedJawOpen);
            }
        }
        Face->bAgentFaceActive = ExpressionSnapshot.bPrimaryHealthy;
        Face->AgentFaceWeight = ExpressionPrimary.GetOwnershipAlpha();
    }

private:
    FAnimNode_CopyPoseFromMesh CopyBody;
    UE::Interviewer::FFaceCurveTransition Transition;
    UE::Interviewer::FExpressionPrimaryFace ExpressionPrimary;
    UE::Interviewer::FExpressionSnapshot ExpressionSnapshot;
    UE::Interviewer::FPresentationMotion CoordinatedMotion;
    UE::Interviewer::FFaceCurveMap SpeechCurves;
    FName Subject;
    float StepDelta = 0.0f;
    float Duration = 0.3f;
    float Strength = 1.0f;
    float UpperFaceWeight = 0.3f;
    bool bSpeaking = false;
};
}

void UInterviewerFaceAnimInstance::SetPresentation(bool bInSpeaking, FName InSubject)
{
    if (bInSpeaking && !bSpeakingTarget)
    {
        SpeechEvaluationCount = 0;
        SpeechLastEvaluationTime = SpeechMaxEvaluationGapSeconds = 0.0;
    }
    if (bInSpeaking) bSpeechCompletedNormally = false;
    bSpeakingTarget = bInSpeaking;
    SubjectName = InSubject;
}

UE::Interviewer::EExpressionApplyResult UInterviewerFaceAnimInstance::SetExpressionPlan(
    const UE::Interviewer::FExpressionPlan& Plan, FString& Detail)
{
    check(IsInGameThread());
    return ExpressionRuntime.Apply(Plan, Detail);
}

UE::Interviewer::EExpressionApplyResult UInterviewerFaceAnimInstance::ClearExpressionPlan(
    const FString& PresentationId, int32 Generation, FString& Detail)
{
    check(IsInGameThread());
    return ExpressionRuntime.Clear(PresentationId, Generation, Detail);
}

void UInterviewerFaceAnimInstance::SetExpressionState(const FString& State)
{
    check(IsInGameThread());
    ExpressionRuntime.SetState(State);
}

void UInterviewerFaceAnimInstance::ExpressionPlaybackStarted(const FString& UtteranceId)
{
    check(IsInGameThread());
    ExpressionRuntime.PlaybackStarted(UtteranceId, FPlatformTime::Seconds());
}

void UInterviewerFaceAnimInstance::ExpressionPlaybackStopped(const FString& UtteranceId, bool bCompletedNormally)
{
    check(IsInGameThread());
    bSpeechCompletedNormally = bCompletedNormally;
    ExpressionRuntime.PlaybackStopped(UtteranceId);
}

float UInterviewerFaceAnimInstance::GetSpeechTransitionSeconds() const
{
    return !bSpeakingTarget && bSpeechCompletedNormally
        ? FMath::Clamp(SpeechReleaseSeconds, 0.0f, 2.0f)
        : FMath::Clamp(TransitionSeconds, 0.0f, 1.0f);
}

UE::Interviewer::FExpressionSnapshot UInterviewerFaceAnimInstance::GetExpressionSnapshot() const
{
    check(IsInGameThread());
    return ExpressionRuntime.Snapshot(FPlatformTime::Seconds());
}

FAnimInstanceProxy* UInterviewerFaceAnimInstance::CreateAnimInstanceProxy()
{
    return new FInterviewerFaceAnimInstanceProxy(this);
}

void UInterviewerFaceAnimInstance::DestroyAnimInstanceProxy(FAnimInstanceProxy* InProxy)
{
    delete static_cast<FInterviewerFaceAnimInstanceProxy*>(InProxy);
}
