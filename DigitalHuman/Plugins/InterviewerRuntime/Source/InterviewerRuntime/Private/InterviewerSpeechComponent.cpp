#include "InterviewerSpeechComponent.h"

#include "InterviewerSpeechTimeline.h"
#include "Components/AudioComponent.h"
#include "Sound/SoundWaveProcedural.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "ISpeechAnimationSolver.h"
#include "SpeechAnimationSolverV4.h"
#include "GuiToRawControlsUtils.h"
#include "NNE.h"
#include "NNEModelData.h"
#include "NNERuntimeCPU.h"
#include "Async/Async.h"
#include "Misc/ScopeLock.h"
#include "Modules/ModuleManager.h"
#include "UObject/StrongObjectPtr.h"

/** Only the model is reused. PCM, recurrent state and curves belong to one utterance. */
class FInterviewerSpeechSolverState
{
public:
    explicit FInterviewerSpeechSolverState(UNNEModelData* InModel) : Model(InModel) {}

    bool Solve(const TArray<int16>& Samples, const UE::Interviewer::FSpeechPreparationFence& Fence,
        UE::Interviewer::FSpeechTimeline& Timeline, FString& Error)
    {
        FScopeLock Lock(&Mutex);
        if (Fence.bCancelled.Load()) return false;
        if (!Solver)
        {
            Solver = MakeUnique<FSpeechAnimationSolverV4>(TObjectPtr<UNNEModelData>(Model.Get()), TEXT("NNERuntimeORTCpu"));
            if (!Solver->Initialize())
            { Solver.Reset(); Error = TEXT("Native CPU speech model could not initialize"); return false; }
        }
        Solver->ClearCache();
        Timeline.DurationSeconds = static_cast<double>(Samples.Num()) / 24000.0;
        constexpr int32 SamplesPerStep = 480;
        constexpr int32 LookaheadSteps = 4;
        const int32 Steps = (Samples.Num() + SamplesPerStep - 1) / SamplesPerStep + LookaheadSteps;
        FSpeechAnimationAudioFrame Input;
        Input.SampleRate = 24000;
        Input.NumChannels = 1;
        Input.SamplesCount = SamplesPerStep;
        Input.bContiguous = true;
        Input.Mood = EAudioDrivenAnimationMood::Neutral;
        Input.MoodIntensity = 0.25f;
        Input.Lookahead = 80;
        Input.AudioSamples.SetNumUninitialized(SamplesPerStep);
        for (int32 Step = 0; Step < Steps; ++Step)
        {
            if (Fence.bCancelled.Load()) return false;
            const int32 Offset = Step * SamplesPerStep;
            for (int32 Index = 0; Index < SamplesPerStep; ++Index)
                Input.AudioSamples[Index] = Offset + Index < Samples.Num()
                    ? static_cast<float>(Samples[Offset + Index]) / 32768.0f : 0.0f;
            const double InputEndSeconds = (Step + 1) * UE::Interviewer::SpeechSolverStepSeconds;
            Input.ArrivalTime = InputEndSeconds;
            FSpeechAnimationFrameData Output;
            if (!Solver->SolveAudioFrame(Input, Output) || Output.CurveNames.Num() != Output.CurveValues.Num())
            { Error = TEXT("Native CPU speech solver failed to generate a frame"); return false; }
            TMap<FString, float> Gui;
            for (int32 Index = 0; Index < Output.CurveNames.Num(); ++Index)
            {
                const float Value = Output.CurveValues[Index];
                if (!FMath::IsFinite(Value)) { Error = TEXT("Native speech solver returned a nonfinite curve"); return false; }
                Gui.Add(Output.CurveNames[Index].ToString(), Value);
            }
            const TMap<FString, float> Raw = GuiToRawControlsUtils::ConvertGuiToRawControls(Gui);
            if (Raw.Num() != UE::Interviewer::MaxSpeechCurves)
            { Error = TEXT("Native speech solver returned an unexpected facial curve layout"); return false; }
            if (Timeline.Names.IsEmpty())
            {
                for (const auto& Pair : Raw) Timeline.Names.Add(FName(*Pair.Key));
                Timeline.Names.Sort([](FName A, FName B) { return A.LexicalLess(B); });
            }
            TArray<float> Values;
            Values.Reserve(Timeline.Names.Num());
            for (FName Name : Timeline.Names)
            {
                const float* Value = Raw.Find(Name.ToString());
                if (!Value) { Error = TEXT("Native speech curve layout changed during preparation"); return false; }
                Values.Add(*Value);
            }
            if (!Timeline.AppendFrame(InputEndSeconds, Values))
            { Error = TEXT("Native speech timeline exceeded its validated bounds"); return false; }
        }
        if (Timeline.Frames.IsEmpty() || Timeline.Frames.Last().Seconds + 1.e-7 < Timeline.DurationSeconds)
        { Error = TEXT("Native speech timeline did not cover the complete audio clip"); return false; }
        return !Fence.bCancelled.Load();
    }

private:
    // Created on the game thread and retained until the last canceled worker exits.
    TStrongObjectPtr<UNNEModelData> Model;
    FCriticalSection Mutex;
    TUniquePtr<FSpeechAnimationSolverV4> Solver;
};

UInterviewerSpeechComponent::UInterviewerSpeechComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
}

void UInterviewerSpeechComponent::BeginPlay()
{
    Super::BeginPlay();
    Audio = NewObject<UAudioComponent>(GetOwner());
    Audio->bAutoActivate = false;
    Audio->bIsUISound = true;
    Audio->bAllowSpatialization = false;
    Audio->RegisterComponent();
    // NNE model-data caches and runtime registration are not synchronized.
    // Resolve them before the worker creates its CPU session.
    if (!FModuleManager::Get().LoadModulePtr<IModuleInterface>(TEXT("NNERuntimeORT"))) return;
    if (UClass* SettingsClass = FindObject<UClass>(nullptr, TEXT("/Script/NNERuntimeORT.NNERuntimeORTSettings")))
        SettingsClass->GetDefaultObject();
    auto* ModelData = LoadObject<UNNEModelData>(nullptr, *ISpeechAnimationSolver::GetLatestModelAssetPath());
    SolverModel = ModelData;
    const auto Runtime = UE::NNE::GetRuntime<INNERuntimeCPU>(TEXT("NNERuntimeORTCpu"));
    if (ModelData && Runtime.IsValid() && ModelData->GetModelData(TEXT("NNERuntimeORTCpu")).IsValid())
        SolverState = MakeShared<FInterviewerSpeechSolverState, ESPMode::ThreadSafe>(ModelData);
}

void UInterviewerSpeechComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    StopSpeaking();
    SolverState.Reset();
    if (Audio) Audio->DestroyComponent();
    Super::EndPlay(Reason);
}

void UInterviewerSpeechComponent::Speak(const FString& AudioUrl, const FString& UtteranceId)
{
    StopSpeaking();
    CurrentId = UtteranceId;
    LastError.Empty();
    LastGeneratedFrameCount = 0;
    LastSpeechPreparationMs = LastSpeechSolveMs = LastSpeechDurationSeconds = 0.0f;
    LastSpeechSampleCount = 0;
    LastSpeechSampleFrame = LastSpeechAudioSeconds = LastSpeechCurveSeconds = LastSpeechMaxFrameGapSeconds = 0.0f;
    LastPlaybackEvent = TEXT("preparing");
    PreparationStarted = FPlatformTime::Seconds();
    const bool bLocal = AudioUrl.StartsWith("http://127.0.0.1:8765/api/speech/audio/")
        || AudioUrl.StartsWith("http://localhost:8765/api/speech/audio/");
    FGuid Capability;
    if (!bLocal || !FGuid::Parse(CurrentId, Capability) || !AudioUrl.EndsWith("/" + CurrentId + "/"))
    { Fail(TEXT("Invalid local speech URL or utterance ID")); return; }
    if (!SolverState || !SolverModel)
    { Fail(TEXT("Native CPU speech model is unavailable; check StreamingADA cooked CPU model data")); return; }
    const uint32 ExpectedGeneration = Generation;
    Request = FHttpModule::Get().CreateRequest();
    Request->SetURL(AudioUrl);
    Request->SetVerb(TEXT("GET"));
    Request->SetTimeout(15.0f);
    TWeakObjectPtr<UInterviewerSpeechComponent> WeakThis(this);
    Request->OnProcessRequestComplete().BindLambda(
        [WeakThis, ExpectedGeneration](FHttpRequestPtr, FHttpResponsePtr Response, bool bOK)
        {
            AsyncTask(ENamedThreads::GameThread, [WeakThis, ExpectedGeneration, Response, bOK]()
            {
                if (!WeakThis.IsValid() || WeakThis->Generation != ExpectedGeneration) return;
                auto* Self = WeakThis.Get();
                Self->Request.Reset();
                if (!bOK || !Response.IsValid() || Response->GetResponseCode() != 200
                    || !Self->DecodeWave(Response->GetContent()))
                { Self->Fail(TEXT("Unable to download valid 24 kHz mono PCM WAV")); return; }
                Self->PrepareSpeech();
            });
        });
    if (!Request->ProcessRequest()) Fail(TEXT("Audio download could not start"));
}

bool UInterviewerSpeechComponent::DecodeWave(const TArray<uint8>& Bytes)
{
    if (Bytes.Num() < 44 || Bytes.Num() > 5760044
        || FMemory::Memcmp(Bytes.GetData(), "RIFF", 4) != 0
        || FMemory::Memcmp(Bytes.GetData() + 8, "WAVE", 4) != 0) return false;
    auto Read16 = [&Bytes](int32 Offset) -> uint16 { return Bytes[Offset] | (uint16(Bytes[Offset + 1]) << 8); };
    auto Read32 = [&Bytes](int32 Offset) -> uint32 {
        return uint32(Bytes[Offset]) | (uint32(Bytes[Offset + 1]) << 8)
            | (uint32(Bytes[Offset + 2]) << 16) | (uint32(Bytes[Offset + 3]) << 24);
    };
    bool bFormatValid = false;
    int32 DataOffset = -1, DataSize = 0;
    for (int64 Offset = 12; Offset + 8 <= Bytes.Num();)
    {
        const uint32 Size = Read32(static_cast<int32>(Offset + 4));
        const int64 Payload = Offset + 8;
        if (Payload + Size > Bytes.Num()) return false;
        if (FMemory::Memcmp(Bytes.GetData() + Offset, "fmt ", 4) == 0 && Size >= 16)
        {
            const int32 P = static_cast<int32>(Payload);
            bFormatValid = Read16(P) == 1 && Read16(P + 2) == 1
                && Read32(P + 4) == 24000 && Read16(P + 12) == 2 && Read16(P + 14) == 16;
        }
        if (FMemory::Memcmp(Bytes.GetData() + Offset, "data", 4) == 0)
        { DataOffset = static_cast<int32>(Payload); DataSize = static_cast<int32>(Size); }
        Offset = Payload + Size + (Size & 1);
    }
    if (!bFormatValid || DataOffset < 0 || DataSize <= 0 || DataSize % 2) return false;
    Samples.SetNumUninitialized(DataSize / 2);
    FMemory::Memcpy(Samples.GetData(), Bytes.GetData() + DataOffset, DataSize);
    return true;
}

void UInterviewerSpeechComponent::PrepareSpeech()
{
    check(IsInGameThread());
    bPreparing = true;
    PreparationJob = MakeShared<UE::Interviewer::FSpeechPreparationFence, ESPMode::ThreadSafe>(Generation);
    const auto Job = PreparationJob;
    const auto State = SolverState;
    TArray<int16> WorkSamples = Samples;
    TWeakObjectPtr<UInterviewerSpeechComponent> WeakThis(this);
    Async(EAsyncExecution::ThreadPool, [WeakThis, State, Job, WorkSamples = MoveTemp(WorkSamples)]()
    {
        const double SolveStarted = FPlatformTime::Seconds();
        auto Result = MakeShared<UE::Interviewer::FSpeechTimeline, ESPMode::ThreadSafe>();
        FString Error;
        const bool bOK = State->Solve(WorkSamples, *Job, *Result, Error);
        const float SolveMs = static_cast<float>((FPlatformTime::Seconds() - SolveStarted) * 1000.0);
        if (Job->bCancelled.Load()) return;
        AsyncTask(ENamedThreads::GameThread, [WeakThis, Job, Result, Error, bOK, SolveMs]()
        {
            if (!WeakThis.IsValid() || !Job->Accepts(WeakThis->Generation)) return;
            auto* Self = WeakThis.Get();
            Self->PreparationJob.Reset();
            Self->bPreparing = false;
            Self->LastSpeechSolveMs = SolveMs;
            if (!bOK) { Self->Fail(Error.IsEmpty() ? TEXT("Native speech preparation failed") : Error); return; }
            Self->SpeechTimeline = Result;
            Self->LastGeneratedFrameCount = Result->Frames.Num();
            Self->LastSpeechDurationSeconds = static_cast<float>(Result->DurationSeconds);
            Self->LastSpeechMaxFrameGapSeconds = static_cast<float>(Result->GetMaxFrameGapSeconds());
            Self->Wave = NewObject<USoundWaveProcedural>(Self);
            Self->Wave->SetSampleRate(Self->SampleRate);
            Self->Wave->NumChannels = 1;
            Self->Wave->Duration = Self->LastSpeechDurationSeconds;
            Self->Wave->QueueAudio(reinterpret_cast<const uint8*>(Self->Samples.GetData()), Self->Samples.Num() * sizeof(int16));
            Self->Audio->SetSound(Self->Wave);
            // No inference remains during playback. Sound, lips and rhythm share one clock.
            Self->Audio->Play();
            Self->AudiblePlaybackEpoch = FPlatformTime::Seconds();
            Self->bPlaying = true;
            Self->LastSpeechPreparationMs = static_cast<float>((Self->AudiblePlaybackEpoch - Self->PreparationStarted) * 1000.0);
            Self->LastPlaybackEvent = TEXT("playback_started");
            UE_LOG(LogTemp, Display, TEXT("Interviewer speech prepared: utterance=%s preparation_ms=%.1f solve_ms=%.1f duration=%.3f frames=%d max_curve_gap=%.4f"),
                *Self->CurrentId, Self->LastSpeechPreparationMs, SolveMs, Self->LastSpeechDurationSeconds,
                Self->LastGeneratedFrameCount, Self->LastSpeechMaxFrameGapSeconds);
            Self->OnPlaybackEvent.Broadcast(TEXT("playback_started"), Self->CurrentId);
        });
    });
}

void UInterviewerSpeechComponent::TickComponent(float Delta, ELevelTick TickType, FActorComponentTickFunction* Tick)
{
    Super::TickComponent(Delta, TickType, Tick);
    const double Now = FPlatformTime::Seconds();
    if (bPreparing && Now - PreparationStarted > 120.0)
    { Fail(TEXT("Native speech preparation timed out")); return; }
    if (!bPlaying || !SpeechTimeline) return;
    const double Elapsed = FMath::Max(0.0, Now - AudiblePlaybackEpoch);
    LastSpeechAudioSeconds = static_cast<float>(FMath::Min(Elapsed, SpeechTimeline->DurationSeconds));
    TMap<FName, float> Curves;
    double CurveSeconds = 0;
    if (SpeechTimeline->Sample(Elapsed, Curves, &LastSpeechSampleFrame, &CurveSeconds))
    {
        ++LastSpeechSampleCount;
        LastSpeechCurveSeconds = static_cast<float>(CurveSeconds);
    }
    if (Elapsed >= SpeechTimeline->DurationSeconds) FinishPlayback();
}

bool UInterviewerSpeechComponent::GetSpeechCurves(TMap<FName, float>& Out, double Now) const
{
    check(IsInGameThread());
    Out.Reset();
    return bPlaying && SpeechTimeline && SpeechTimeline->Sample(FMath::Max(0.0, Now - AudiblePlaybackEpoch), Out);
}

void UInterviewerSpeechComponent::StopSpeaking()
{
    ++Generation;
    const FString StoppedId = CurrentId;
    if (Request) { Request->CancelRequest(); Request.Reset(); }
    if (PreparationJob) { PreparationJob->Cancel(); PreparationJob.Reset(); }
    if (Audio) Audio->Stop();
    bPreparing = bPlaying = false;
    AudiblePlaybackEpoch = 0;
    SpeechTimeline.Reset();
    Wave = nullptr;
    Samples.Empty();
    CurrentId.Empty();
    // Preserve final diagnostics until a new utterance starts.
    if (!StoppedId.IsEmpty())
    {
        LastPlaybackEvent = TEXT("interrupted");
        OnPlaybackEvent.Broadcast(TEXT("interrupted"), StoppedId);
    }
}

void UInterviewerSpeechComponent::FinishPlayback()
{
    const FString CompletedId = CurrentId;
    CurrentId.Empty();
    StopSpeaking();
    LastPlaybackEvent = TEXT("playback_finished");
    UE_LOG(LogTemp, Display, TEXT("Interviewer speech completed: utterance=%s sampled_updates=%d audio_seconds=%.3f curve_seconds=%.3f last_frame=%.2f"),
        *CompletedId, LastSpeechSampleCount, LastSpeechAudioSeconds, LastSpeechCurveSeconds, LastSpeechSampleFrame);
    OnPlaybackEvent.Broadcast(TEXT("playback_finished"), CompletedId);
}

void UInterviewerSpeechComponent::Fail(const FString& Reason)
{
    LastError = Reason;
    const FString FailedId = CurrentId;
    CurrentId.Empty();
    StopSpeaking();
    LastPlaybackEvent = TEXT("playback_failed");
    OnPlaybackEvent.Broadcast(TEXT("playback_failed"), FailedId);
}

UE::Interviewer::FSpeechRhythm UInterviewerSpeechComponent::GetSpeechRhythm(double Now) const
{
    check(IsInGameThread());
    UE::Interviewer::FSpeechRhythm Rhythm;
    Rhythm.PlaybackGeneration = Generation;
    Rhythm.bPlaying = bPlaying;
    if (bPlaying)
    {
        Rhythm.AudibleSeconds = FMath::Max(0.0, Now - AudiblePlaybackEpoch);
        Rhythm.Rms = UE::Interviewer::PcmWindowRms(Samples, SampleRate, Rhythm.AudibleSeconds);
    }
    return Rhythm;
}
