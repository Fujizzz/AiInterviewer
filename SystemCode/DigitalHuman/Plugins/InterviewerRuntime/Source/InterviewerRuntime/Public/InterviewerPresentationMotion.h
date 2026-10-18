#pragma once

#include "CoreMinimal.h"
#include "InterviewerExpressionPlan.h"

class FJsonObject;

namespace UE::Interviewer
{
struct FListeningActivity
{
    FString PresentationId;
    FString CaptureId;
    int32 CaptureGeneration = 0;
    int32 Sequence = 0;
    bool bActive = false;
    bool bEnded = false;
};

struct FListeningSnapshot
{
    bool bFresh = false;
    bool bActive = false;
    uint64 PauseSerial = 0;
    int32 CaptureGeneration = 0;
};

/** Correlation and retirement fences survive question/profile refreshes. */
class INTERVIEWERRUNTIME_API FListeningActivityRuntime
{
public:
    EExpressionApplyResult Apply(const FListeningActivity& Activity, const FExpressionSnapshot& Plan, FString& Detail);
    FListeningSnapshot Snapshot(const FExpressionSnapshot& Plan) const;
private:
    struct FCapture
    {
        FString Id;
        int32 Generation = 0;
        int32 Sequence = 0;
        double ReceivedAt = 0;
        double VoicedAt = 0;
        double LastVoicedAt = 0;
        uint64 PauseSerial = 0;
        bool bActive = false;
        bool bObservedVoice = false;
        bool bEnded = false;
    };
    TMap<FString, FCapture> Captures;
    TArray<FString> Order;
};

INTERVIEWERRUNTIME_API bool ParseListeningActivity(const TSharedPtr<FJsonObject>& Message,
    FListeningActivity& Activity, FString& Detail);

struct FSpeechRhythm
{
    bool bPlaying = false;
    uint32 PlaybackGeneration = 0;
    float Rms = 0;
    double AudibleSeconds = 0;
};

struct FPresentationMotion
{
    FRotator Head = FRotator::ZeroRotator;
    FVector2D Gaze = FVector2D::ZeroVector;
    float GazeWeight = 0;
    float BrowEmphasis = 0;
};

/** Continuous local timing; no model calls and no candidate meaning is inferred. */
class INTERVIEWERRUNTIME_API FPresentationMotionRuntime
{
public:
    FPresentationMotion Step(const FExpressionSnapshot& Plan, const FListeningSnapshot& Listening,
        const FSpeechRhythm& Rhythm, float DeltaSeconds);
private:
    FRandomStream Random;
    FPresentationMotion Displayed;
    FString State;
    FString Presentation;
    uint64 SeenPause = 0;
    int32 SeenCaptureGeneration = 0;
    uint32 PlaybackGeneration = 0;
    double NextMotion = 0;
    double Started = -1;
    double Duration = 0.7;
    double QuietSince = -1;
    double LastAudioAccent = -100;
    float PreviousRms = 0;
    float Height = 0;
    bool bInitialized = false;
    bool bThinkingGesture = false;
};

/** Computes a short trailing PCM window at the audible playback clock. */
INTERVIEWERRUNTIME_API float PcmWindowRms(TConstArrayView<int16> Samples, int32 SampleRate, double AudibleSeconds);
}
