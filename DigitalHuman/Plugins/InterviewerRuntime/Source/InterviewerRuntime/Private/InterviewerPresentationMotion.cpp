#include "InterviewerPresentationMotion.h"
#include "InterviewerExpressionPlanHelpers.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/Guid.h"

namespace UE::Interviewer
{
namespace
{
bool Uuid(const FString& Value)
{
    FGuid Id;
    return FGuid::ParseExact(Value, EGuidFormats::DigitsWithHyphens, Id) && Id.IsValid();
}
float Smooth(float X) { X = FMath::Clamp(X, 0.0f, 1.0f); return X * X * (3 - 2 * X); }
float Envelope(double Time, double Duration)
{
    const float Phase = static_cast<float>(Time / Duration);
    return Phase < 0.4f ? Smooth(Phase / 0.4f) : 1 - Smooth((Phase - 0.4f) / 0.6f);
}
}

bool ParseListeningActivity(const TSharedPtr<FJsonObject>& Message, FListeningActivity& Out, FString& Detail)
{
    Detail = TEXT("Invalid listening activity fields or types");
    if (!Message.IsValid() || Message->Values.Num() != 7) return false;
    FListeningActivity Parsed;
    const auto String = [&Message](const TCHAR* Name, FString& Value)
    {
        const auto Field = Message->TryGetField(Name);
        return Field.IsValid() && Field->Type == EJson::String && Field->TryGetString(Value);
    };
    const auto Integer = [&Message](const TCHAR* Name, int32& Value)
    {
        const auto Field = Message->TryGetField(Name);
        if (!Field.IsValid() || Field->Type != EJson::Number) return false;
        const double Number = Field->AsNumber();
        if (!FMath::IsFinite(Number) || Number < 1 || Number > MAX_int32 || FMath::FloorToDouble(Number) != Number) return false;
        Value = static_cast<int32>(Number);
        return true;
    };
    FString Type;
    const auto Active = Message->TryGetField(TEXT("active"));
    const auto Ended = Message->TryGetField(TEXT("ended"));
    if (!String(TEXT("type"), Type) || Type != TEXT("listening_activity")
        || !String(TEXT("presentation_id"), Parsed.PresentationId) || !Uuid(Parsed.PresentationId)
        || !String(TEXT("capture_id"), Parsed.CaptureId) || !Uuid(Parsed.CaptureId)
        || !Integer(TEXT("capture_generation"), Parsed.CaptureGeneration) || !Integer(TEXT("sequence"), Parsed.Sequence)
        || !Active.IsValid() || Active->Type != EJson::Boolean || !Ended.IsValid() || Ended->Type != EJson::Boolean) return false;
    Parsed.bActive = Active->AsBool();
    Parsed.bEnded = Ended->AsBool();
    if (Parsed.bActive && Parsed.bEnded) return false;
    Out = MoveTemp(Parsed);
    Detail.Reset();
    return true;
}

EExpressionApplyResult FListeningActivityRuntime::Apply(const FListeningActivity& A,
    const FExpressionSnapshot& Plan, FString& Detail)
{
    if (!Plan.bPrimaryHealthy || A.PresentationId != Plan.Plan.PresentationId || !Uuid(A.PresentationId)
        || !Uuid(A.CaptureId) || A.CaptureGeneration < 1 || A.Sequence < 1 || !FMath::IsFinite(Plan.Now)
        || (A.bEnded && A.bActive))
    {
        Detail = TEXT("Listening activity requires the current healthy presentation");
        return EExpressionApplyResult::Rejected;
    }
    if (!Captures.Contains(A.PresentationId))
    {
        if (Order.Num() >= 64) { Captures.Remove(Order[0]); Order.RemoveAt(0); }
        Order.Add(A.PresentationId);
        Captures.Add(A.PresentationId, FCapture());
    }
    FCapture& Capture = Captures.FindChecked(A.PresentationId);
    if (A.CaptureGeneration < Capture.Generation)
    {
        Detail = TEXT("Ignored stale listening capture generation");
        return EExpressionApplyResult::Ignored;
    }
    if (A.CaptureGeneration == Capture.Generation
        && (A.CaptureId != Capture.Id || Capture.bEnded || A.Sequence <= Capture.Sequence))
    {
        Detail = TEXT("Listening capture identity, sequence or retirement fence rejected");
        return EExpressionApplyResult::Rejected;
    }
    if (A.CaptureGeneration > Capture.Generation)
    {
        const uint64 Pause = Capture.PauseSerial;
        Capture = FCapture();
        Capture.PauseSerial = Pause;
        Capture.Id = A.CaptureId;
        Capture.Generation = A.CaptureGeneration;
    }
    // An expired voiced heartbeat cannot turn a later quiet packet into a nod.
    if (Plan.Now - Capture.ReceivedAt > 1.8) Capture.bObservedVoice = false;
    if (A.bEnded) Capture.bObservedVoice = false;
    else if (A.bActive)
    {
        if (!Capture.bActive || !Capture.bObservedVoice) Capture.VoicedAt = Plan.Now;
        Capture.bObservedVoice = true;
        Capture.LastVoicedAt = Plan.Now;
    }
    else if (Capture.bActive && Capture.bObservedVoice && Capture.LastVoicedAt - Capture.VoicedAt >= 0.5)
    {
        ++Capture.PauseSerial;
        Capture.bObservedVoice = false;
    }
    Capture.Sequence = A.Sequence;
    Capture.ReceivedAt = Plan.Now;
    Capture.bActive = A.bActive;
    Capture.bEnded = A.bEnded;
    Detail.Reset();
    return EExpressionApplyResult::Applied;
}

FListeningSnapshot FListeningActivityRuntime::Snapshot(const FExpressionSnapshot& Plan) const
{
    FListeningSnapshot Result;
    const FCapture* Capture = Captures.Find(Plan.Plan.PresentationId);
    if (Capture)
    {
        Result.PauseSerial = Capture->PauseSerial;
        Result.CaptureGeneration = Capture->Generation;
        Result.bFresh = Plan.bPrimaryHealthy && !Capture->bEnded && Plan.Now >= Capture->ReceivedAt
            && Plan.Now - Capture->ReceivedAt <= 1.8;
        Result.bActive = Result.bFresh && Capture->bActive;
    }
    return Result;
}

float PcmWindowRms(TConstArrayView<int16> Samples, int32 SampleRate, double AudibleSeconds)
{
    if (SampleRate <= 0 || !FMath::IsFinite(AudibleSeconds) || AudibleSeconds < 0 || Samples.IsEmpty()) return 0;
    const int32 End = static_cast<int32>(FMath::Clamp(AudibleSeconds * SampleRate, 0.0, static_cast<double>(Samples.Num())));
    const int32 Start = FMath::Max(0, End - FMath::Max(1, SampleRate / 20));
    if (End <= Start) return 0;
    double Sum = 0;
    for (int32 Index = Start; Index < End; ++Index)
    {
        const double Value = Samples[Index] / 32768.0;
        Sum += Value * Value;
    }
    return static_cast<float>(FMath::Sqrt(Sum / (End - Start)));
}

FPresentationMotion FPresentationMotionRuntime::Step(const FExpressionSnapshot& Plan,
    const FListeningSnapshot& Listening, const FSpeechRhythm& Rhythm, float DeltaSeconds)
{
    const auto& Profile = ExpressionProfile(Plan);
    const double Now = Plan.Now;
    if (!bInitialized)
    {
        Random.Initialize(static_cast<int32>(GetTypeHash(Plan.Plan.PresentationId) ^ 0x528ea21u));
        NextMotion = Now + Profile.MotionMinMs / 1000.0;
        bInitialized = true;
    }
    if (State != Plan.State || Presentation != Plan.Plan.PresentationId)
    {
        State = Plan.State;
        Presentation = Plan.Plan.PresentationId;
        Started = -1;
        SeenPause = Listening.PauseSerial;
        NextMotion = State == TEXT("speaking") ? Now + 0.25
            : FMath::Max(NextMotion, Now + Profile.MotionMinMs / 1000.0);
    }
    FPresentationMotion Target;
    const bool bListening = State == TEXT("listening") || State == TEXT("interrupted");
    const bool bSpeaking = State == TEXT("speaking");
    const bool bEnabled = Plan.bPrimaryHealthy && Profile.HeadMotionStrength > 0;
    if (!Plan.bPrimaryHealthy || (bListening && (!Listening.bFresh || Listening.CaptureGeneration != SeenCaptureGeneration)))
        Started = -1;
    SeenCaptureGeneration = Listening.CaptureGeneration;
    const auto Start = [&](bool bThinking, double Seconds, float Amplitude)
    {
        Started = Now;
        Duration = Seconds;
        Height = Amplitude;
        bThinkingGesture = bThinking;
        NextMotion = Now + Random.FRandRange(Profile.MotionMinMs / 1000.0f, Profile.MotionMaxMs / 1000.0f);
    };
    if (Listening.PauseSerial != SeenPause)
    {
        SeenPause = Listening.PauseSerial;
        if (bEnabled && bListening && Listening.bFresh && !Listening.bActive && Now >= NextMotion
            && Random.FRand() < Profile.HeadMotionProbability)
            Start(false, Random.FRandRange(0.5f, 0.8f), 3.0f * Profile.HeadMotionStrength);
    }
    if (bEnabled && State == TEXT("thinking") && Now >= NextMotion
        && (Started < 0 || Now - Started >= Duration))
    {
        NextMotion = Now + Random.FRandRange(Profile.MotionMinMs / 1000.0f, Profile.MotionMaxMs / 1000.0f);
        if (Random.FRand() < Profile.HeadMotionProbability)
            Start(true, Random.FRandRange(1.4f, 2.0f), (Random.FRand() < 0.5f ? -1.0f : 1.0f) * 3.0f * Profile.HeadMotionStrength);
    }
    if (PlaybackGeneration != Rhythm.PlaybackGeneration)
    {
        PlaybackGeneration = Rhythm.PlaybackGeneration;
        QuietSince = Now;
        PreviousRms = 0;
        if (bSpeaking && Rhythm.bPlaying) NextMotion = Now + 0.25;
    }
    if (Rhythm.Rms < 0.018f)
    {
        if (QuietSince < 0) QuietSince = Now;
    }
    else
    {
        const bool bAfterPause = QuietSince >= 0 && Now - QuietSince >= 0.12;
        const bool bRise = Rhythm.Rms > 0.035f && Rhythm.Rms > PreviousRms * 1.35f;
        if (bEnabled && bSpeaking && Rhythm.bPlaying && Profile.AudioEmphasisStrength > 0
            && bAfterPause && bRise && Now >= NextMotion && Now - LastAudioAccent >= 1.0
            && Random.FRand() < Profile.HeadMotionProbability)
        {
            Start(false, 0.65, 2.0f * Profile.HeadMotionStrength * Profile.AudioEmphasisStrength);
            LastAudioAccent = Now;
        }
        QuietSince = -1;
    }
    PreviousRms = Rhythm.Rms;
    if (bEnabled && Started >= 0 && Now - Started <= Duration
        && (!bSpeaking || Rhythm.bPlaying) && (!bListening || Listening.bFresh))
    {
        const float Pulse = Envelope(Now - Started, Duration);
        if (bThinkingGesture)
        {
            Target.Head.Roll = Height * Pulse;
            Target.Head.Yaw = Height * 0.6f * Pulse;
            Target.Gaze = FVector2D(FMath::Sign(Height) * Profile.GazeAmplitude, Profile.GazeAmplitude * 0.25f);
        }
        else Target.Head.Pitch = Height * Pulse;
        Target.GazeWeight = Pulse;
        if (bSpeaking) Target.BrowEmphasis = 0.06f * Profile.AudioEmphasisStrength * Pulse;
    }
    // The same release applies to state changes, expiry, stop and missing activity.
    const float Alpha = 1 - FMath::Exp(-FMath::Max(0.0f, DeltaSeconds) / 0.09f);
    Displayed.Head = FMath::Lerp(Displayed.Head, Target.Head, Alpha);
    Displayed.Gaze = FMath::Lerp(Displayed.Gaze, Target.Gaze, Alpha);
    Displayed.GazeWeight = FMath::Lerp(Displayed.GazeWeight, Target.GazeWeight, Alpha);
    Displayed.BrowEmphasis = FMath::Lerp(Displayed.BrowEmphasis, Target.BrowEmphasis, Alpha);
    return Displayed;
}
}
