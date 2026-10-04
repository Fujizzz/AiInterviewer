#if WITH_DEV_AUTOMATION_TESTS

#include "InterviewerPresentationMotion.h"
#include "InterviewerExpressionPlanHelpers.h"
#include "AnimNode_InterviewerHeadMotion.h"
#include "InterviewerController.h"
#include "InterviewerMotionComponent.h"
#include "Misc/AutomationTest.h"
#include "Misc/MemStack.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Animation/AnimCurveTypes.h"

using namespace UE::Interviewer;
namespace
{
constexpr EAutomationTestFlags Flags = EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter;
const FString MotionPresentation(TEXT("41000000-0000-0000-0000-000000000001"));
const FString CaptureOne(TEXT("42000000-0000-0000-0000-000000000001"));
const FString CaptureTwo(TEXT("42000000-0000-0000-0000-000000000002"));
FExpressionSnapshot MotionPlan(const FString& State = TEXT("listening"))
{
    FExpressionSnapshot S;
    S.bHasPlan = S.bPrimaryHealthy = true;
    S.Plan.PresentationId = MotionPresentation;
    S.Plan.Generation = 1;
    S.Plan.QuestionId = TEXT("motion-fixture");
    S.Plan.Source = TEXT("model");
    S.State = State;
    for (auto* P : {&S.Plan.Idle, &S.Plan.Listening, &S.Plan.Thinking, &S.Plan.Speaking})
    {
        P->HeadMotionStrength = 1;
        P->HeadMotionProbability = 1;
        P->AudioEmphasisStrength = 1;
        P->MotionMinMs = 2500;
        P->MotionMaxMs = 4000;
        P->GazeAmplitude = 0.15f;
    }
    return S;
}
FListeningActivity Activity(int32 Sequence, bool bActive, bool bEnded = false)
{
    FListeningActivity A;
    A.PresentationId = MotionPresentation;
    A.CaptureId = CaptureOne;
    A.CaptureGeneration = 1;
    A.Sequence = Sequence;
    A.bActive = bActive;
    A.bEnded = bEnded;
    return A;
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerListeningActivityTest, "Interviewer.PresentationMotion.ListeningActivity", Flags);
bool FInterviewerListeningActivityTest::RunTest(const FString& Parameters)
{
    FString Detail;
    auto S = MotionPlan();
    FListeningActivityRuntime ActivityRuntime;
    auto Message = MakeShared<FJsonObject>();
    Message->SetStringField(TEXT("type"), TEXT("listening_activity"));
    Message->SetStringField(TEXT("presentation_id"), MotionPresentation);
    Message->SetStringField(TEXT("capture_id"), CaptureOne);
    Message->SetNumberField(TEXT("capture_generation"), 1);
    Message->SetNumberField(TEXT("sequence"), 1);
    Message->SetBoolField(TEXT("active"), false);
    Message->SetBoolField(TEXT("ended"), false);
    FListeningActivity Parsed;
    TestTrue(TEXT("Strict browser activity parses"), ParseListeningActivity(Message, Parsed, Detail));
    Message->SetStringField(TEXT("active"), TEXT("false"));
    TestFalse(TEXT("Activity never coerces bool strings"), ParseListeningActivity(Message, Parsed, Detail));
    Message->SetBoolField(TEXT("active"), false);
    Message->SetNumberField(TEXT("sequence"), 1.1);
    TestFalse(TEXT("Fractional activity sequence rejects"), ParseListeningActivity(Message, Parsed, Detail));
    Message->SetNumberField(TEXT("sequence"), 1);
    Message->SetStringField(TEXT("question_id"), TEXT("extra"));
    TestFalse(TEXT("Activity accepts only its capture namespace"), ParseListeningActivity(Message, Parsed, Detail));
    ActivityRuntime.Apply(Activity(1, false), S, Detail);
    TestEqual(TEXT("Quiet capture start never makes a pause"), ActivityRuntime.Snapshot(S).PauseSerial, uint64(0));
    S.Now = 0.1;
    ActivityRuntime.Apply(Activity(2, true), S, Detail);
    S.Now = 0.65;
    ActivityRuntime.Apply(Activity(3, false), S, Detail);
    TestEqual(TEXT("One voiced blip plus debounce never makes a pause"), ActivityRuntime.Snapshot(S).PauseSerial, uint64(0));
    S.Now = 1;
    ActivityRuntime.Apply(Activity(4, true), S, Detail);
    S.Now = 1.8;
    ActivityRuntime.Apply(Activity(5, true), S, Detail);
    S.Now = 2.3;
    ActivityRuntime.Apply(Activity(6, false), S, Detail);
    TestEqual(TEXT("Sustained voiced heartbeats followed by quiet create one pause"), ActivityRuntime.Snapshot(S).PauseSerial, uint64(1));
    TestTrue(TEXT("Same sequence rejects"), ActivityRuntime.Apply(Activity(6, false), S, Detail) == EExpressionApplyResult::Rejected);
    auto WrongId = Activity(7, false); WrongId.CaptureId = CaptureTwo;
    TestTrue(TEXT("Same generation different capture rejects"), ActivityRuntime.Apply(WrongId, S, Detail) == EExpressionApplyResult::Rejected);
    S.Plan.Generation = 2; S.Plan.QuestionId = TEXT("next-question");
    TestTrue(TEXT("Question refresh keeps current capture"), ActivityRuntime.Apply(Activity(7, true), S, Detail) == EExpressionApplyResult::Applied);
    S.Now = 3.1;
    ActivityRuntime.Apply(Activity(8, true), S, Detail);
    S.Now = 3.3;
    ActivityRuntime.Apply(Activity(9, false, true), S, Detail);
    TestEqual(TEXT("Capture end never creates another pause"), ActivityRuntime.Snapshot(S).PauseSerial, uint64(1));
    TestFalse(TEXT("Ended capture is not fresh"), ActivityRuntime.Snapshot(S).bFresh);
    TestTrue(TEXT("Ended capture cannot revive"), ActivityRuntime.Apply(Activity(10, true), S, Detail) == EExpressionApplyResult::Rejected);
    auto NewCapture = Activity(1, true); NewCapture.CaptureId = CaptureTwo; NewCapture.CaptureGeneration = 2;
    TestTrue(TEXT("New capture generation is allowed"), ActivityRuntime.Apply(NewCapture, S, Detail) == EExpressionApplyResult::Applied);
    TestTrue(TEXT("Older capture generation is ignored"), ActivityRuntime.Apply(Activity(11, true), S, Detail) == EExpressionApplyResult::Ignored);
    S.Now = 5.2;
    NewCapture.Sequence = 2; NewCapture.bActive = false;
    ActivityRuntime.Apply(NewCapture, S, Detail);
    TestEqual(TEXT("Expired voiced heartbeat cannot create a pause"), ActivityRuntime.Snapshot(S).PauseSerial, uint64(1));
    S.bPrimaryHealthy = false;
    NewCapture.Sequence = 3;
    TestTrue(TEXT("Fallback and expired plans reject microphone activity"), ActivityRuntime.Apply(NewCapture, S, Detail) == EExpressionApplyResult::Rejected);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerMotionStateTest, "Interviewer.PresentationMotion.StateGestures", Flags);
bool FInterviewerMotionStateTest::RunTest(const FString& Parameters)
{
    auto S = MotionPlan();
    FPresentationMotionRuntime Runtime;
    FListeningSnapshot Listening; Listening.bFresh = true;
    FSpeechRhythm Rhythm;
    float MaximumNod = 0, MaximumJump = 0;
    FPresentationMotion Previous;
    for (int32 Frame = 0; Frame < 420; ++Frame)
    {
        S.Now = Frame / 60.0;
        if (Frame == 270) Listening.PauseSerial = 1;
        auto Motion = Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
        if (Frame < 270) TestTrue(TEXT("Quiet listening produces no nod"), Motion.Head.IsNearlyZero());
        MaximumNod = FMath::Max(MaximumNod, static_cast<float>(Motion.Head.Pitch));
        MaximumJump = FMath::Max(MaximumJump, FMath::Abs(static_cast<float>(Motion.Head.Pitch - Previous.Head.Pitch)));
        Previous = Motion;
    }
    TestTrue(TEXT("Eligible voiced pause generates a gentle bounded nod"), MaximumNod > 1 && MaximumNod <= 3);
    TestTrue(TEXT("Nod entry and return are continuous"), MaximumJump < 0.5f && Previous.Head.IsNearlyZero(0.01));
    // Retirement cancels the old envelope permanently, even if a new capture arrives immediately.
    S.Now = 10; Listening.PauseSerial = 2; Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
    S.Now = 10.2; auto AtPeak = Runtime.Step(S, Listening, Rhythm, 0.2f);
    Listening.bFresh = false; S.Now = 10.25;
    auto Released = Runtime.Step(S, Listening, Rhythm, 0.05f);
    Listening.bFresh = true; S.Now = 10.3;
    auto AfterNewCapture = Runtime.Step(S, Listening, Rhythm, 0.05f);
    TestTrue(TEXT("Capture retirement cannot resume an old nod"), AfterNewCapture.Head.Pitch < Released.Head.Pitch && Released.Head.Pitch < AtPeak.Head.Pitch);
    S = MotionPlan(TEXT("thinking")); S.Now = 20;
    Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
    float Tilt = 0, Glance = 0;
    for (int32 Frame = 1; Frame < 900; ++Frame)
    {
        S.Now = 20 + Frame / 60.0;
        const auto Motion = Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
        Tilt = FMath::Max(Tilt, FMath::Abs(static_cast<float>(Motion.Head.Roll)));
        Glance = FMath::Max(Glance, Motion.GazeWeight);
        TestTrue(TEXT("Thinking rotations stay under four degrees"), Motion.Head.Quaternion().AngularDistance(FQuat::Identity) <= FMath::DegreesToRadians(4.0f));
    }
    TestTrue(TEXT("Thinking generates tilt and coordinated glance"), Tilt > 1 && Glance > 0.5f);
    S.bPrimaryHealthy = false;
    for (int32 Frame = 0; Frame < 90; ++Frame) { S.Now += 1.0 / 60; Previous = Runtime.Step(S, Listening, Rhythm, 1.0f / 60); }
    TestTrue(TEXT("Plan expiry releases head and gaze"), Previous.Head.IsNearlyZero(0.001) && Previous.GazeWeight < 0.001f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerMotionAudioTest, "Interviewer.PresentationMotion.PcmRhythm", Flags);
bool FInterviewerMotionAudioTest::RunTest(const FString& Parameters)
{
    TArray<int16> Pcm; Pcm.SetNumZeroed(24000);
    for (int32 Index = 7200; Index < 14400; ++Index) Pcm[Index] = Index % 2 ? 10000 : -10000;
    TestEqual(TEXT("Audible time zero has no future energy"), PcmWindowRms(Pcm, 24000, 0), 0.0f);
    TestEqual(TEXT("Initial silence stays zero"), PcmWindowRms(Pcm, 24000, 0.25), 0.0f);
    TestTrue(TEXT("RMS reads the trailing actual PCM window"), FMath::IsNearlyEqual(PcmWindowRms(Pcm, 24000, 0.4), 10000.0f / 32768, 0.0001f));
    TestEqual(TEXT("Later silence returns to zero"), PcmWindowRms(Pcm, 24000, 0.8), 0.0f);
    auto S = MotionPlan(TEXT("speaking"));
    FPresentationMotionRuntime Runtime;
    FExpressionLocalMotion FaceMotion;
    FListeningSnapshot Listening;
    FSpeechRhythm Rhythm; Rhythm.bPlaying = true; Rhythm.PlaybackGeneration = 1;
    float MaximumHead = 0, MaximumBrow = 0;
    for (int32 Frame = 0; Frame < 90; ++Frame)
    {
        S.Now = Frame / 60.0;
        Rhythm.AudibleSeconds = S.Now;
        Rhythm.Rms = PcmWindowRms(Pcm, 24000, S.Now);
        const auto Motion = Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
        MaximumHead = FMath::Max(MaximumHead, static_cast<float>(Motion.Head.Pitch));
        MaximumBrow = FMath::Max(MaximumBrow, Motion.BrowEmphasis);
        const auto Curves = FaceMotion.Generate(S, true, &Motion);
        TestFalse(TEXT("Coordinated speaking never generates jaw"), Curves.Contains(TEXT("CTRL_expressions_jawOpen")));
        TestFalse(TEXT("Coordinated speaking never generates mouth warmth"), Curves.Contains(TEXT("CTRL_expressions_mouthCornerPullL")));
    }
    TestTrue(TEXT("Short real PCM phrase can generate small head and eyebrow emphasis"), MaximumHead > 0.5f && MaximumHead <= 2 && MaximumBrow > 0.02f && MaximumBrow <= 0.06f);
    Rhythm.bPlaying = false; Rhythm.Rms = 0;
    for (int32 Frame = 0; Frame < 90; ++Frame) { S.Now += 1.0 / 60; Runtime.Step(S, Listening, Rhythm, 1.0f / 60); }
    S.Plan.Speaking.HeadMotionStrength = S.Plan.Speaking.HeadMotionProbability = S.Plan.Speaking.AudioEmphasisStrength = 0;
    const auto Quiet = Runtime.Step(S, Listening, Rhythm, 1.0f / 60);
    TestTrue(TEXT("Stopped audio and legacy zero controls release all accents"), Quiet.Head.IsNearlyZero(0.001) && Quiet.BrowEmphasis < 0.001f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerBodyMotionPoseTest, "Interviewer.PresentationMotion.BodyPosePreservation", Flags);
bool FInterviewerBodyMotionPoseTest::RunTest(const FString& Parameters)
{
    FMemMark PoseAllocations(FMemStack::Get());
    USkeletalMesh* Mesh = nullptr;
    if (GEngine) for (const auto& Context : GEngine->GetWorldContexts())
    {
        UWorld* World = Context.World();
        if (!World || (World->WorldType != EWorldType::Game && World->WorldType != EWorldType::PIE)) continue;
        for (TActorIterator<AInterviewerController> It(World); It; ++It)
            if (It->Avatar)
            {
                TArray<USkeletalMeshComponent*> Components; It->Avatar->GetComponents(Components);
                for (auto* Component : Components) if (Component->GetName() == TEXT("Body")) Mesh = Component->GetSkeletalMeshAsset();
            }
    }
    if (!Mesh) Mesh = LoadObject<USkeletalMesh>(nullptr, TEXT("/Game/MetaHumans/MHC_Hannah/Body/SKM_MHC_Hannah_BodyMesh.SKM_MHC_Hannah_BodyMesh"));
    if (!TestNotNull(TEXT("Actual MetaHuman Body mesh fixture exists"), Mesh)) return false;
    const auto& Ref = Mesh->GetRefSkeleton();
    TArray<FBoneIndexType> Required;
    for (int32 Index = 0; Index < Ref.GetNum(); ++Index) Required.Add(static_cast<FBoneIndexType>(Index));
    FBoneContainer Bones(Required, UE::Anim::FCurveFilterSettings(), *Mesh);
    FCompactPose Pose; Pose.SetBoneContainer(&Bones); Pose.ResetToRefPose();
    FCompactPose Original; Original.CopyBonesFrom(Pose);
    const FCompactPoseBoneIndex N1(Ref.FindBoneIndex(TEXT("neck_01"))), N2(Ref.FindBoneIndex(TEXT("neck_02"))), H(Ref.FindBoneIndex(TEXT("head")));
    TestTrue(TEXT("Actual shared neck/head bones exist"), N1.GetInt() >= 0 && N2.GetInt() >= 0 && H.GetInt() >= 0);
    FAnimNode_InterviewerHeadMotion::ApplyRotation(Pose, FQuat(FVector::RightVector, FMath::DegreesToRadians(3.0f)), N1, N2, H);
    bool bOtherBonesIntact = true, bTranslationsIntact = true;
    for (int32 Index = 0; Index < Pose.GetNumBones(); ++Index)
    {
        const FCompactPoseBoneIndex Bone(Index);
        bTranslationsIntact &= Pose[Bone].GetTranslation().Equals(Original[Bone].GetTranslation()) && Pose[Bone].GetScale3D().Equals(Original[Bone].GetScale3D());
        if (Index != N1.GetInt() && Index != N2.GetInt() && Index != H.GetInt()) bOtherBonesIntact &= Pose[Bone].Equals(Original[Bone]);
    }
    TestTrue(TEXT("All Body translations/scales and non-neck local bones are preserved"), bTranslationsIntact && bOtherBonesIntact);
    FCSPose<FCompactPose> Before, After; Before.InitPose(Original); After.InitPose(Pose);
    const float Angle = Before.GetComponentSpaceTransform(H).GetRotation().AngularDistance(After.GetComponentSpaceTransform(H).GetRotation());
    TestTrue(TEXT("Distributed neck/head rotation totals the requested gentle angle"), FMath::IsNearlyEqual(Angle, FMath::DegreesToRadians(3.0f), 0.0001f));
    FCompactPose Safe; Safe.CopyBonesFrom(Original);
    FAnimNode_InterviewerHeadMotion::ApplyRotation(Safe, FQuat(FVector::RightVector, FMath::DegreesToRadians(20.0f)), N1, N2, H);
    TestTrue(TEXT("Unsafe rotation is rejected without changing the pose"), Safe[H].Equals(Original[H]) && Safe[N1].Equals(Original[N1]));
    return true;
}

#endif
