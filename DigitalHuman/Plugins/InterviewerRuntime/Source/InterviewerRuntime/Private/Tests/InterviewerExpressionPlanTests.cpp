#if WITH_DEV_AUTOMATION_TESTS

#include "InterviewerExpressionPlanHelpers.h"
#include "InterviewerFaceAnimInstance.h"
#include "InterviewerController.h"
#include "Misc/AutomationTest.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/SkeletalMeshComponent.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include <limits>

using namespace UE::Interviewer;

namespace
{
const FString PresentationId(TEXT("10000000-0000-0000-0000-000000000001"));
const FString UtteranceId(TEXT("20000000-0000-0000-0000-000000000001"));
const FName Brow(TEXT("CTRL_expressions_browRaiseInL"));
const FName Blink(TEXT("CTRL_expressions_eyeBlinkL"));
const FName Jaw(TEXT("CTRL_expressions_jawOpen"));
constexpr EAutomationTestFlags TestFlags = EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext
    | EAutomationTestFlags::EngineFilter;

FExpressionPlan PlanFixture()
{
    FExpressionPlan Plan;
    Plan.PresentationId = PresentationId;
    Plan.Generation = 1;
    Plan.QuestionId = TEXT("session-idle");
    Plan.UtteranceId = UtteranceId;
    Plan.Source = TEXT("model");
    Plan.Idle.Expression = EExpressionIntent::Neutral;
    Plan.Listening.Expression = EExpressionIntent::Attentive;
    Plan.Listening.Intensity = 0.18f;
    Plan.Thinking.Expression = EExpressionIntent::Thoughtful;
    Plan.Thinking.Intensity = 0.2f;
    Plan.Speaking.Expression = EExpressionIntent::Emphasis;
    Plan.Speaking.Intensity = 0.35f;
    return Plan;
}

TSharedPtr<FJsonObject> JsonProfile()
{
    auto Profile = MakeShared<FJsonObject>();
    Profile->SetStringField(TEXT("expression"), TEXT("attentive"));
    Profile->SetNumberField(TEXT("intensity"), 0.18);
    Profile->SetNumberField(TEXT("variation"), 0.8);
    Profile->SetNumberField(TEXT("blink_min_ms"), 1800);
    Profile->SetNumberField(TEXT("blink_max_ms"), 4500);
    Profile->SetNumberField(TEXT("blink_duration_ms"), 180);
    Profile->SetNumberField(TEXT("gaze_amplitude"), 0.1);
    Profile->SetNumberField(TEXT("gaze_hold_min_ms"), 1500);
    Profile->SetNumberField(TEXT("gaze_hold_max_ms"), 3000);
    Profile->SetNumberField(TEXT("eye_contact"), 0.8);
    Profile->SetNumberField(TEXT("warmth"), 0.05);
    Profile->SetNumberField(TEXT("motion_min_ms"), 3000);
    Profile->SetNumberField(TEXT("motion_max_ms"), 6000);
    return Profile;
}

TSharedPtr<FJsonObject> JsonFixture()
{
    auto Message = MakeShared<FJsonObject>();
    Message->SetStringField(TEXT("type"), TEXT("expression_plan"));
    Message->SetNumberField(TEXT("version"), 2);
    Message->SetStringField(TEXT("presentation_id"), PresentationId);
    Message->SetNumberField(TEXT("generation"), 1);
    Message->SetStringField(TEXT("question_id"), TEXT("session-idle"));
    Message->SetStringField(TEXT("utterance_id"), TEXT(""));
    Message->SetStringField(TEXT("source"), TEXT("model"));
    Message->SetField(TEXT("reason"), MakeShared<FJsonValueNull>());
    Message->SetNumberField(TEXT("transition_ms"), 350);
    Message->SetNumberField(TEXT("valid_ms"), 60000);
    auto States = MakeShared<FJsonObject>();
    for (const TCHAR* State : {TEXT("idle"), TEXT("listening"), TEXT("thinking"), TEXT("speaking")})
        States->SetObjectField(State, JsonProfile());
    Message->SetObjectField(TEXT("states"), States);
    return Message;
}

FFaceCurveMap RecordedFixture()
{
    FFaceCurveMap Recorded;
    Recorded.Add(Brow, 0.95f);
    Recorded.Add(Blink, 1.0f);
    Recorded.Add(TEXT("CTRL_expressions_eyeLookLeftL"), 0.8f);
    Recorded.Add(TEXT("CTRL_expressions_mouthCornerPullL"), 0.85f);
    Recorded.Add(TEXT("CTRL_expressions_noseWrinkleL"), 0.8f);
    Recorded.Add(Jaw, 0.9f);
    return Recorded;
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionProtocol,
    "Interviewer.ExpressionPlan.Protocol", TestFlags);

bool FInterviewerExpressionProtocol::RunTest(const FString& Parameters)
{
    FString Detail;
    FExpressionPlan Plan;
    auto Message = JsonFixture();
    TestTrue(TEXT("Complete backend V2 plan with reason null parses"), ParseExpressionPlan(Message, Plan, Detail));
    TestEqual(TEXT("Speaking has its own continuous profile"), Plan.Speaking.BlinkMinMs, 1800);
    TestEqual(TEXT("Legacy V2 has no head motion"), Plan.Listening.HeadMotionStrength, 0.0f);
    auto Extended = Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("listening"));
    Extended->SetNumberField(TEXT("head_motion_strength"), 0.5);
    Extended->SetNumberField(TEXT("head_motion_probability"), 0.55);
    Extended->SetNumberField(TEXT("audio_emphasis_strength"), 0.35);
    TestTrue(TEXT("Bounded optional motion controls parse"), ParseExpressionPlan(Message, Plan, Detail));
    TestEqual(TEXT("Optional motion controls are preserved"), Plan.Listening.HeadMotionStrength, 0.5f);
    Extended->SetNumberField(TEXT("head_motion_probability"), 1.01);
    TestFalse(TEXT("Optional probability is bounded"), ParseExpressionPlan(Message, Plan, Detail));
    Extended->SetStringField(TEXT("head_motion_probability"), TEXT("0.55"));
    TestFalse(TEXT("Optional motion controls reject numeric strings"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetNumberField(TEXT("version"), 1);
    TestFalse(TEXT("Old cue contract is rejected"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->GetObjectField(TEXT("states"))->RemoveField(TEXT("speaking"));
    TestFalse(TEXT("All four states are required"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetNumberField(TEXT("generation"), 1.5);
    TestFalse(TEXT("Fractional generation is rejected"), ParseExpressionPlan(Message, Plan, Detail));
    Message->SetNumberField(TEXT("generation"), 2147483648.0);
    TestFalse(TEXT("Generation overflow is rejected before conversion"), ParseExpressionPlan(Message, Plan, Detail));
    Message->SetNumberField(TEXT("generation"), MAX_int32);
    TestTrue(TEXT("Maximum generation parses"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetNumberField(TEXT("question_id"), 1);
    TestFalse(TEXT("Question ID cannot be coerced into a string"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetNumberField(TEXT("reason"), 1);
    TestFalse(TEXT("Reason cannot be coerced into a string"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetStringField(TEXT("reason"), FString::ChrN(97, TEXT('x')));
    TestFalse(TEXT("Reason length is bounded"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetNumberField(TEXT("valid_ms"), 600001);
    TestFalse(TEXT("Plan lifetime is bounded"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->SetArrayField(TEXT("speaking"), {});
    TestFalse(TEXT("V1 timed cues cannot accompany a V2 plan"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("idle"))->SetNumberField(TEXT("jawopen"), 1);
    TestFalse(TEXT("Profiles do not accept arbitrary facial controls"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("thinking"))->SetStringField(TEXT("variation"), TEXT("0.8"));
    TestFalse(TEXT("Numeric strings cannot enter profiles"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("speaking"))->SetNumberField(TEXT("blink_max_ms"), 2200);
    TestFalse(TEXT("Blink interval and separation guards apply to speaking"), ParseExpressionPlan(Message, Plan, Detail));
    Message = JsonFixture();
    Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("listening"))->SetNumberField(TEXT("gaze_hold_max_ms"), 1600);
    TestFalse(TEXT("Gaze interval guards are enforced"), ParseExpressionPlan(Message, Plan, Detail));
    Plan = PlanFixture();
    Plan.Idle.Warmth = std::numeric_limits<float>::quiet_NaN();
    TestFalse(TEXT("Native non-finite profiles are rejected"), ValidateExpressionPlan(Plan, Detail));
    Message = JsonFixture();
    Message->SetStringField(TEXT("source"), TEXT("preview"));
    Message->SetStringField(TEXT("reason"), TEXT("manual_preview"));
    TestTrue(TEXT("Offline preview uses the same V2 profile contract"), ParseExpressionPlan(Message, Plan, Detail));
    auto Clear = MakeShared<FJsonObject>();
    Clear->SetStringField(TEXT("type"), TEXT("expression_clear"));
    Clear->SetStringField(TEXT("presentation_id"), PresentationId);
    Clear->SetNumberField(TEXT("generation"), 1);
    FString Id;
    int32 Generation = 0;
    TestTrue(TEXT("Clear contract remains valid"), ParseExpressionClear(Clear, Id, Generation, Detail));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionPrimaryOwnership,
    "Interviewer.ExpressionPlan.PrimaryOwnership", TestFlags);

bool FInterviewerExpressionPrimaryOwnership::RunTest(const FString& Parameters)
{
    FString Detail;
    auto Plan = PlanFixture();
    for (auto* Profile : {&Plan.Idle, &Plan.Listening, &Plan.Thinking, &Plan.Speaking})
    {
        Profile->Expression = EExpressionIntent::Neutral;
        Profile->Intensity = 0;
        Profile->Variation = 0;
        Profile->Warmth = 0;
        Profile->GazeAmplitude = 0;
        Profile->EyeContact = 1;
    }
    const auto Recorded = RecordedFixture();
    for (const TCHAR* State : {TEXT("idle"), TEXT("listening"), TEXT("thinking"), TEXT("speaking")})
    {
        FExpressionPlanRuntime Runtime;
        FExpressionPrimaryFace Primary;
        Runtime.Apply(Plan, Detail, 100);
        Runtime.SetState(State);
        Primary.Step(Runtime.Snapshot(100), Recorded, false, 0);
        const auto& Face = Primary.Step(Runtime.Snapshot(100.35), Recorded, false, 0.35f);
        TestEqual(TEXT("Healthy ownership reaches the primary face"), Primary.GetOwnershipAlpha(), 1.0f);
        for (const auto& Curve : Recorded)
            TestEqual(TEXT("Healthy state does not leak exaggerated recorded CTRL_expressions"), Face.FindRef(Curve.Key), 0.0f);
    }
    FExpressionPlanRuntime Runtime;
    FExpressionPrimaryFace Primary;
    Plan.Source = TEXT("fallback");
    Runtime.Apply(Plan, Detail, 100);
    Primary.Step(Runtime.Snapshot(100), Recorded, false, 0);
    const auto& Fallback = Primary.Step(Runtime.Snapshot(100.35), Recorded, false, 0.35f);
    TestFalse(TEXT("A fallback plan does not own the face"), Runtime.Snapshot(100.35).bPrimaryHealthy);
    for (const auto& Curve : Recorded) TestEqual(TEXT("Fallback preserves recordings"), Fallback.FindRef(Curve.Key), Curve.Value);
    Plan.Source = TEXT("preview");
    Plan.Generation = 2;
    Runtime.Apply(Plan, Detail, 101);
    TestTrue(TEXT("A valid offline preview owns the face"), Runtime.Snapshot(101).bPrimaryHealthy);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionLocalMotion,
    "Interviewer.ExpressionPlan.LocalMotion", TestFlags);

bool FInterviewerExpressionLocalMotion::RunTest(const FString& Parameters)
{
    auto Plan = PlanFixture();
    Plan.ValidMs = 600000;
    Plan.Idle = Plan.Listening;
    Plan.Idle.Variation = 1;
    Plan.Idle.GazeAmplitude = 0.15f;
    Plan.Idle.EyeContact = 0.65f;
    Plan.Idle.Warmth = 0.15f;
    FExpressionPlanRuntime Runtime;
    FExpressionLocalMotion Motion;
    FExpressionPlanRuntime ReboundRuntime;
    FExpressionLocalMotion ReboundMotion;
    FString Detail;
    Runtime.Apply(Plan, Detail, 0);
    ReboundRuntime.Apply(Plan, Detail, 0);
    TArray<double> BlinkTimes;
    bool bPreviousBlink = false;
    float MaxGaze = 0, MinBrow = 1, MaxBrow = 0;
    for (int32 Frame = 0; Frame < 7200; ++Frame)
    {
        const double Now = Frame / 60.0;
        if (Frame == 150)
        {
            ReboundRuntime.Apply(Plan, Detail, Now);
            ReboundRuntime.PlaybackStarted(UtteranceId, Now);
        }
        const auto Generated = Motion.Generate(Runtime.Snapshot(Now), false);
        const auto Rebound = ReboundMotion.Generate(ReboundRuntime.Snapshot(Now), false);
        for (const auto& Curve : Generated)
            if (!FMath::IsNearlyEqual(Curve.Value, Rebound.FindRef(Curve.Key), UE_SMALL_NUMBER))
                AddError(TEXT("Rebinding or repeated plan reception reset a continuous motion clock"));
        const bool bBlinking = Generated.FindRef(Blink) > 0.5f;
        if (bBlinking && !bPreviousBlink) BlinkTimes.Add(Now);
        bPreviousBlink = bBlinking;
        MaxGaze = FMath::Max(MaxGaze, FMath::Max(Generated.FindRef(TEXT("CTRL_expressions_eyeLookLeftL")),
            Generated.FindRef(TEXT("CTRL_expressions_eyeLookRightL"))));
        MinBrow = FMath::Min(MinBrow, Generated.FindRef(Brow));
        MaxBrow = FMath::Max(MaxBrow, Generated.FindRef(Brow));
        for (const auto& Curve : Generated)
            if (!FMath::IsFinite(Curve.Value) || Curve.Value < 0 || Curve.Value > 1) AddError(TEXT("Generated facial value escaped its bounds"));
    }
    TestTrue(TEXT("Continuous face generates multiple blinks"), BlinkTimes.Num() > 4);
    bool bIrregular = false;
    for (int32 Index = 2; Index < BlinkTimes.Num(); ++Index)
        bIrregular |= FMath::Abs((BlinkTimes[Index] - BlinkTimes[Index - 1]) - (BlinkTimes[1] - BlinkTimes[0])) > 0.1;
    TestTrue(TEXT("Blink intervals are irregular"), bIrregular);
    TestTrue(TEXT("Gaze varies within the model's small amplitude"), MaxGaze > 0 && MaxGaze <= 0.15f);
    TestTrue(TEXT("Brows vary continuously without a fixed clip loop"), MaxBrow - MinBrow > 0.001f);
    Runtime.SetState(TEXT("speaking"));
    const auto Speaking = Motion.Generate(Runtime.Snapshot(121), true);
    for (const auto& Curve : Speaking)
        TestFalse(TEXT("Speaking generation excludes all mouth and jaw controls"), Curve.Key.ToString().Contains(TEXT("mouth"))
            || Curve.Key.ToString().Contains(TEXT("jaw")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionExpiryAndCorrelation,
    "Interviewer.ExpressionPlan.ExpiryAndCorrelation", TestFlags);

bool FInterviewerExpressionExpiryAndCorrelation::RunTest(const FString& Parameters)
{
    auto Plan = PlanFixture();
    Plan.ValidMs = 1000;
    Plan.Source = TEXT("fallback");
    FExpressionPlanRuntime Runtime;
    FString Detail;
    Runtime.Apply(Plan, Detail, 100);
    Plan.Source = TEXT("model");
    TestTrue(TEXT("Same-generation fallback can become a model before expiry"), Runtime.Apply(Plan, Detail, 100.4) == EExpressionApplyResult::Applied);
    TestEqual(TEXT("Same generation never extends its original TTL"), Runtime.Snapshot(100.4).ExpiresAt, 101.0);
    TestTrue(TEXT("Model owns the face before expiry"), Runtime.Snapshot(100.9).bPrimaryHealthy);
    TestFalse(TEXT("Model relinquishes ownership at expiry"), Runtime.Snapshot(101).bPrimaryHealthy);
    Runtime.Apply(Plan, Detail, 102);
    TestFalse(TEXT("A repeated expired plan cannot renew ownership"), Runtime.Snapshot(102).bPrimaryHealthy);
    Plan.Generation = 2;
    Plan.ValidMs = 60000;
    Runtime.Apply(Plan, Detail, 103);
    Runtime.PlaybackStarted(UtteranceId, 104);
    Runtime.Apply(Plan, Detail, 104.5);
    Runtime.PlaybackStarted(UtteranceId, 105);
    TestEqual(TEXT("Rebinding and repeated start do not reset the active playback clock"), Runtime.Snapshot(105).PlaybackStarted, 104.0);
    Runtime.PlaybackStopped(UtteranceId);
    Runtime.Apply(Plan, Detail, 105.5);
    TestFalse(TEXT("Late plans cannot revive ended playback"), Runtime.Snapshot(105.5).bPlaybackMatches);
    Runtime.PlaybackStarted(UtteranceId, 106);
    TestEqual(TEXT("Real cached replay may restart its playback clock"), Runtime.Snapshot(106).PlaybackStarted, 106.0);
    Plan.QuestionId = TEXT("another-question");
    TestTrue(TEXT("Same generation cannot change question identity"), Runtime.Apply(Plan, Detail, 106) == EExpressionApplyResult::Rejected);
    Plan.QuestionId = TEXT("session-idle");
    Plan.Generation = 1;
    TestTrue(TEXT("Older generation is ignored"), Runtime.Apply(Plan, Detail, 106) == EExpressionApplyResult::Ignored);
    TestTrue(TEXT("Older clear is ignored"), Runtime.Clear(PresentationId, 1, Detail) == EExpressionApplyResult::Ignored);
    Runtime.Clear(PresentationId, 2, Detail);
    Plan.Generation = 2;
    TestTrue(TEXT("Cleared generation cannot be resurrected"), Runtime.Apply(Plan, Detail, 106) == EExpressionApplyResult::Rejected);

    FExpressionPlanRuntime Expiring;
    FExpressionPrimaryFace Primary;
    Plan.Generation = 1;
    Plan.ValidMs = 500;
    Plan.Idle.Variation = 0;
    const auto Recorded = RecordedFixture();
    Expiring.Apply(Plan, Detail, 0);
    Primary.Step(Expiring.Snapshot(0), Recorded, false, 0);
    Primary.Step(Expiring.Snapshot(0.35), Recorded, false, 0.35f);
    const float BeforeExpiry = Primary.GetDisplayed().FindRef(Brow);
    TestEqual(TEXT("Expiry starts from the visible Agent face"), Primary.Step(Expiring.Snapshot(0.5), Recorded, false, 0.15f).FindRef(Brow), BeforeExpiry);
    const float Mid = Primary.Step(Expiring.Snapshot(0.675), Recorded, false, 0.175f).FindRef(Brow);
    TestTrue(TEXT("Expiry returns to recordings through a smooth blend"), Mid > BeforeExpiry && Mid < Recorded.FindRef(Brow));
    TestEqual(TEXT("Expiry fully restores fallback recordings"), Primary.Step(Expiring.Snapshot(0.85), Recorded, false, 0.175f).FindRef(Brow), Recorded.FindRef(Brow));
    TestEqual(TEXT("Expiry relinquishes primary ownership"), Primary.GetOwnershipAlpha(), 0.0f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionSpeechAndTransitions,
    "Interviewer.ExpressionPlan.SpeechAndTransitions", TestFlags);

bool FInterviewerExpressionSpeechAndTransitions::RunTest(const FString& Parameters)
{
    auto Plan = PlanFixture();
    Plan.Speaking.Warmth = 0.15f;
    Plan.Speaking.Variation = 0;
    FExpressionPlanRuntime Runtime;
    FExpressionPrimaryFace Primary;
    FFaceCurveTransition SpeechBlend;
    FString Detail;
    Runtime.Apply(Plan, Detail, 100);
    Runtime.SetState(TEXT("speaking"));
    const auto Recorded = RecordedFixture();
    Primary.Step(Runtime.Snapshot(100), Recorded, true, 0);
    const auto Effective = Primary.Step(Runtime.Snapshot(100.35), Recorded, true, 0.35f);
    TestEqual(TEXT("Primary face input contains no speaking warmth mouth"), Effective.FindRef(TEXT("CTRL_expressions_mouthCornerPullL")), 0.0f);
    FFaceCurveMap Speech;
    Speech.Add(Jaw, 0.9f);
    Speech.Add(TEXT("CTRL_expressions_mouthStretchL"), 0.8f);
    Speech.Add(TEXT("CTRL_expressions_mouthCornerPullL"), 0.2f);
    Speech.Add(Brow, 0.3f);
    Speech.Add(Blink, 0.95f);
    Speech.Add(TEXT("CTRL_expressions_eyeSolverOnly"), 0.7f);
    SpeechBlend.Step(Effective, nullptr, false, TEXT("speech"), 0, 0.3f, 1, 1);
    SpeechBlend.Step(Effective, &Speech, true, TEXT("speech"), 0, 0.3f, 1, 1);
    const auto& Displayed = SpeechBlend.Step(Effective, &Speech, true, TEXT("speech"), 0.3f, 0.3f, 1, 1);
    TestEqual(TEXT("Primary speaking does not attenuate jaw"), Displayed.FindRef(Jaw), 0.9f);
    TestEqual(TEXT("Primary speaking does not attenuate mouth"), Displayed.FindRef(TEXT("CTRL_expressions_mouthStretchL")), 0.8f);
    TestEqual(TEXT("Speaking mouth corner remains the solver value"), Displayed.FindRef(TEXT("CTRL_expressions_mouthCornerPullL")), 0.2f);
    TestEqual(TEXT("Agent speaking owns brows fully"), Displayed.FindRef(Brow), Effective.FindRef(Brow));
    TestEqual(TEXT("Recorded and solver blink cannot leak into Agent speaking"), Displayed.FindRef(Blink), Effective.FindRef(Blink));
    TestEqual(TEXT("Solver-only upper-face curves cannot leak"), Displayed.FindRef(TEXT("CTRL_expressions_eyeSolverOnly")), 0.0f);
    Speech[Jaw] = 0.01f;
    TestEqual(TEXT("Ordinary visemes remain unfiltered"), SpeechBlend.Step(Effective, &Speech, true, TEXT("speech"), 1.0f / 60, 0.3f, 1, 1).FindRef(Jaw), 0.01f);
    FFaceCurveTransition FallbackBlend;
    FallbackBlend.Step(Recorded, nullptr, false, TEXT("speech"), 0, 0.3f, 0.3f);
    FallbackBlend.Step(Recorded, &Speech, true, TEXT("speech"), 0, 0.3f, 0.3f);
    const auto& Fallback = FallbackBlend.Step(Recorded, &Speech, true, TEXT("speech"), 0.3f, 0.3f, 0.3f);
    TestEqual(TEXT("Native speech jaw is identical with Agent or fallback ownership"), Displayed.FindRef(Jaw), Fallback.FindRef(Jaw));
    Runtime.SetState(TEXT("listening"));
    const auto QuietStart = Primary.Step(Runtime.Snapshot(101), Recorded, false, 0.1f);
    const float ExitStart = SpeechBlend.Step(QuietStart, nullptr, false, TEXT("speech"), 0.1f, 0.3f, 1, 1).FindRef(Jaw);
    TestEqual(TEXT("Speech exit begins at the actual mouth without a snap"), ExitStart, 0.01f);
    const auto QuietEnd = Primary.Step(Runtime.Snapshot(101.35), Recorded, false, 0.35f);
    TestEqual(TEXT("Speech exit reaches generated quiet mouth"), SpeechBlend.Step(QuietEnd, nullptr, false,
        TEXT("speech"), 0.3f, 0.3f, 1, 1).FindRef(Jaw), 0.0f);
    TestEqual(TEXT("Healthy quiet cannot retain recorded jaw after speech"), QuietEnd.FindRef(Jaw), 0.0f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerExpressionController,
    "Interviewer.ExpressionPlan.Controller", EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerExpressionController::RunTest(const FString& Parameters)
{
    for (const FWorldContext& Context : GEngine->GetWorldContexts())
    {
        if (Context.WorldType != EWorldType::Game && Context.WorldType != EWorldType::PIE) continue;
        for (TActorIterator<AInterviewerController> It(Context.World()); It; ++It)
        {
            TArray<USkeletalMeshComponent*> Meshes;
            if (It->Avatar) It->Avatar->GetComponents(Meshes);
            UInterviewerFaceAnimInstance* Face = nullptr;
            for (auto* Mesh : Meshes) if (Mesh->GetName() == TEXT("Face")) Face = Cast<UInterviewerFaceAnimInstance>(Mesh->GetAnimInstance());
            if (!TestNotNull(TEXT("Controller retains its persistent Face instance"), Face)) return false;
            UFunction* Input = It->FindFunction(TEXT("HandleInput"));
            if (!TestNotNull(TEXT("Pixel input uses the native controller"), Input)) return false;
            const FString OriginalState = It->State;
            const FString Id = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
            auto Message = JsonFixture();
            Message->SetStringField(TEXT("presentation_id"), Id);
            FString Descriptor;
            FJsonSerializer::Serialize(Message.ToSharedRef(), TJsonWriterFactory<>::Create(&Descriptor));
            struct FInputParameters { FString Descriptor; } InputParameters{Descriptor};
            It->ProcessEvent(Input, &InputParameters);
            TestTrue(TEXT("Controller applies the real V2 backend model plan"), Face->GetExpressionSnapshot().bPrimaryHealthy);
            It->SetState(TEXT("thinking"));
            TestEqual(TEXT("Quiet controller state reaches the four-state driver"), Face->GetExpressionSnapshot().State, FString(TEXT("thinking")));
            const uint64 Revision = Face->GetExpressionSnapshot().Revision;
            Message->GetObjectField(TEXT("states"))->GetObjectField(TEXT("speaking"))->SetNumberField(TEXT("warmth"), 1);
            Descriptor.Reset();
            FJsonSerializer::Serialize(Message.ToSharedRef(), TJsonWriterFactory<>::Create(&Descriptor));
            InputParameters.Descriptor = Descriptor;
            It->ProcessEvent(Input, &InputParameters);
            TestEqual(TEXT("Invalid speaking profile leaves the native plan intact"), Face->GetExpressionSnapshot().Revision, Revision);
            FString Detail;
            Face->ClearExpressionPlan(Id, 1, Detail);
            TestFalse(TEXT("Clear returns face ownership to fallback"), Face->GetExpressionSnapshot().bPrimaryHealthy);
            It->SetState(OriginalState);
            return true;
        }
    }
    AddError(TEXT("Run ExpressionPlan.Controller in the assembled L_Interview game scene"));
    return false;
}

#endif
