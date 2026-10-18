#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "InterviewerFaceCurveTransition.h"

namespace
{
using UE::Interviewer::FFaceCurveMap;
using UE::Interviewer::FFaceCurveTransition;
const FName Jaw(TEXT("CTRL_expressions_jawopen"));
const FName Subject(TEXT("InterviewerAudio"));
constexpr float Duration = 0.3f;

FFaceCurveMap JawFrame(float Value)
{
    FFaceCurveMap Result;
    Result.Add(Jaw, Value);
    return Result;
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionContinuity,
    "Interviewer.FaceTransition.Continuity",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionContinuity::RunTest(const FString& Parameters)
{
    FFaceCurveTransition Blend;
    const auto Recorded = JawFrame(0.2f);
    auto Speech = JawFrame(0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 1.0f / 60.0f, Duration, 0.3f);
    const float AtStart = Blend.Step(Recorded, &Speech, true, Subject, 1.0f / 60.0f, Duration, 0.3f).FindRef(Jaw);
    TestEqual(TEXT("Entry starts at the last displayed mouth"), AtStart, 0.2f);

    float Previous = AtStart;
    float MaxFrameChange = 0.0f;
    for (int32 Frame = 0; Frame < 18; ++Frame)
    {
        const float Current = Blend.Step(Recorded, &Speech, true, Subject, Duration / 18.0f, Duration, 0.3f).FindRef(Jaw);
        TestTrue(TEXT("Entry progresses monotonically"), Current >= Previous - UE_KINDA_SMALL_NUMBER);
        MaxFrameChange = FMath::Max(MaxFrameChange, FMath::Abs(Current - Previous));
        Previous = Current;
    }
    TestTrue(TEXT("Entry has no abrupt boundary step"), MaxFrameChange < 0.06f);
    TestTrue(TEXT("Entry reaches the speech mouth"), FMath::IsNearlyEqual(Previous, 0.8f, UE_KINDA_SMALL_NUMBER));
    TestFalse(TEXT("Entry blend completes"), Blend.IsTransitioning());

    // A new viseme is not filtered once the boundary transition finishes.
    Speech[Jaw] = 0.05f;
    TestEqual(TEXT("Normal speech mouth has no smoothing delay"),
        Blend.Step(Recorded, &Speech, true, Subject, 1.0f / 60.0f, Duration, 0.3f).FindRef(Jaw), 0.05f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionSourceLoss,
    "Interviewer.FaceTransition.SourceLoss",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionSourceLoss::RunTest(const FString& Parameters)
{
    FFaceCurveTransition Blend;
    const auto Recorded = JawFrame(0.2f);
    const auto Speech = JawFrame(0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 0.0f, Duration, 0.3f);
    Blend.Step(Recorded, &Speech, true, Subject, 0.0f, Duration, 0.3f);
    Blend.Step(Recorded, &Speech, true, Subject, Duration, Duration, 0.3f);
    TestEqual(TEXT("Live Link loss holds the last valid speech frame"),
        Blend.Step(Recorded, nullptr, true, Subject, 1.0f / 60.0f, Duration, 0.3f).FindRef(Jaw), 0.8f);
    TestEqual(TEXT("Exit freezes displayed curves after source removal"),
        Blend.Step(Recorded, nullptr, false, Subject, 1.0f / 60.0f, Duration, 0.3f).FindRef(Jaw), 0.8f);
    TestTrue(TEXT("Exit is halfway to recorded expressions after 150 ms"), FMath::IsNearlyEqual(
        Blend.Step(Recorded, nullptr, false, Subject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw), 0.5f));
    TestTrue(TEXT("Exit restores the recorded expression"), FMath::IsNearlyEqual(
        Blend.Step(Recorded, nullptr, false, Subject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw), 0.2f));
    TestFalse(TEXT("Exit completes without a source"), Blend.IsTransitioning());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionNormalCompletionRelease,
    "Interviewer.FaceTransition.NormalCompletionRelease",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionNormalCompletionRelease::RunTest(const FString& Parameters)
{
    constexpr float ReleaseSeconds = 0.8f;
    constexpr float FrameSeconds = 1.0f / 30.0f;
    const FName Lip(TEXT("CTRL_expressions_mouthstretchl"));
    const FName Brow(TEXT("CTRL_expressions_browraiseinl"));
    auto Quiet = JawFrame(0.05f);
    Quiet.Add(Lip, 0.03f);
    Quiet.Add(Brow, 0.12f);
    auto SpeakingFace = Quiet;
    SpeakingFace[Brow] = 0.5f;
    auto Speech = JawFrame(0.8f);
    Speech.Add(Lip, 0.65f);
    Speech.Add(Brow, 0.9f);
    FFaceCurveTransition Blend;
    Blend.Step(Quiet, nullptr, false, Subject, 0, Duration, 1, 1);
    Blend.Step(SpeakingFace, &Speech, true, Subject, 0, Duration, 1, 1);
    Blend.Step(SpeakingFace, &Speech, true, Subject, Duration, Duration, 1, 1);
    const FFaceCurveMap BeforeExit = Blend.GetDisplayed();
    TestEqual(TEXT("Settled speech keeps full native jaw"), BeforeExit.FindRef(Jaw), 0.8f);
    TestEqual(TEXT("Settled speech keeps full native lip curve"), BeforeExit.FindRef(Lip), 0.65f);
    TestEqual(TEXT("Speaking brows remain Agent-owned"), BeforeExit.FindRef(Brow), 0.5f);
    const FFaceCurveMap AtExit = Blend.Step(Quiet, nullptr, false, Subject, FrameSeconds, ReleaseSeconds, 1, 1);
    for (FName Name : {Jaw, Lip, Brow})
        TestEqual(TEXT("Normal release begins at the rendered curve without a snap"), AtExit.FindRef(Name), BeforeExit.FindRef(Name));

    FFaceCurveMap Previous = AtExit;
    float MaxJawStep = 0, MaxLipStep = 0, MaxBrowStep = 0;
    for (int32 Frame = 1; Frame <= 25; ++Frame)
    {
        // Later quiet-state calls can report the entry duration; the exit must
        // retain its latched normal-completion duration instead of compressing.
        const FFaceCurveMap Current = Blend.Step(Quiet, nullptr, false, Subject, FrameSeconds, Duration, 1, 1);
        MaxJawStep = FMath::Max(MaxJawStep, FMath::Abs(Current.FindRef(Jaw) - Previous.FindRef(Jaw)));
        MaxLipStep = FMath::Max(MaxLipStep, FMath::Abs(Current.FindRef(Lip) - Previous.FindRef(Lip)));
        MaxBrowStep = FMath::Max(MaxBrowStep, FMath::Abs(Current.FindRef(Brow) - Previous.FindRef(Brow)));
        for (FName Name : {Jaw, Lip, Brow})
            TestTrue(TEXT("Release approaches the quiet jaw, lip and brow monotonically"),
                Current.FindRef(Name) <= Previous.FindRef(Name) + UE_KINDA_SMALL_NUMBER
                && Current.FindRef(Name) >= Quiet.FindRef(Name) - UE_KINDA_SMALL_NUMBER);
        if (Frame == 9)
        {
            TestTrue(TEXT("Normal release remains active after the old 300 ms exit"), Blend.IsTransitioning());
            TestTrue(TEXT("Changing later duration arguments cannot snap the mouth closed"), Current.FindRef(Jaw) > 0.4f);
        }
        Previous = Current;
    }
    TestTrue(TEXT("Normal release has small curve changes at 30 FPS"), MaxJawStep < 0.06f && MaxLipStep < 0.05f && MaxBrowStep < 0.04f);
    for (FName Name : {Jaw, Lip, Brow})
        TestTrue(TEXT("Normal release reaches the quiet curve within its configured duration"),
            FMath::IsNearlyEqual(Previous.FindRef(Name), Quiet.FindRef(Name), UE_KINDA_SMALL_NUMBER));
    TestFalse(TEXT("The longer normal release completes"), Blend.IsTransitioning());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionReleaseReentry,
    "Interviewer.FaceTransition.ReleaseReentry",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionReleaseReentry::RunTest(const FString& Parameters)
{
    constexpr float ReleaseSeconds = 0.8f;
    FFaceCurveTransition Blend;
    const auto Quiet = JawFrame(0.05f);
    auto Speech = JawFrame(0.8f);
    Blend.Step(Quiet, nullptr, false, Subject, 0, Duration, 0.3f);
    Blend.Step(Quiet, &Speech, true, Subject, 0, Duration, 0.3f);
    Blend.Step(Quiet, &Speech, true, Subject, Duration, Duration, 0.3f);
    Blend.Step(Quiet, nullptr, false, Subject, 0, ReleaseSeconds, 0.3f);
    const float MidRelease = Blend.Step(Quiet, nullptr, false, Subject, Duration, ReleaseSeconds, 0.3f).FindRef(Jaw);
    const FName NextSubject(TEXT("NextUtterance"));
    TestEqual(TEXT("New speech during a long release starts at the displayed mouth"),
        Blend.Step(Quiet, nullptr, true, NextSubject, 0.1f, Duration, 0.3f).FindRef(Jaw), MidRelease);
    TestTrue(TEXT("Re-entry still waits for the new solver frame"), Blend.IsWaitingForSpeech());
    Speech[Jaw] = 0.9f;
    TestEqual(TEXT("The new solver frame cannot cause a re-entry snap"),
        Blend.Step(Quiet, &Speech, true, NextSubject, 0.1f, Duration, 0.3f).FindRef(Jaw), MidRelease);
    TestTrue(TEXT("Re-entry uses the original 300 ms duration"), FMath::IsNearlyEqual(
        Blend.Step(Quiet, &Speech, true, NextSubject, Duration / 2, ReleaseSeconds, 0.3f).FindRef(Jaw),
        FMath::Lerp(MidRelease, 0.9f, 0.5f), UE_KINDA_SMALL_NUMBER));
    Blend.Step(Quiet, &Speech, true, NextSubject, Duration / 2, ReleaseSeconds, 0.3f);
    TestFalse(TEXT("Re-entry completes after 300 ms despite a later release-duration argument"), Blend.IsTransitioning());
    Speech[Jaw] = 0.1f;
    TestEqual(TEXT("New ordinary visemes remain unfiltered after re-entry"),
        Blend.Step(Quiet, &Speech, true, NextSubject, 1.0f / 30, ReleaseSeconds, 0.3f).FindRef(Jaw), 0.1f);
    // Explicit interruption retains the original short release, even after a
    // normal-completion release has used the same persistent transition helper.
    Blend.Step(Quiet, nullptr, false, NextSubject, 0, Duration, 0.3f);
    Blend.Step(Quiet, nullptr, false, NextSubject, Duration, ReleaseSeconds, 0.3f);
    TestFalse(TEXT("Interruption still settles in the original short duration"), Blend.IsTransitioning());
    TestEqual(TEXT("Interruption restores the quiet mouth"), Blend.GetDisplayed().FindRef(Jaw), Quiet.FindRef(Jaw));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionMissingFirstFrame,
    "Interviewer.FaceTransition.MissingFirstFrame",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionMissingFirstFrame::RunTest(const FString& Parameters)
{
    FFaceCurveTransition Blend;
    const auto Recorded = JawFrame(0.2f);
    const auto Speech = JawFrame(0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 0.0f, Duration, 0.3f);
    TestEqual(TEXT("Entering speech without a frame preserves the visible expression"),
        Blend.Step(Recorded, nullptr, true, Subject, 1.0f, Duration, 0.3f).FindRef(Jaw), 0.2f);
    Blend.Step(Recorded, nullptr, true, Subject, 3.0f, Duration, 0.3f);
    TestTrue(TEXT("The transition waits for real solver data"), Blend.IsWaitingForSpeech());
    TestEqual(TEXT("A late first speech frame starts at the preserved expression"),
        Blend.Step(Recorded, &Speech, true, Subject, 1.0f, Duration, 0.3f).FindRef(Jaw), 0.2f);
    TestFalse(TEXT("The first valid frame releases the wait"), Blend.IsWaitingForSpeech());
    TestTrue(TEXT("The full blend remains available after the wait"), FMath::IsNearlyEqual(
        Blend.Step(Recorded, &Speech, true, Subject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw), 0.5f));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionRapidReversal,
    "Interviewer.FaceTransition.RapidReversal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionRapidReversal::RunTest(const FString& Parameters)
{
    FFaceCurveTransition Blend;
    const auto Recorded = JawFrame(0.2f);
    const auto Speech = JawFrame(0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 0.0f, Duration, 0.3f);
    Blend.Step(Recorded, &Speech, true, Subject, 0.0f, Duration, 0.3f);
    const float MidEntry = Blend.Step(Recorded, &Speech, true, Subject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw);
    TestEqual(TEXT("Interruption starts at the partially blended expression"),
        Blend.Step(Recorded, nullptr, false, Subject, 0.1f, Duration, 0.3f).FindRef(Jaw), MidEntry);
    const float MidExit = Blend.Step(Recorded, nullptr, false, Subject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw);
    const FName NewSubject(TEXT("NextUtterance"));
    TestEqual(TEXT("A new utterance waits at the current expression"),
        Blend.Step(Recorded, nullptr, true, NewSubject, 0.1f, Duration, 0.3f).FindRef(Jaw), MidExit);
    TestTrue(TEXT("Previous utterance curves cannot start a new subject"), Blend.IsWaitingForSpeech());
    TestEqual(TEXT("Re-entry does not jump to the old speech pose"),
        Blend.Step(Recorded, &Speech, true, NewSubject, 0.1f, Duration, 0.3f).FindRef(Jaw), MidExit);
    TestTrue(TEXT("Re-entry progresses toward the new speech"), FMath::IsNearlyEqual(
        Blend.Step(Recorded, &Speech, true, NewSubject, Duration / 2.0f, Duration, 0.3f).FindRef(Jaw),
        FMath::Lerp(MidExit, 0.8f, 0.5f)));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerFaceTransitionUpperFace,
    "Interviewer.FaceTransition.UpperFace",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerFaceTransitionUpperFace::RunTest(const FString& Parameters)
{
    FFaceCurveTransition Blend;
    const FName Blink(TEXT("CTRL_expressions_eyeblinkl"));
    const FName Gaze(TEXT("CTRL_expressions_eyelookleftl"));
    const FName Brow(TEXT("CTRL_expressions_browraiseinl"));
    const FName SpeechOnly(TEXT("CTRL_expressions_mouthstretchl"));
    auto Recorded = JawFrame(0.15f);
    Recorded.Add(Blink, 1.0f);
    Recorded.Add(Gaze, 0.4f);
    Recorded.Add(Brow, 0.7f);
    auto Speech = JawFrame(0.9f);
    Speech.Add(Blink, 0.0f);
    Speech.Add(Gaze, 0.95f);
    Speech.Add(Brow, 0.2f);
    Speech.Add(SpeechOnly, 0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 0.0f, Duration, 0.3f);
    Blend.Step(Recorded, &Speech, true, Subject, 0.0f, Duration, 0.3f);
    const auto& Result = Blend.Step(Recorded, &Speech, true, Subject, Duration, Duration, 0.3f);
    TestEqual(TEXT("Recorded blink preserves full eyelid closure"), Result.FindRef(Blink), 1.0f);
    TestEqual(TEXT("Recorded gaze overrides solver gaze"), Result.FindRef(Gaze), 0.4f);
    TestEqual(TEXT("Brows mix 30 percent recorded expression"), Result.FindRef(Brow), 0.35f, UE_KINDA_SMALL_NUMBER);
    TestEqual(TEXT("Speech jaw is not attenuated by recorded strength"), Result.FindRef(Jaw), 0.9f);
    TestEqual(TEXT("Speech-only mouth curves are included"), Result.FindRef(SpeechOnly), 0.8f);
    Blend.Step(Recorded, nullptr, false, Subject, 0.0f, Duration, 0.3f);
    const auto& AfterExit = Blend.Step(Recorded, nullptr, false, Subject, Duration, Duration, 0.3f);
    TestEqual(TEXT("Speech-only curves clear at exit rather than sticking on the face"), AfterExit.FindRef(SpeechOnly), 0.0f);
    return true;
}

#endif
