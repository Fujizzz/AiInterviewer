#pragma once

#include "CoreMinimal.h"
#include "Templates/Atomic.h"

namespace UE::Interviewer
{
constexpr double SpeechSolverStepSeconds = 0.02;
constexpr double SpeechSolverLookaheadSeconds = 0.08;
constexpr double MaxSpeechAudioSeconds = 120.0;
constexpr int32 MaxSpeechCurves = 251;
constexpr int32 MaxSpeechFrames = 6005;

/** A result belongs to one request, even when the initialized model is reused. */
struct FSpeechPreparationFence
{
    explicit FSpeechPreparationFence(uint32 InGeneration) : Generation(InGeneration) {}
    bool Accepts(uint32 CurrentGeneration) const { return !bCancelled.Load() && Generation == CurrentGeneration; }
    void Cancel() { bCancelled.Store(true); }
    TAtomic<bool> bCancelled{false};
    const uint32 Generation;
};

struct FSpeechTimelineFrame
{
    double Seconds = 0.0;
    TArray<float> Values;
};

/** Immutable after preparation. All timestamps describe audible PCM, not inference wall time. */
struct FSpeechTimeline
{
    TArray<FName> Names;
    TArray<FSpeechTimelineFrame> Frames;
    double DurationSeconds = 0.0;

    bool HasValidNames() const
    {
        if (Names.IsEmpty() || Names.Num() > MaxSpeechCurves) return false;
        TSet<FName> Unique;
        for (FName Name : Names)
        {
            if (Name.IsNone() || Unique.Contains(Name)) return false;
            Unique.Add(Name);
        }
        return true;
    }

    bool AppendFrame(double InputEndSeconds, const TArray<float>& Values)
    {
        if (!FMath::IsFinite(DurationSeconds) || DurationSeconds <= 0.0 || DurationSeconds > MaxSpeechAudioSeconds
            || !FMath::IsFinite(InputEndSeconds) || InputEndSeconds < 0.0 || !HasValidNames()
            || Values.Num() != Names.Num()) return false;
        for (float Value : Values) if (!FMath::IsFinite(Value)) return false;
        double Seconds = InputEndSeconds - SpeechSolverLookaheadSeconds;
        // Outputs before PCM time zero are warmup state, not playback frames.
        if (Seconds < -1.e-7) return Frames.IsEmpty();
        Seconds = FMath::Max(0.0, Seconds);
        if (Seconds > DurationSeconds + SpeechSolverStepSeconds + 1.e-7 || Frames.Num() >= MaxSpeechFrames
            || (!Frames.IsEmpty() && Seconds <= Frames.Last().Seconds)) return false;
        FSpeechTimelineFrame Frame;
        Frame.Seconds = Seconds;
        Frame.Values = Values;
        Frames.Add(MoveTemp(Frame));
        return true;
    }

    bool Sample(double AudioSeconds, TMap<FName, float>& Out, float* OutFrame = nullptr,
        double* OutCurveSeconds = nullptr) const
    {
        Out.Reset();
        if (!FMath::IsFinite(AudioSeconds) || Frames.IsEmpty() || Frames.Num() > MaxSpeechFrames
            || !HasValidNames() || !FMath::IsFinite(DurationSeconds) || DurationSeconds <= 0.0
            || DurationSeconds > MaxSpeechAudioSeconds || !FMath::IsFinite(Frames[0].Seconds)
            || !FMath::IsFinite(Frames.Last().Seconds) || Frames[0].Seconds < 0.0
            || Frames[0].Seconds > FMath::Min(DurationSeconds, Frames.Last().Seconds)) return false;
        const double Seconds = FMath::Clamp(AudioSeconds, Frames[0].Seconds,
            FMath::Min(DurationSeconds, Frames.Last().Seconds));
        int32 Low = 0, High = Frames.Num() - 1;
        while (Low < High)
        {
            const int32 Mid = Low + (High - Low + 1) / 2;
            if (Frames[Mid].Seconds <= Seconds) Low = Mid;
            else High = Mid - 1;
        }
        const int32 Next = FMath::Min(Low + 1, Frames.Num() - 1);
        const auto& A = Frames[Low];
        const auto& B = Frames[Next];
        if (A.Values.Num() != Names.Num() || B.Values.Num() != Names.Num()) return false;
        const double Gap = B.Seconds - A.Seconds;
        const float Alpha = Gap > UE_SMALL_NUMBER ? static_cast<float>((Seconds - A.Seconds) / Gap) : 0.0f;
        for (int32 Index = 0; Index < Names.Num(); ++Index)
        {
            const float Value = FMath::Lerp(A.Values[Index], B.Values[Index], Alpha);
            if (!FMath::IsFinite(Value)) { Out.Reset(); return false; }
            Out.Add(Names[Index], Value);
        }
        if (OutFrame) *OutFrame = static_cast<float>(Low) + Alpha;
        if (OutCurveSeconds) *OutCurveSeconds = FMath::Lerp(A.Seconds, B.Seconds, static_cast<double>(Alpha));
        return true;
    }

    double GetMaxFrameGapSeconds() const
    {
        double Gap = 0.0;
        for (int32 Index = 1; Index < Frames.Num(); ++Index)
            Gap = FMath::Max(Gap, Frames[Index].Seconds - Frames[Index - 1].Seconds);
        return Gap;
    }
};
}
