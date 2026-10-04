#pragma once

#include "InterviewerExpressionPlan.h"
#include "InterviewerFaceCurveTransition.h"
#include "InterviewerPresentationMotion.h"

class FJsonObject;

namespace UE::Interviewer
{
bool ParseExpressionPlan(const TSharedPtr<FJsonObject>& Message, FExpressionPlan& Plan, FString& Detail);
bool ParseExpressionClear(const TSharedPtr<FJsonObject>& Message, FString& PresentationId,
    int32& Generation, FString& Detail);
bool ValidateExpressionPlan(const FExpressionPlan& Plan, FString& Detail);
FFaceCurveMap MapExpressionIntent(const FExpressionIntent& Intent);
const FExpressionProfile& ExpressionProfile(const FExpressionSnapshot& Snapshot);

/** Irregular bounded motion generated locally from a model's four state profiles. */
class FExpressionLocalMotion
{
public:
    FFaceCurveMap Generate(const FExpressionSnapshot& Snapshot, bool bSpeaking,
        const FPresentationMotion* CoordinatedMotion = nullptr);

private:
    float Interval(int32 MinimumMs, int32 MaximumMs, float Variation);
    FRandomStream Random;
    bool bInitialized = false;
    double NextBlink = 0.0;
    double BlinkStarted = -1.0;
    double BlinkDuration = 0.18;
    double NextGaze = 0.0;
    double GazeStarted = 0.0;
    FVector2D GazeFrom = FVector2D::ZeroVector;
    FVector2D GazeTarget = FVector2D::ZeroVector;
    FVector2D GazeDisplayed = FVector2D::ZeroVector;
    double NextMotion = 0.0;
    double MotionStarted = -1.0;
    double MotionDuration = 1.5;
    float MotionHeight = 0.0f;
};

/** Recordings are used only while no healthy primary plan owns the face. */
class FExpressionPrimaryFace
{
public:
    const FFaceCurveMap& Step(const FExpressionSnapshot& Snapshot, const FFaceCurveMap& Recorded,
        bool bSpeaking, float DeltaSeconds, const FPresentationMotion* CoordinatedMotion = nullptr);
    const FFaceCurveMap& GetDisplayed() const { return Displayed; }
    float GetOwnershipAlpha() const { return OwnershipAlpha; }

private:
    FExpressionLocalMotion Motion;
    FFaceCurveMap Displayed;
    FFaceCurveMap From;
    TSet<FName> KnownNames;
    uint64 Revision = MAX_uint64;
    float Elapsed = 0.0f;
    float OwnershipAlpha = 0.0f;
    float OwnershipFrom = 0.0f;
    bool bInitialized = false;
    bool bLastHealthy = false;
    bool bBlending = false;
};
}
