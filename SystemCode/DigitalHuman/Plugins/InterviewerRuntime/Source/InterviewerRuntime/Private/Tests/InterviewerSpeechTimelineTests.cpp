#include "Misc/AutomationTest.h"

#include "InterviewerSpeechTimeline.h"
#include <limits>

#if WITH_DEV_AUTOMATION_TESTS

using namespace UE::Interviewer;
namespace
{
constexpr EAutomationTestFlags SpeechFlags = EAutomationTestFlags::EditorContext
    | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter;
const FName TimelineJaw(TEXT("CTRL_expressions_jawopen"));
const FName TimelineLip(TEXT("CTRL_expressions_mouthstretchl"));

FSpeechTimeline MakeLinearTimeline()
{
    FSpeechTimeline Timeline;
    Timeline.DurationSeconds = 0.08;
    Timeline.Names = {TimelineJaw, TimelineLip};
    for (int32 Index = 0; Index <= 4; ++Index)
    {
        const float Seconds = Index * 0.02f;
        Timeline.AppendFrame(Index * SpeechSolverStepSeconds + SpeechSolverLookaheadSeconds,
            {Seconds * 10.0f, 0.6f - Seconds * 2.0f});
    }
    return Timeline;
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechTimelineInterpolation,
    "Interviewer.SpeechTimeline.Interpolation", SpeechFlags);

bool FInterviewerSpeechTimelineInterpolation::RunTest(const FString& Parameters)
{
    const FSpeechTimeline Timeline = MakeLinearTimeline();
    TMap<FName, float> Curves;
    // Deliberately skip render updates. Sampling follows audio time rather than
    // repeating the newest solved frame or accumulating rendered delta time.
    for (double Seconds : {0.0, 0.007, 0.018, 0.034, 0.065, 0.077, 0.08})
    {
        float Frame = -1.0f;
        double CurveSeconds = -1.0;
        TestTrue(TEXT("An irregular playback update has a prepared sample"), Timeline.Sample(Seconds, Curves, &Frame, &CurveSeconds));
        TestEqual(TEXT("Jaw samples the audible clock between native frames"), Curves.FindRef(TimelineJaw), static_cast<float>(Seconds * 10.0), 0.00001f);
        TestEqual(TEXT("Lip samples the same audible clock"), Curves.FindRef(TimelineLip), static_cast<float>(0.6 - Seconds * 2.0), 0.00001f);
        TestEqual(TEXT("Curve and audio clocks coincide"), CurveSeconds, Seconds, 0.000001);
        TestEqual(TEXT("Fractional frame is independent of render cadence"), Frame, static_cast<float>(Seconds / SpeechSolverStepSeconds), 0.00001f);
    }
    TestTrue(TEXT("Sampling before playback holds its first prepared pose"), Timeline.Sample(-1.0, Curves));
    TestEqual(TEXT("Before playback does not invent a prior mouth frame"), Curves.FindRef(TimelineJaw), 0.0f);
    TestTrue(TEXT("Sampling beyond the clip holds its prepared final pose"), Timeline.Sample(5.0, Curves));
    TestEqual(TEXT("After playback does not wrap into an old utterance"), Curves.FindRef(TimelineJaw), 0.8f, 0.00001f);
    TestEqual(TEXT("Dense generated frames report a 20 ms maximum gap"), Timeline.GetMaxFrameGapSeconds(), SpeechSolverStepSeconds, 0.000001);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechTimelineLookahead,
    "Interviewer.SpeechTimeline.Lookahead", SpeechFlags);

bool FInterviewerSpeechTimelineLookahead::RunTest(const FString& Parameters)
{
    FSpeechTimeline Timeline;
    Timeline.DurationSeconds = 0.06;
    Timeline.Names = {TimelineJaw};
    for (int32 Step = 1; Step <= 3; ++Step)
        TestTrue(TEXT("Negative-time solver warmup outputs are valid but discarded"),
            Timeline.AppendFrame(Step * SpeechSolverStepSeconds, {Step * 0.1f}));
    TestEqual(TEXT("80 ms lookahead does not leak warmup into time zero"), Timeline.Frames.Num(), 0);
    for (int32 Step = 4; Step <= 7; ++Step)
        TestTrue(TEXT("The complete clip includes lookahead-padded tail frames"),
            Timeline.AppendFrame(Step * SpeechSolverStepSeconds, {Step * 0.1f}));
    TestEqual(TEXT("Input ending at 80 ms is timestamped at audio time zero"), Timeline.Frames[0].Seconds, 0.0);
    TestEqual(TEXT("Next output uses input end time, avoiding a 20 ms index error"), Timeline.Frames[1].Seconds, 0.02, 0.000001);
    TMap<FName, float> Curves;
    TestTrue(TEXT("Time-zero output is ready before Audio Play"), Timeline.Sample(0.0, Curves));
    TestEqual(TEXT("Time zero uses the fourth recurrent output"), Curves.FindRef(TimelineJaw), 0.4f);
    Timeline.Sample(0.06, Curves);
    TestEqual(TEXT("Padding keeps the final audible sample covered"), Curves.FindRef(TimelineJaw), 0.7f, 0.00001f);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechTimelineValidation,
    "Interviewer.SpeechTimeline.Validation", SpeechFlags);

bool FInterviewerSpeechTimelineValidation::RunTest(const FString& Parameters)
{
    FSpeechTimeline Timeline;
    Timeline.DurationSeconds = 1.0;
    Timeline.Names = {TimelineJaw};
    TestFalse(TEXT("Negative input position is rejected"), Timeline.AppendFrame(-0.01, {0.1f}));
    TestFalse(TEXT("Nonfinite input position is rejected"), Timeline.AppendFrame(std::numeric_limits<double>::infinity(), {0.1f}));
    TestFalse(TEXT("Nonfinite solver values are rejected"), Timeline.AppendFrame(0.08, {std::numeric_limits<float>::quiet_NaN()}));
    TestFalse(TEXT("Mismatched curve layouts are rejected"), Timeline.AppendFrame(0.08, {0.1f, 0.2f}));
    TestTrue(TEXT("The first finite pose is accepted"), Timeline.AppendFrame(0.08, {0.1f}));
    TestFalse(TEXT("Duplicate timestamps cannot overwrite a prepared pose"), Timeline.AppendFrame(0.08, {0.9f}));
    TestFalse(TEXT("An unordered pose is rejected"), Timeline.AppendFrame(0.079, {0.9f}));
    TestFalse(TEXT("A frame outside the bounded audio tail is rejected"), Timeline.AppendFrame(1.2, {0.9f}));
    TMap<FName, float> Curves;
    TestFalse(TEXT("Nonfinite playback clocks do not reach the Face"), Timeline.Sample(std::numeric_limits<double>::quiet_NaN(), Curves));
    TestTrue(TEXT("An invalid sample clears prior curve output"), Curves.IsEmpty());
    Timeline.Names = {TimelineJaw, TimelineJaw};
    TestFalse(TEXT("Duplicate control names are rejected"), Timeline.AppendFrame(0.1, {0.1f, 0.2f}));
    Timeline.Names = {NAME_None};
    TestFalse(TEXT("Unnamed controls are rejected"), Timeline.AppendFrame(0.1, {0.1f}));
    Timeline.Names.Reset();
    TArray<float> Values;
    for (int32 Index = 0; Index <= MaxSpeechCurves; ++Index)
    {
        Timeline.Names.Add(FName(*FString::Printf(TEXT("Curve%d"), Index)));
        Values.Add(0.1f);
    }
    TestFalse(TEXT("More than 251 controls is rejected"), Timeline.AppendFrame(0.1, Values));
    Timeline.Names = {TimelineJaw};
    Timeline.Frames.Reset();
    Timeline.DurationSeconds = MaxSpeechAudioSeconds + 0.01;
    TestFalse(TEXT("Audio beyond 120 seconds is rejected"), Timeline.AppendFrame(0.08, {0.1f}));
    Timeline.DurationSeconds = MaxSpeechAudioSeconds;
    for (int32 Index = 0; Index < MaxSpeechFrames; ++Index)
        if (!Timeline.AppendFrame(SpeechSolverLookaheadSeconds + Index * 0.001, {0.1f}))
        { AddError(TEXT("Valid bounded capacity failed before its limit")); return false; }
    TestFalse(TEXT("The prepared timeline has a hard frame-count bound"),
        Timeline.AppendFrame(SpeechSolverLookaheadSeconds + MaxSpeechFrames * 0.001, {0.1f}));
    TestEqual(TEXT("Rejected capacity does not grow stored results"), Timeline.Frames.Num(), MaxSpeechFrames);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechTimelineCancellation,
    "Interviewer.SpeechTimeline.Cancellation", SpeechFlags);

bool FInterviewerSpeechTimelineCancellation::RunTest(const FString& Parameters)
{
    auto OldJob = MakeShared<FSpeechPreparationFence, ESPMode::ThreadSafe>(7);
    TestTrue(TEXT("An active result may commit only to its request generation"), OldJob->Accepts(7));
    TestFalse(TEXT("A late result cannot commit to a newer utterance"), OldJob->Accepts(8));
    OldJob->Cancel();
    TestFalse(TEXT("Stopped preparation cannot start sound even with its old generation"), OldJob->Accepts(7));
    TestFalse(TEXT("Canceled results remain retired at a newer generation"), OldJob->Accepts(8));
    auto NewJob = MakeShared<FSpeechPreparationFence, ESPMode::ThreadSafe>(8);
    TestTrue(TEXT("A fresh utterance can prepare while the old job retires"), NewJob->Accepts(8));
    TestFalse(TEXT("The fresh result cannot be mistaken for the old request"), NewJob->Accepts(7));
    return true;
}

#endif
