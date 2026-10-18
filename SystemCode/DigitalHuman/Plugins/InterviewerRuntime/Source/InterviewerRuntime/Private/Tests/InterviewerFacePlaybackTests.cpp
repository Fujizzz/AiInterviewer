#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/AudioComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "InterviewerController.h"
#include "InterviewerFaceAnimInstance.h"
#include "InterviewerSpeechComponent.h"
#include "InterviewerMotionComponent.h"

namespace
{
enum class EFacePlaybackStage : uint8 { Baseline, FirstSpeech, FirstExit, SecondSpeech, InterruptedExit };

struct FFacePlaybackContext
{
    TWeakObjectPtr<AInterviewerController> Controller;
    TWeakObjectPtr<USkeletalMeshComponent> Face;
    TWeakObjectPtr<USkeletalMeshComponent> Body;
    TWeakObjectPtr<UInterviewerFaceAnimInstance> FaceInstance;
    TWeakObjectPtr<UAnimInstance> BodyInstance;
    EFacePlaybackStage Stage = EFacePlaybackStage::Baseline;
    double StageStarted = FPlatformTime::Seconds();
    int32 MaxNativeFrames = 0;
    int32 MaxSpeechCurves = 0;
    int32 SpeechSamples = 0;
    float MinJaw = TNumericLimits<float>::Max();
    float MaxJaw = TNumericLimits<float>::Lowest();
    bool bEntrySettled = false;
    bool bHeadControlObserved = false;
    bool bExpressionProfileObserved = false;
    int32 ExpressionGeneration = 0;
    int32 JawOutputSamples = 0;
    float MaxJawOverlayDeviation = 0.0f;
    double SecondPlaybackStarted = 0.0;
    float MaxAudibleRms = 0.0f;
    float MaxHeadAgreementDegrees = 0.0f;
    bool bBodyHeadNodeObserved = false;
    bool bNormalReleaseObserved = false;

    void ChangeStage(EFacePlaybackStage Next)
    {
        Stage = Next;
        StageStarted = FPlatformTime::Seconds();
    }

    void Cleanup()
    {
        if (Controller.IsValid())
        {
            Controller->Speech->StopSpeaking();
            Controller->SetState(TEXT("listening"));
        }
        if (FaceInstance.IsValid())
        {
            FString Detail;
            FaceInstance->ClearExpressionPlan(TEXT("30000000-0000-0000-0000-000000000003"),
                ++ExpressionGeneration, Detail);
        }
    }

    /** Exercise primary Agent ownership at maximum accepted strength during cached speech. */
    bool BindExpression(const FString& UtteranceId, FAutomationTestBase* Test)
    {
        UE::Interviewer::FExpressionPlan Plan;
        Plan.PresentationId = TEXT("30000000-0000-0000-0000-000000000003");
        Plan.Generation = ++ExpressionGeneration;
        Plan.QuestionId = TEXT("cached-expression-speech");
        Plan.UtteranceId = UtteranceId;
        Plan.Source = TEXT("model");
        Plan.ValidMs = 60000;
        for (auto* Profile : {&Plan.Idle, &Plan.Listening, &Plan.Thinking, &Plan.Speaking})
        {
            Profile->Expression = UE::Interviewer::EExpressionIntent::Emphasis;
            Profile->Intensity = 0.35f;
            Profile->Variation = 1.0f;
            Profile->GazeAmplitude = 0.15f;
            Profile->EyeContact = 0.65f;
            Profile->Warmth = 0.15f;
            Profile->HeadMotionStrength = 0.5f;
            Profile->HeadMotionProbability = 1.0f;
            Profile->AudioEmphasisStrength = 0.35f;
        }
        FString Detail;
        return Test->TestTrue(TEXT("Speech accepts the maximum-strength V2 primary profiles"),
            FaceInstance->SetExpressionPlan(Plan, Detail) == UE::Interviewer::EExpressionApplyResult::Applied);
    }

    bool CheckInstances(FAutomationTestBase* Test)
    {
        if (!Controller.IsValid() || !Face.IsValid() || !Body.IsValid()
            || !FaceInstance.IsValid() || !BodyInstance.IsValid())
        {
            Test->AddError(TEXT("Face playback scene objects disappeared"));
            return false;
        }
        if (Face->GetAnimInstance() != FaceInstance.Get() || Body->GetAnimInstance() != BodyInstance.Get())
        {
            Test->AddError(TEXT("Speech changed the persistent Face or Body animation instance"));
            return false;
        }
        if (FaceInstance->SubjectName != Controller->Speech->SubjectName)
        {
            Test->AddError(TEXT("Persistent Face lost its speech subject binding"));
            return false;
        }
        if (const auto* Motion = Controller->Avatar->FindComponentByClass<UInterviewerMotionComponent>())
        {
            bBodyHeadNodeObserved |= Motion->bBodyNodeBound;
            MaxAudibleRms = FMath::Max(MaxAudibleRms, Motion->AudibleRms);
        }
        for (const FName Bone : {FName(TEXT("neck_01")), FName(TEXT("neck_02")), FName(TEXT("head"))})
        {
            if (Body->GetBoneIndex(Bone) >= 0 && Face->GetBoneIndex(Bone) >= 0)
            {
                const FQuat BodyRotation = Body->GetSocketTransform(Bone, RTS_World).GetRotation();
                const FQuat FaceRotation = Face->GetSocketTransform(Bone, RTS_World).GetRotation();
                MaxHeadAgreementDegrees = FMath::Max(MaxHeadAgreementDegrees,
                    FMath::RadiansToDegrees(BodyRotation.AngularDistance(FaceRotation)));
            }
        }
        float HeadControl = 0.0f;
        if (FaceInstance->GetCurveValue(TEXT("HeadControlSwitch"), HeadControl))
        {
            bHeadControlObserved = true;
            if (!FMath::IsNearlyZero(HeadControl))
            {
                Test->AddError(TEXT("Speech enabled the face head override instead of retaining the Body pose"));
                return false;
            }
        }
        return true;
    }

    bool CheckExit(FAutomationTestBase* Test, const TCHAR* ExpectedState)
    {
        Test->TestEqual(TEXT("Playback selects the expected presentation state"), Controller->State, FString(ExpectedState));
        Test->TestFalse(TEXT("Playback exit disables speech presentation"), FaceInstance->bSpeakingTarget);
        Test->TestFalse(TEXT("Playback exit completes the expression blend"), FaceInstance->bTransitioning);
        Test->TestFalse(TEXT("Playback exit does not wait for removed speech data"), FaceInstance->bWaitingForSpeechFrame);
        Test->TestTrue(TEXT("Playback exit reaches the Agent quiet face"), FMath::IsNearlyEqual(FaceInstance->TransitionAlpha, 1.0f));
        TArray<UAudioComponent*> AudioComponents;
        Controller->GetComponents(AudioComponents);
        for (const auto* Audio : AudioComponents)
            Test->TestFalse(TEXT("Playback exit stops its audio component"), Audio->IsPlaying());
        return Controller->State == ExpectedState && !FaceInstance->bSpeakingTarget
            && !FaceInstance->bTransitioning && !FaceInstance->bWaitingForSpeechFrame
            && FMath::IsNearlyEqual(FaceInstance->TransitionAlpha, 1.0f);
    }

    bool Update(FAutomationTestBase* Test)
    {
        if (!CheckInstances(Test)) { Cleanup(); return true; }
        const double Elapsed = FPlatformTime::Seconds() - StageStarted;
        if (Elapsed > 25.0)
        {
            Test->AddError(FString::Printf(TEXT("Face speech playback timed out in stage %d"), static_cast<int32>(Stage)));
            Cleanup();
            return true;
        }
        auto* Speech = Controller->Speech.Get();
        if (Stage != EFacePlaybackStage::Baseline && Speech->LastPlaybackEvent == TEXT("playback_failed"))
        {
            Test->AddError(Speech->LastError);
            Cleanup();
            return true;
        }
        const double SettleSeconds = FMath::Max(0.35, static_cast<double>(FaceInstance->GetSpeechTransitionSeconds()) + 0.15);
        switch (Stage)
        {
        case EFacePlaybackStage::Baseline:
            if (Elapsed < SettleSeconds) return false;
            if (!BindExpression(TEXT("00000000-0000-0000-0000-000000000001"), Test)) { Cleanup(); return true; }
            Speech->Speak(TEXT("http://127.0.0.1:8765/api/speech/audio/00000000-0000-0000-0000-000000000001/"),
                TEXT("00000000-0000-0000-0000-000000000001"));
            ChangeStage(EFacePlaybackStage::FirstSpeech);
            return false;
        case EFacePlaybackStage::FirstSpeech:
            MaxNativeFrames = FMath::Max(MaxNativeFrames, Speech->LastGeneratedFrameCount);
            if (Speech->LastPlaybackEvent == TEXT("playback_started") && FaceInstance->LastSpeechCurveCount > 0)
            {
                if (!FaceInstance->bSpeakingTarget || !FMath::IsFinite(FaceInstance->DisplayedJawOpen))
                {
                    Test->AddError(TEXT("Playing speech has an invalid Face presentation or jaw curve"));
                    Cleanup();
                    return true;
                }
                MaxSpeechCurves = FMath::Max(MaxSpeechCurves, FaceInstance->LastSpeechCurveCount);
                MinJaw = FMath::Min(MinJaw, FaceInstance->DisplayedJawOpen);
                MaxJaw = FMath::Max(MaxJaw, FaceInstance->DisplayedJawOpen);
                ++SpeechSamples;
                bExpressionProfileObserved |= FaceInstance->GetExpressionSnapshot().bPlaybackMatches
                    && FaceInstance->GetExpressionSnapshot().bPrimaryHealthy && FaceInstance->bAgentFaceActive
                    && FaceInstance->AgentFaceWeight > 0.99f;
                float OutputJaw = 0.0f;
                if (FaceInstance->GetCurveValue(TEXT("CTRL_expressions_jawopen"), OutputJaw))
                {
                    ++JawOutputSamples;
                    MaxJawOverlayDeviation = FMath::Max(MaxJawOverlayDeviation,
                        FMath::Abs(OutputJaw - FaceInstance->DisplayedJawOpen));
                }
                bEntrySettled |= !FaceInstance->bTransitioning && FMath::IsNearlyEqual(FaceInstance->TransitionAlpha, 1.0f);
            }
            if (Speech->LastPlaybackEvent != TEXT("playback_finished")) return false;
            Test->TestTrue(TEXT("Cached speech produces native solver frames"), MaxNativeFrames > 0);
            Test->TestTrue(TEXT("Face consumes native expression curves"), MaxSpeechCurves > 0);
            Test->TestTrue(TEXT("Cached speech moves the displayed jaw"), SpeechSamples > 1 && MaxJaw - MinJaw > 0.005f);
            Test->TestTrue(TEXT("Speech entry settles while playback is active"), bEntrySettled);
            Test->TestTrue(TEXT("Actual playback uses primary Agent speaking ownership"), bExpressionProfileObserved);
            Test->TestTrue(TEXT("Normal completion selects the configured slower face release"),
                FMath::IsNearlyEqual(FaceInstance->GetSpeechTransitionSeconds(), FaceInstance->SpeechReleaseSeconds)
                && FaceInstance->SpeechReleaseSeconds > FaceInstance->TransitionSeconds);
            Test->TestTrue(TEXT("Actual Body graph evaluates the appended native motion node"), bBodyHeadNodeObserved);
            Test->TestTrue(TEXT("Actual playing PCM supplies local speech rhythm"), MaxAudibleRms > 0.01f);
            Test->TestTrue(TEXT("Face copies the same updated Body neck/head rotations"), MaxHeadAgreementDegrees < 0.5f);
            Test->TestTrue(TEXT("Overlay preserves the rendered jaw throughout native speech"),
                JawOutputSamples > 1 && MaxJawOverlayDeviation < 0.0001f);
            UE_LOG(LogTemp, Display, TEXT("Interviewer face playback: frames=%d curves=%d samples=%d jawMin=%.6f jawMax=%.6f"),
                MaxNativeFrames, MaxSpeechCurves, SpeechSamples, SpeechSamples ? MinJaw : 0.0f, SpeechSamples ? MaxJaw : 0.0f);
            UE_LOG(LogTemp, Display, TEXT("Interviewer expression speech: jawSamples=%d maxJawDeviation=%.6f playbackBound=%d"),
                JawOutputSamples, MaxJawOverlayDeviation, bExpressionProfileObserved ? 1 : 0);
            UE_LOG(LogTemp, Display, TEXT("Interviewer motion playback: bodyNode=%d audibleRms=%.6f maxBodyFaceHeadDegrees=%.6f"),
                bBodyHeadNodeObserved ? 1 : 0, MaxAudibleRms, MaxHeadAgreementDegrees);
            ChangeStage(EFacePlaybackStage::FirstExit);
            return false;
        case EFacePlaybackStage::FirstExit:
            if (Elapsed > FaceInstance->TransitionSeconds + 0.05
                && Elapsed < FaceInstance->SpeechReleaseSeconds - 0.1)
                bNormalReleaseObserved |= FaceInstance->bTransitioning && FaceInstance->TransitionAlpha < 1.0f;
            if (Elapsed < SettleSeconds) return false;
            Test->TestTrue(TEXT("Actual normal completion keeps blending beyond the original short exit"), bNormalReleaseObserved);
            if (!CheckExit(Test, TEXT("listening"))) { Cleanup(); return true; }
            if (!BindExpression(TEXT("00000000-0000-0000-0000-000000000002"), Test)) { Cleanup(); return true; }
            Speech->Speak(TEXT("http://127.0.0.1:8765/api/speech/audio/00000000-0000-0000-0000-000000000002/"),
                TEXT("00000000-0000-0000-0000-000000000002"));
            ChangeStage(EFacePlaybackStage::SecondSpeech);
            return false;
        case EFacePlaybackStage::SecondSpeech:
            if (Speech->LastPlaybackEvent != TEXT("playback_started")) return false;
            if (SecondPlaybackStarted == 0.0)
            {
                SecondPlaybackStarted = FPlatformTime::Seconds();
                return false;
            }
            // Allow new Face evaluations before considering diagnostics from the previous utterance.
            if (FPlatformTime::Seconds() - SecondPlaybackStarted < 0.1
                || Speech->LastGeneratedFrameCount <= 0 || FaceInstance->LastSpeechCurveCount <= 0
                || FaceInstance->bWaitingForSpeechFrame) return false;
            Test->TestTrue(TEXT("Second speech enables the persistent Face"), FaceInstance->bSpeakingTarget);
            Test->TestTrue(TEXT("Next actual speech restores the original entry duration"),
                FMath::IsNearlyEqual(FaceInstance->GetSpeechTransitionSeconds(), FaceInstance->TransitionSeconds));
            Speech->StopSpeaking();
            Test->TestEqual(TEXT("Second speech is interrupted"), Speech->LastPlaybackEvent, FString(TEXT("interrupted")));
            Test->TestTrue(TEXT("Explicit interruption keeps the original short release"),
                FMath::IsNearlyEqual(FaceInstance->GetSpeechTransitionSeconds(), FaceInstance->TransitionSeconds));
            ChangeStage(EFacePlaybackStage::InterruptedExit);
            return false;
        case EFacePlaybackStage::InterruptedExit:
            if (Elapsed < SettleSeconds) return false;
            CheckExit(Test, TEXT("interrupted"));
            UE_LOG(LogTemp, Display, TEXT("Interviewer face playback: interruption settled alpha=%.3f sameFace=1 sameBody=1 headControlObserved=%d"),
                FaceInstance->TransitionAlpha, bHeadControlObserved ? 1 : 0);
            Cleanup();
            return true;
        }
        return false;
    }
};
}

DEFINE_LATENT_AUTOMATION_COMMAND_TWO_PARAMETER(FWaitInterviewerFacePlayback,
    TSharedPtr<FFacePlaybackContext>, Context, FAutomationTestBase*, Test);

bool FWaitInterviewerFacePlayback::Update()
{
    return Context->Update(Test);
}

// Requires audio_smoke_fixture.py --wav Saved/SpeechValidation/qwen-question.wav.
// Runs the real avatar and native solver offline; visual appearance remains a human check.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceSpeechPlayback,
    "Interviewer.FaceSpeechPlayback", EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceSpeechPlayback::RunTest(const FString& Parameters)
{
    for (const FWorldContext& WorldContext : GEngine->GetWorldContexts())
    {
        if (WorldContext.WorldType != EWorldType::Game && WorldContext.WorldType != EWorldType::PIE) continue;
        for (TActorIterator<AInterviewerController> It(WorldContext.World()); It; ++It)
        {
            if (!TestNotNull(TEXT("Speech playback scene has an assembled avatar"), It->Avatar.Get())) return false;
            TArray<USkeletalMeshComponent*> Meshes;
            It->Avatar->GetComponents(Meshes);
            auto Context = MakeShared<FFacePlaybackContext>();
            Context->Controller = *It;
            for (auto* Mesh : Meshes)
            {
                if (Mesh->GetName() == TEXT("Face")) Context->Face = Mesh;
                if (Mesh->GetName() == TEXT("Body")) Context->Body = Mesh;
            }
            if (!TestNotNull(TEXT("Speech playback avatar has Face"), Context->Face.Get())
                || !TestNotNull(TEXT("Speech playback avatar has Body"), Context->Body.Get())) return false;
            Context->FaceInstance = Cast<UInterviewerFaceAnimInstance>(Context->Face->GetAnimInstance());
            Context->BodyInstance = Context->Body->GetAnimInstance();
            if (!TestNotNull(TEXT("Speech playback uses the persistent Face instance"), Context->FaceInstance.Get())
                || !TestNotNull(TEXT("Speech playback keeps the Body instance"), Context->BodyInstance.Get())) return false;
            TestTrue(TEXT("Face copies its attached Body pose"), Context->Face->GetAttachParent() == Context->Body.Get());
            It->Speech->StopSpeaking();
            It->SetState(TEXT("listening"));
            ADD_LATENT_AUTOMATION_COMMAND(FWaitInterviewerFacePlayback(Context, this));
            return true;
        }
    }
    AddError(TEXT("Run FaceSpeechPlayback in the assembled L_Interview game scene with the cached WAV fixture"));
    return false;
}

#endif
