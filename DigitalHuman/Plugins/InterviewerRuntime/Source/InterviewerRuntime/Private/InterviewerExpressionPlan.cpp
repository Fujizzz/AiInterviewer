#include "InterviewerExpressionPlan.h"
#include "InterviewerExpressionPlanHelpers.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/Guid.h"
#include <initializer_list>

namespace UE::Interviewer
{
namespace
{
bool IsUuid(const FString& Value)
{
    FGuid Guid;
    return FGuid::ParseExact(Value, EGuidFormats::DigitsWithHyphens, Guid) && Guid.IsValid();
}

bool Fields(const TSharedPtr<FJsonObject>& Object, std::initializer_list<const TCHAR*> Required,
    std::initializer_list<const TCHAR*> Optional = {})
{
    if (!Object.IsValid()) return false;
    TSet<FString> Allowed;
    for (const TCHAR* Name : Required)
    {
        if (!Object->HasField(Name)) return false;
        Allowed.Add(Name);
    }
    for (const TCHAR* Name : Optional) Allowed.Add(Name);
    for (const auto& Field : Object->Values) if (!Allowed.Contains(FString(*Field.Key))) return false;
    return true;
}

bool Integer(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, int32 Minimum,
    int32 Maximum, int32& Result)
{
    const auto Value = Object->TryGetField(Name);
    if (!Value.IsValid() || Value->Type != EJson::Number) return false;
    const double Number = Value->AsNumber();
    if (!FMath::IsFinite(Number) || Number < Minimum || Number > Maximum
        || FMath::FloorToDouble(Number) != Number) return false;
    Result = static_cast<int32>(Number);
    return true;
}

bool Float(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, double Minimum,
    double Maximum, float& Result)
{
    const auto Value = Object->TryGetField(Name);
    if (!Value.IsValid() || Value->Type != EJson::Number) return false;
    const double Number = Value->AsNumber();
    if (!FMath::IsFinite(Number) || Number < Minimum || Number > Maximum) return false;
    Result = static_cast<float>(Number);
    return true;
}

bool String(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, FString& Result)
{
    const auto Value = Object->TryGetField(Name);
    // UE's string accessor also coerces numbers and booleans; the wire contract does not.
    return Value.IsValid() && Value->Type == EJson::String && Value->TryGetString(Result);
}

bool ParseProfile(const TSharedPtr<FJsonObject>& Object, FExpressionProfile& Profile)
{
    if (!Fields(Object, {TEXT("expression"), TEXT("intensity"), TEXT("variation"),
        TEXT("blink_min_ms"), TEXT("blink_max_ms"), TEXT("blink_duration_ms"),
        TEXT("gaze_amplitude"), TEXT("gaze_hold_min_ms"), TEXT("gaze_hold_max_ms"),
        TEXT("eye_contact"), TEXT("warmth"), TEXT("motion_min_ms"), TEXT("motion_max_ms")},
        {TEXT("head_motion_strength"), TEXT("head_motion_probability"), TEXT("audio_emphasis_strength")})) return false;
    if ((Object->HasField(TEXT("head_motion_strength")) && !Float(Object, TEXT("head_motion_strength"), 0, 1, Profile.HeadMotionStrength))
        || (Object->HasField(TEXT("head_motion_probability")) && !Float(Object, TEXT("head_motion_probability"), 0, 1, Profile.HeadMotionProbability))
        || (Object->HasField(TEXT("audio_emphasis_strength")) && !Float(Object, TEXT("audio_emphasis_strength"), 0, 1, Profile.AudioEmphasisStrength))) return false;
    FString Name;
    if (!String(Object, TEXT("expression"), Name)) return false;
    if (Name == TEXT("neutral")) Profile.Expression = EExpressionIntent::Neutral;
    else if (Name == TEXT("attentive")) Profile.Expression = EExpressionIntent::Attentive;
    else if (Name == TEXT("thoughtful")) Profile.Expression = EExpressionIntent::Thoughtful;
    else if (Name == TEXT("friendly")) Profile.Expression = EExpressionIntent::Friendly;
    else if (Name == TEXT("emphasis")) Profile.Expression = EExpressionIntent::Emphasis;
    else return false;
    return Float(Object, TEXT("intensity"), 0, 0.35, Profile.Intensity)
        && Float(Object, TEXT("variation"), 0, 1, Profile.Variation)
        && Integer(Object, TEXT("blink_min_ms"), 1800, 9000, Profile.BlinkMinMs)
        && Integer(Object, TEXT("blink_max_ms"), 2500, 12000, Profile.BlinkMaxMs)
        && Profile.BlinkMaxMs >= Profile.BlinkMinMs + 500
        && Integer(Object, TEXT("blink_duration_ms"), 120, 250, Profile.BlinkDurationMs)
        && Float(Object, TEXT("gaze_amplitude"), 0, 0.15, Profile.GazeAmplitude)
        && Integer(Object, TEXT("gaze_hold_min_ms"), 1000, 4500, Profile.GazeHoldMinMs)
        && Integer(Object, TEXT("gaze_hold_max_ms"), 1800, 6500, Profile.GazeHoldMaxMs)
        && Profile.GazeHoldMaxMs >= Profile.GazeHoldMinMs + 300
        && Float(Object, TEXT("eye_contact"), 0.65, 1, Profile.EyeContact)
        && Float(Object, TEXT("warmth"), 0, 0.15, Profile.Warmth)
        && Integer(Object, TEXT("motion_min_ms"), 2500, 8000, Profile.MotionMinMs)
        && Integer(Object, TEXT("motion_max_ms"), 4000, 16000, Profile.MotionMaxMs)
        && Profile.MotionMaxMs >= Profile.MotionMinMs + 500;
}

bool Within(float Value, float Minimum, float Maximum)
{
    return FMath::IsFinite(Value) && Value >= Minimum && Value <= Maximum;
}

bool ProfileValid(const FExpressionProfile& P)
{
    return static_cast<uint8>(P.Expression) <= static_cast<uint8>(EExpressionIntent::Emphasis)
        && Within(P.Intensity, 0, 0.35f) && Within(P.Variation, 0, 1)
        && P.BlinkMinMs >= 1800 && P.BlinkMinMs <= 9000
        && P.BlinkMaxMs >= 2500 && P.BlinkMaxMs <= 12000 && P.BlinkMaxMs >= P.BlinkMinMs + 500
        && P.BlinkDurationMs >= 120 && P.BlinkDurationMs <= 250
        && Within(P.GazeAmplitude, 0, 0.15f) && Within(P.EyeContact, 0.65f, 1) && Within(P.Warmth, 0, 0.15f)
        && P.GazeHoldMinMs >= 1000 && P.GazeHoldMinMs <= 4500
        && P.GazeHoldMaxMs >= 1800 && P.GazeHoldMaxMs <= 6500 && P.GazeHoldMaxMs >= P.GazeHoldMinMs + 300
        && P.MotionMinMs >= 2500 && P.MotionMinMs <= 8000
        && P.MotionMaxMs >= 4000 && P.MotionMaxMs <= 16000 && P.MotionMaxMs >= P.MotionMinMs + 500
        && Within(P.HeadMotionStrength, 0, 1) && Within(P.HeadMotionProbability, 0, 1)
        && Within(P.AudioEmphasisStrength, 0, 1);
}

float Smooth(double Value)
{
    const float X = static_cast<float>(FMath::Clamp(Value, 0.0, 1.0));
    return X * X * (3.0f - 2.0f * X);
}
}

bool ValidateExpressionPlan(const FExpressionPlan& Plan, FString& Detail)
{
    if (!IsUuid(Plan.PresentationId) || Plan.Generation < 1 || Plan.QuestionId.IsEmpty()
        || Plan.QuestionId.Len() > 128 || (!Plan.UtteranceId.IsEmpty() && !IsUuid(Plan.UtteranceId))
        || (Plan.Source != TEXT("model") && Plan.Source != TEXT("fallback") && Plan.Source != TEXT("preview"))
        || Plan.TransitionMs != 350 || Plan.ValidMs < 1 || Plan.ValidMs > 600000 || Plan.Reason.Len() > 96
        || !ProfileValid(Plan.Idle) || !ProfileValid(Plan.Listening)
        || !ProfileValid(Plan.Thinking) || !ProfileValid(Plan.Speaking))
    {
        Detail = TEXT("Invalid V2 expression identity, profile or bounds");
        return false;
    }
    return true;
}

bool ParseExpressionPlan(const TSharedPtr<FJsonObject>& Message, FExpressionPlan& OutPlan, FString& Detail)
{
    Detail = TEXT("Invalid V2 expression fields or types");
    if (!Fields(Message, {TEXT("type"), TEXT("version"), TEXT("presentation_id"), TEXT("generation"),
        TEXT("question_id"), TEXT("utterance_id"), TEXT("source"), TEXT("transition_ms"), TEXT("valid_ms"), TEXT("states")},
        {TEXT("reason")})) return false;
    FExpressionPlan Parsed;
    FString Type;
    int32 Version = 0;
    if (!String(Message, TEXT("type"), Type) || Type != TEXT("expression_plan")
        || !Integer(Message, TEXT("version"), 2, 2, Version)
        || !Integer(Message, TEXT("generation"), 1, MAX_int32, Parsed.Generation)
        || !Integer(Message, TEXT("transition_ms"), 350, 350, Parsed.TransitionMs)
        || !Integer(Message, TEXT("valid_ms"), 1, 600000, Parsed.ValidMs)
        || !String(Message, TEXT("presentation_id"), Parsed.PresentationId)
        || !String(Message, TEXT("question_id"), Parsed.QuestionId)
        || !String(Message, TEXT("utterance_id"), Parsed.UtteranceId)
        || !String(Message, TEXT("source"), Parsed.Source)) return false;
    if (Message->HasField(TEXT("reason")))
    {
        const auto Reason = Message->TryGetField(TEXT("reason"));
        if (!Reason.IsValid() || (Reason->Type != EJson::Null && !String(Message, TEXT("reason"), Parsed.Reason))) return false;
    }
    const TSharedPtr<FJsonObject>* States = nullptr;
    if (!Message->TryGetObjectField(TEXT("states"), States)
        || !Fields(*States, {TEXT("idle"), TEXT("listening"), TEXT("thinking"), TEXT("speaking")})) return false;
    for (auto Entry : {TPair<const TCHAR*, FExpressionProfile*>(TEXT("idle"), &Parsed.Idle),
        TPair<const TCHAR*, FExpressionProfile*>(TEXT("listening"), &Parsed.Listening),
        TPair<const TCHAR*, FExpressionProfile*>(TEXT("thinking"), &Parsed.Thinking),
        TPair<const TCHAR*, FExpressionProfile*>(TEXT("speaking"), &Parsed.Speaking)})
    {
        const TSharedPtr<FJsonObject>* Profile = nullptr;
        if (!(*States)->TryGetObjectField(Entry.Key, Profile) || !ParseProfile(*Profile, *Entry.Value)) return false;
    }
    if (!ValidateExpressionPlan(Parsed, Detail)) return false;
    OutPlan = MoveTemp(Parsed);
    Detail.Reset();
    return true;
}

bool ParseExpressionClear(const TSharedPtr<FJsonObject>& Message, FString& PresentationId,
    int32& Generation, FString& Detail)
{
    Detail = TEXT("Invalid expression clear identity or generation");
    FString Type;
    if (!Fields(Message, {TEXT("type"), TEXT("presentation_id"), TEXT("generation")})
        || !String(Message, TEXT("type"), Type) || Type != TEXT("expression_clear")
        || !String(Message, TEXT("presentation_id"), PresentationId) || !IsUuid(PresentationId)
        || !Integer(Message, TEXT("generation"), 1, MAX_int32, Generation)) return false;
    Detail.Reset();
    return true;
}

void FExpressionPlanRuntime::RememberPresentation(const FString& Id)
{
    if (Presentations.Contains(Id)) return;
    if (PresentationOrder.Num() >= 64)
    {
        Presentations.Remove(PresentationOrder[0]);
        PresentationOrder.RemoveAt(0);
    }
    PresentationOrder.Add(Id);
    Presentations.Add(Id, FRevisionRecord());
}

EExpressionApplyResult FExpressionPlanRuntime::Apply(const FExpressionPlan& Incoming, FString& Detail, double Now)
{
    if (!ValidateExpressionPlan(Incoming, Detail) || !FMath::IsFinite(Now)) return EExpressionApplyResult::Rejected;
    RememberPresentation(Incoming.PresentationId);
    FRevisionRecord& Record = Presentations.FindChecked(Incoming.PresentationId);
    if (Incoming.Generation < Record.Generation)
    {
        Detail = TEXT("Ignored stale expression generation");
        return EExpressionApplyResult::Ignored;
    }
    if (Incoming.Generation == Record.Generation && (Record.bCleared || Record.QuestionId != Incoming.QuestionId))
    {
        Detail = TEXT("Expression generation was cleared or belongs to another question");
        return EExpressionApplyResult::Rejected;
    }
    const double ReceivedExpiry = Now + Incoming.ValidMs / 1000.0;
    Record.ExpiresAt = Incoming.Generation == Record.Generation
        ? FMath::Min(Record.ExpiresAt, ReceivedExpiry) : ReceivedExpiry;
    Record.Generation = Incoming.Generation;
    Record.QuestionId = Incoming.QuestionId;
    Record.bCleared = false;
    Plan = Incoming;
    bHasPlan = true;
    ++Revision;
    Detail.Reset();
    return EExpressionApplyResult::Applied;
}

EExpressionApplyResult FExpressionPlanRuntime::Clear(const FString& Id, int32 Generation, FString& Detail)
{
    if (!IsUuid(Id) || Generation < 1)
    {
        Detail = TEXT("Invalid expression clear identity or generation");
        return EExpressionApplyResult::Rejected;
    }
    RememberPresentation(Id);
    FRevisionRecord& Record = Presentations.FindChecked(Id);
    if (Generation < Record.Generation)
    {
        Detail = TEXT("Ignored stale expression clear");
        return EExpressionApplyResult::Ignored;
    }
    Record.Generation = Generation;
    Record.bCleared = true;
    if (Plan.PresentationId == Id) { bHasPlan = false; ++Revision; }
    Detail.Reset();
    return EExpressionApplyResult::Applied;
}

void FExpressionPlanRuntime::SetState(const FString& State)
{
    if (CurrentState != State) { CurrentState = State; ++Revision; }
}

void FExpressionPlanRuntime::PlaybackStarted(const FString& Id, double Now)
{
    if (!IsUuid(Id) || !FMath::IsFinite(Now) || (bPlaying && PlaybackId == Id)) return;
    EndedUtterances.Remove(Id);
    PlaybackId = Id;
    PlaybackAnchor = Now;
    bPlaying = true;
    ++Revision;
}

void FExpressionPlanRuntime::PlaybackStopped(const FString& Id)
{
    if (!IsUuid(Id)) return;
    if (!EndedUtterances.Contains(Id))
    {
        if (EndedUtterances.Num() >= 64) EndedUtterances.RemoveAt(0);
        EndedUtterances.Add(Id);
    }
    if (PlaybackId == Id) bPlaying = false;
    if (Plan.UtteranceId == Id) { Plan.UtteranceId.Reset(); ++Revision; }
}

FExpressionSnapshot FExpressionPlanRuntime::Snapshot(double Now) const
{
    FExpressionSnapshot Result;
    Result.Plan = Plan;
    Result.State = CurrentState;
    Result.Revision = Revision;
    Result.bHasPlan = bHasPlan;
    Result.bPlaybackMatches = bPlaying && !Plan.UtteranceId.IsEmpty() && Plan.UtteranceId == PlaybackId
        && !EndedUtterances.Contains(Plan.UtteranceId);
    Result.PlaybackStarted = PlaybackAnchor;
    Result.Now = FMath::IsFinite(Now) ? Now : PlaybackAnchor;
    const FRevisionRecord* Record = Presentations.Find(Plan.PresentationId);
    Result.ExpiresAt = Record ? Record->ExpiresAt : 0.0;
    Result.bPrimaryHealthy = bHasPlan && (Plan.Source == TEXT("model") || Plan.Source == TEXT("preview"))
        && FMath::IsFinite(Now) && Now < Result.ExpiresAt;
    return Result;
}

FFaceCurveMap MapExpressionIntent(const FExpressionIntent& Intent)
{
    FFaceCurveMap Result;
    const float Intensity = FMath::IsFinite(Intent.Intensity) ? FMath::Clamp(Intent.Intensity, 0.0f, 0.35f) : 0.0f;
    float Inner = 0.0f, Outer = 0.0f, Down = 0.0f;
    switch (Intent.Expression)
    {
    case EExpressionIntent::Attentive: Inner = 0.35f; Outer = 0.25f; break;
    case EExpressionIntent::Thoughtful: Inner = 0.1f; Down = 0.25f; break;
    case EExpressionIntent::Friendly: Inner = 0.15f; Outer = 0.3f; break;
    case EExpressionIntent::Emphasis: Inner = 0.55f; Outer = 0.45f; break;
    default: break;
    }
    Result.Add(TEXT("CTRL_expressions_browRaiseInL"), Inner * Intensity);
    Result.Add(TEXT("CTRL_expressions_browRaiseInR"), Inner * Intensity);
    Result.Add(TEXT("CTRL_expressions_browRaiseOuterL"), Outer * Intensity);
    Result.Add(TEXT("CTRL_expressions_browRaiseOuterR"), Outer * Intensity);
    Result.Add(TEXT("CTRL_expressions_browDownL"), Down * Intensity);
    Result.Add(TEXT("CTRL_expressions_browDownR"), Down * Intensity);
    return Result;
}

const FExpressionProfile& ExpressionProfile(const FExpressionSnapshot& Snapshot)
{
    if (Snapshot.State == TEXT("speaking")) return Snapshot.Plan.Speaking;
    if (Snapshot.State == TEXT("thinking")) return Snapshot.Plan.Thinking;
    if (Snapshot.State == TEXT("listening") || Snapshot.State == TEXT("interrupted")) return Snapshot.Plan.Listening;
    return Snapshot.Plan.Idle;
}

float FExpressionLocalMotion::Interval(int32 MinimumMs, int32 MaximumMs, float Variation)
{
    const float Fraction = FMath::Lerp(0.5f, Random.FRand(), FMath::Clamp(Variation, 0.0f, 1.0f));
    return FMath::Lerp(static_cast<float>(MinimumMs), static_cast<float>(MaximumMs), Fraction) / 1000.0f;
}

FFaceCurveMap FExpressionLocalMotion::Generate(const FExpressionSnapshot& Snapshot, bool bSpeaking,
    const FPresentationMotion* CoordinatedMotion)
{
    const FExpressionProfile& P = ExpressionProfile(Snapshot);
    const double Now = Snapshot.Now;
    if (!bInitialized)
    {
        Random.Initialize(static_cast<int32>(GetTypeHash(Snapshot.Plan.PresentationId)));
        NextBlink = Now + Interval(P.BlinkMinMs, P.BlinkMaxMs, P.Variation);
        NextGaze = Now + Interval(P.GazeHoldMinMs, P.GazeHoldMaxMs, P.Variation);
        NextMotion = Now + Interval(P.MotionMinMs, P.MotionMaxMs, P.Variation);
        GazeStarted = Now;
        bInitialized = true;
    }
    if (BlinkStarted < 0.0 && Now >= NextBlink)
    {
        BlinkStarted = Now;
        BlinkDuration = P.BlinkDurationMs / 1000.0;
    }
    float Blink = 0.0f;
    if (BlinkStarted >= 0.0)
    {
        const double Phase = (Now - BlinkStarted) / BlinkDuration;
        Blink = Phase < 0.35 ? Smooth(Phase / 0.35) : 1.0f - Smooth((Phase - 0.35) / 0.65);
        if (Phase >= 1.0)
        {
            BlinkStarted = -1.0;
            NextBlink = Now + Interval(P.BlinkMinMs, P.BlinkMaxMs, P.Variation);
        }
    }
    if (Now >= NextGaze)
    {
        GazeFrom = GazeDisplayed;
        GazeStarted = Now;
        const bool bEyeContact = Random.FRand() < P.EyeContact;
        GazeTarget = bEyeContact ? FVector2D::ZeroVector
            : FVector2D(Random.FRandRange(-P.GazeAmplitude, P.GazeAmplitude),
                Random.FRandRange(-P.GazeAmplitude * 0.6f, P.GazeAmplitude * 0.6f));
        NextGaze = Now + Interval(P.GazeHoldMinMs, P.GazeHoldMaxMs, P.Variation);
    }
    GazeDisplayed = FMath::Lerp(GazeFrom, GazeTarget, Smooth((Now - GazeStarted) / 0.35));
    // A state change can reduce gaze amplitude before the next scheduled shift.
    GazeDisplayed.X = FMath::Clamp(GazeDisplayed.X, -static_cast<double>(P.GazeAmplitude), static_cast<double>(P.GazeAmplitude));
    GazeDisplayed.Y = FMath::Clamp(GazeDisplayed.Y, -static_cast<double>(P.GazeAmplitude), static_cast<double>(P.GazeAmplitude));
    if (Now >= NextMotion)
    {
        MotionStarted = Now;
        MotionDuration = Random.FRandRange(1.2f, 2.0f);
        MotionHeight = Random.FRandRange(-1.0f, 1.0f);
        NextMotion = Now + Interval(P.MotionMinMs, P.MotionMaxMs, P.Variation);
    }
    float Pulse = 0.0f;
    if (MotionStarted >= 0.0)
    {
        const double Phase = (Now - MotionStarted) / MotionDuration;
        if (Phase < 1.0) Pulse = Smooth(Phase / 0.3) * (1.0f - Smooth((Phase - 0.6) / 0.4)) * MotionHeight;
    }
    FExpressionIntent Intent = P;
    Intent.Intensity = FMath::Clamp(P.Intensity * (1.0f + 0.2f * P.Variation * Pulse), 0.0f, 0.35f);
    FFaceCurveMap Result = MapExpressionIntent(Intent);
    if (P.Expression == EExpressionIntent::Neutral)
    {
        const float SmallBrowMotion = 0.012f * P.Variation * FMath::Max(0.0f, Pulse);
        Result[TEXT("CTRL_expressions_browRaiseInL")] = SmallBrowMotion;
        Result[TEXT("CTRL_expressions_browRaiseInR")] = SmallBrowMotion;
    }
    // These exact curve names exist in the supplied MetaHuman FaceAnim clips.
    Result.Add(TEXT("CTRL_expressions_eyeBlinkL"), Blink);
    Result.Add(TEXT("CTRL_expressions_eyeBlinkR"), Blink);
    const FVector2D FinalGaze = CoordinatedMotion
        ? FMath::Lerp(GazeDisplayed, CoordinatedMotion->Gaze, FMath::Clamp(CoordinatedMotion->GazeWeight, 0.0f, 1.0f))
        : GazeDisplayed;
    for (const TCHAR* Side : {TEXT("L"), TEXT("R")})
    {
        Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_eyeLookLeft%s"), Side)), FMath::Max(0.0f, static_cast<float>(-FinalGaze.X)));
        Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_eyeLookRight%s"), Side)), FMath::Max(0.0f, static_cast<float>(FinalGaze.X)));
        Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_eyeLookDown%s"), Side)), FMath::Max(0.0f, static_cast<float>(-FinalGaze.Y)));
        Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_eyeLookUp%s"), Side)), FMath::Max(0.0f, static_cast<float>(FinalGaze.Y)));
        if (bSpeaking && CoordinatedMotion)
        {
            const FName Brow(*FString::Printf(TEXT("CTRL_expressions_browRaiseIn%s"), Side));
            Result[Brow] = FMath::Clamp(Result.FindRef(Brow) + CoordinatedMotion->BrowEmphasis, 0.0f, 0.35f);
        }
        Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_eyeCheekRaise%s"), Side)), P.Warmth * 0.2f);
        // Quiet warmth uses verified corner-pull controls. No generated mouth
        // control is allowed into speaking; speech lower-face ownership stays native.
        if (!bSpeaking) Result.Add(FName(*FString::Printf(TEXT("CTRL_expressions_mouthCornerPull%s"), Side)), P.Warmth * 0.4f);
    }
    return Result;
}

const FFaceCurveMap& FExpressionPrimaryFace::Step(const FExpressionSnapshot& Snapshot,
    const FFaceCurveMap& Recorded, bool bSpeaking, float DeltaSeconds, const FPresentationMotion* CoordinatedMotion)
{
    for (const auto& Curve : Recorded) if (IsExpressionCurve(Curve.Key)) KnownNames.Add(Curve.Key);
    const FFaceCurveMap Generated = Snapshot.bPrimaryHealthy ? Motion.Generate(Snapshot, bSpeaking, CoordinatedMotion) : FFaceCurveMap();
    for (const auto& Curve : Generated) KnownNames.Add(Curve.Key);
    FFaceCurveMap Target;
    for (FName Name : KnownNames)
    {
        // A healthy plan wholly replaces recorded CTRL_expressions, including
        // ungenerated lower-face curves. The copied body pose is never modified.
        Target.Add(Name, Snapshot.bPrimaryHealthy ? Generated.FindRef(Name) : Recorded.FindRef(Name));
    }
    if (!bInitialized)
    {
        Displayed = Recorded;
        bInitialized = true;
    }
    if (Revision != Snapshot.Revision || bLastHealthy != Snapshot.bPrimaryHealthy)
    {
        From = Displayed;
        OwnershipFrom = OwnershipAlpha;
        Revision = Snapshot.Revision;
        bLastHealthy = Snapshot.bPrimaryHealthy;
        Elapsed = 0.0f;
        bBlending = true;
    }
    else if (bBlending) Elapsed += FMath::IsFinite(DeltaSeconds) ? FMath::Max(0.0f, DeltaSeconds) : 0.0f;
    const float Alpha = bBlending ? Smooth(Elapsed / 0.35) : 1.0f;
    OwnershipAlpha = FMath::Lerp(OwnershipFrom, Snapshot.bPrimaryHealthy ? 1.0f : 0.0f, Alpha);
    Displayed.Reset();
    for (const auto& Curve : Target)
        Displayed.Add(Curve.Key, FMath::Lerp(From.FindRef(Curve.Key), Curve.Value, Alpha));
    if (Elapsed >= 0.35f) { bBlending = false; From.Reset(); }
    return Displayed;
}
}
