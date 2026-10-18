#pragma once

#include "CoreMinimal.h"

namespace UE::Interviewer
{
using FFaceCurveMap = TMap<FName, float>;

inline bool IsExpressionCurve(FName Name)
{
    return Name.ToString().StartsWith(TEXT("CTRL_expressions_"), ESearchCase::IgnoreCase);
}

inline bool IsRecordedEyeControl(FName Name)
{
    const FString Curve = Name.ToString();
    return Curve.StartsWith(TEXT("CTRL_expressions_eyeblink"), ESearchCase::IgnoreCase)
        || Curve.StartsWith(TEXT("CTRL_expressions_eyelook"), ESearchCase::IgnoreCase);
}

inline bool IsUpperFaceControl(FName Name)
{
    const FString Curve = Name.ToString();
    return Curve.StartsWith(TEXT("CTRL_expressions_brow"), ESearchCase::IgnoreCase)
        || Curve.StartsWith(TEXT("CTRL_expressions_eye"), ESearchCase::IgnoreCase);
}

/** Curves only: the copied body pose and the Face post-process remain untouched. */
class FFaceCurveTransition
{
public:
    const FFaceCurveMap& Step(const FFaceCurveMap& Recorded, const FFaceCurveMap* Speech,
        bool bSpeak, FName Subject, float DeltaSeconds, float Duration, float RecordedUpperFaceWeight,
        float PrimaryOwnership = 0.0f)
    {
        RememberNames(Recorded);
        if (Speech) RememberNames(*Speech);

        if (!bInitialized)
        {
            Displayed = Recorded;
            bInitialized = true;
        }

        bool bJustStarted = false;
        if (bSpeak != bSpeaking || (bSpeak && Subject != SpeechSubject))
        {
            // A reversal starts at what was actually drawn, not at either old source.
            TransitionFrom = Displayed;
            Elapsed = 0.0f;
            // Preserve the selected boundary duration through subsequent state updates.
            ActiveDuration = FMath::Max(0.0f, Duration);
            bTransitioning = true;
            bWaitingForSpeech = bSpeak;
            bSpeaking = bSpeak;
            SpeechSubject = Subject;
            if (bSpeak) LastSpeech.Reset();
            bJustStarted = true;
        }

        if (bSpeaking && Speech && !Speech->IsEmpty())
        {
            LastSpeech = *Speech;
            if (bWaitingForSpeech)
            {
                bWaitingForSpeech = false;
                bJustStarted = true;
            }
        }

        // Do not fade toward a neutral placeholder while the solver starts up.
        if (bWaitingForSpeech)
        {
            if (PrimaryOwnership > 0.0f)
            {
                // Keep the last visible mouth while the audio solver warms up,
                // but allow the healthy speaking profile to animate the upper face.
                for (FName Name : KnownNames)
                    if (IsUpperFaceControl(Name))
                    {
                        Displayed.FindOrAdd(Name) = Recorded.FindRef(Name);
                        TransitionFrom.FindOrAdd(Name) = Displayed.FindRef(Name);
                    }
            }
            return Displayed;
        }

        FFaceCurveMap Destination;
        for (FName Name : KnownNames)
        {
            float Value = Recorded.FindRef(Name);
            if (bSpeaking)
            {
                // Keep the last valid frame if its Live Link source disappears.
                Value = LastSpeech.FindRef(Name);
                if (const float* RecordedValue = Recorded.Find(Name))
                {
                    if (IsRecordedEyeControl(Name)) Value = *RecordedValue;
                    else if (IsUpperFaceControl(Name))
                        Value = FMath::Lerp(Value, *RecordedValue,
                            FMath::Clamp(RecordedUpperFaceWeight, 0.0f, 1.0f));
                }
                else if (PrimaryOwnership > 0.0f && IsUpperFaceControl(Name))
                {
                    // An upper-face control present only in solver data must not
                    // leak into a primary Agent face merely because no clip named it.
                    Value = FMath::Lerp(Value, 0.0f, FMath::Clamp(PrimaryOwnership, 0.0f, 1.0f));
                }
            }
            Destination.Add(Name, Value);
        }

        if (bTransitioning)
        {
            if (!bJustStarted) Elapsed += FMath::Max(0.0f, DeltaSeconds);
            const float Linear = ActiveDuration > UE_SMALL_NUMBER
                ? FMath::Clamp(Elapsed / ActiveDuration, 0.0f, 1.0f) : 1.0f;
            Alpha = Linear * Linear * (3.0f - 2.0f * Linear);
            if (Linear >= 1.0f)
            {
                Displayed = MoveTemp(Destination);
                bTransitioning = false;
                TransitionFrom.Reset();
            }
            else
            {
                Displayed.Reset();
                for (FName Name : KnownNames)
                    Displayed.Add(Name, FMath::Lerp(TransitionFrom.FindRef(Name), Destination.FindRef(Name), Alpha));
            }
        }
        else
        {
            // Ordinary visemes pass through directly after the short boundary blend.
            Displayed = MoveTemp(Destination);
            Alpha = 1.0f;
        }
        return Displayed;
    }

    bool IsTransitioning() const { return bTransitioning; }
    bool IsWaitingForSpeech() const { return bWaitingForSpeech; }
    float GetAlpha() const { return bWaitingForSpeech ? 0.0f : Alpha; }
    const FFaceCurveMap& GetDisplayed() const { return Displayed; }
    int32 GetSpeechCurveCount() const { return LastSpeech.Num(); }

private:
    void RememberNames(const FFaceCurveMap& Curves)
    {
        for (const auto& Curve : Curves) KnownNames.Add(Curve.Key);
    }

    FFaceCurveMap Displayed;
    FFaceCurveMap TransitionFrom;
    FFaceCurveMap LastSpeech;
    TSet<FName> KnownNames;
    FName SpeechSubject;
    float Elapsed = 0.0f;
    float ActiveDuration = 0.3f;
    float Alpha = 1.0f;
    bool bInitialized = false;
    bool bSpeaking = false;
    bool bTransitioning = false;
    bool bWaitingForSpeech = false;
};
}
