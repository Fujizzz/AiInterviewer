#pragma once

#include "CoreMinimal.h"

namespace UE::Interviewer
{
enum class EExpressionIntent : uint8 { Neutral, Attentive, Thoughtful, Friendly, Emphasis };

struct FExpressionIntent
{
    EExpressionIntent Expression = EExpressionIntent::Neutral;
    float Intensity = 0.0f;
};

struct FExpressionProfile : FExpressionIntent
{
    float Variation = 0.5f;
    int32 BlinkMinMs = 2800;
    int32 BlinkMaxMs = 6500;
    int32 BlinkDurationMs = 180;
    float GazeAmplitude = 0.06f;
    int32 GazeHoldMinMs = 1800;
    int32 GazeHoldMaxMs = 3500;
    float EyeContact = 0.85f;
    float Warmth = 0.03f;
    int32 MotionMinMs = 3500;
    int32 MotionMaxMs = 7000;
    // Optional V2 controls default to zero for legacy profiles.
    float HeadMotionStrength = 0.0f;
    float HeadMotionProbability = 0.0f;
    float AudioEmphasisStrength = 0.0f;
};

/** Semantic presentation data, never arbitrary facial controls or candidate scores. */
struct FExpressionPlan
{
    FString PresentationId;
    int32 Generation = 0;
    FString QuestionId;
    FString UtteranceId;
    FString Source;
    FString Reason;
    int32 TransitionMs = 350;
    int32 ValidMs = 60000;
    FExpressionProfile Idle;
    FExpressionProfile Listening;
    FExpressionProfile Thinking;
    FExpressionProfile Speaking;
};

struct FExpressionSnapshot
{
    FExpressionPlan Plan;
    FString State = TEXT("idle");
    uint64 Revision = 0;
    bool bHasPlan = false;
    bool bPrimaryHealthy = false;
    bool bPlaybackMatches = false;
    double ExpiresAt = 0.0;
    double PlaybackStarted = 0.0;
    double Now = 0.0;
};

enum class EExpressionApplyResult : uint8 { Applied, Ignored, Rejected };

/** Game-thread ownership; the animation proxy receives a detached value snapshot. */
class INTERVIEWERRUNTIME_API FExpressionPlanRuntime
{
public:
    EExpressionApplyResult Apply(const FExpressionPlan& Incoming, FString& Detail,
        double Now = FPlatformTime::Seconds());
    EExpressionApplyResult Clear(const FString& PresentationId, int32 Generation, FString& Detail);
    void SetState(const FString& State);
    void PlaybackStarted(const FString& UtteranceId, double Now);
    void PlaybackStopped(const FString& UtteranceId);
    FExpressionSnapshot Snapshot(double Now) const;

private:
    struct FRevisionRecord
    {
        int32 Generation = 0;
        FString QuestionId;
        bool bCleared = false;
        double ExpiresAt = 0.0;
    };
    void RememberPresentation(const FString& PresentationId);
    FExpressionPlan Plan;
    FString CurrentState = TEXT("idle");
    FString PlaybackId;
    TMap<FString, FRevisionRecord> Presentations;
    TArray<FString> PresentationOrder;
    TArray<FString> EndedUtterances;
    uint64 Revision = 0;
    double PlaybackAnchor = 0.0;
    bool bHasPlan = false;
    bool bPlaying = false;
};
}
