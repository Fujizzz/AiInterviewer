#include "InterviewerSpeechComponent.h"

#include "Components/AudioComponent.h"
#include "Sound/SoundWaveProcedural.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "Features/IModularFeatures.h"
#include "ILiveLinkClient.h"
#include "MetaHumanLocalLiveLinkSource.h"
#include "MetaHumanAudioBaseLiveLinkSubject.h"
#include "MetaHumanAudioBaseLiveLinkSubjectSettings.h"
#include "ISpeechAnimationSolver.h"
#include "Async/Async.h"

/** Push externally generated PCM through Epic's public audio-subject extension point. */
class FInterviewerAudioSubject : public FMetaHumanAudioBaseLiveLinkSubject
{
public:
    FInterviewerAudioSubject(ILiveLinkClient* Client, const FGuid& Guid, FName Name,
        UMetaHumanAudioBaseLiveLinkSubjectSettings* Settings)
        : FMetaHumanAudioBaseLiveLinkSubject(Client, Guid, Name, Settings) {}

    // The component supplies audio; do not create a capture-device sampler thread.
    virtual void Start() override { FMetaHumanLocalLiveLinkSubject::Start(); }
    virtual void MediaSamplerMain() override {}
    int32 GetGeneratedFrames() const { return GeneratedFrames.GetValue(); }

    void Push(const int16* Data, int32 Count, int32 Rate)
    {
        FAudioSample Sample;
        Sample.NumChannels = 1;
        Sample.SampleRate = Rate;
        Sample.NumSamples = Count;
        Sample.Data.SetNumUninitialized(Count);
        for (int32 Index = 0; Index < Count; ++Index)
        {
            Sample.Data[Index] = static_cast<float>(Data[Index]) / 32768.0f;
        }
        GetSampleTime(FFrameRate(30, 1), Sample.Time, Sample.TimeSource);
        AddAudioSample(MoveTemp(Sample));
    }
protected:
    virtual void ExtractPipelineData(TSharedPtr<UE::MetaHuman::Pipeline::FPipelineData> Data) override
    {
        FMetaHumanAudioBaseLiveLinkSubject::ExtractPipelineData(Data);
        if (!Animation.AnimationData.IsEmpty()) GeneratedFrames.Increment();
    }
private:
    FThreadSafeCounter GeneratedFrames;
};

/** Recreated per utterance so interrupted work cannot animate the next question. */
class FInterviewerAudioSource : public FMetaHumanLocalLiveLinkSource
{
public:
    explicit FInterviewerAudioSource(FName InName) : Name(InName) {}
    virtual FText GetSourceType() const override { return FText::FromString("Interviewer TTS PCM"); }
    bool IsReady() const { return AudioSubject.IsValid(); }
    int32 GetGeneratedFrames() const { return AudioSubject ? AudioSubject->GetGeneratedFrames() : 0; }
    void Push(const int16* Data, int32 Count, int32 Rate)
    {
        if (AudioSubject.IsValid()) AudioSubject->Push(Data, Count, Rate);
    }
protected:
    virtual void OnSourceCreated(bool bIsPreset) override
    {
        auto* SubjectSettings = CreateSubjectSettings<UMetaHumanAudioBaseLiveLinkSubjectSettings>();
        SubjectSettings->Lookahead = 80;
        SubjectSettings->Mood = EAudioDrivenAnimationMood::Neutral;
        SubjectSettings->MoodIntensity = 0.25f;
        RequestSubjectCreation(Name.ToString(), SubjectSettings);
    }
    virtual TSharedPtr<FMetaHumanLocalLiveLinkSubject> CreateSubject(
        const FName& InName, UMetaHumanLocalLiveLinkSubjectSettings* InSettings) override
    {
        AudioSubject = MakeShared<FInterviewerAudioSubject>(LiveLinkClient, SourceGuid, InName,
            CastChecked<UMetaHumanAudioBaseLiveLinkSubjectSettings>(InSettings));
        return AudioSubject;
    }
private:
    FName Name;
    TSharedPtr<FInterviewerAudioSubject> AudioSubject;
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
    SolverModel = LoadObject<UObject>(nullptr, *ISpeechAnimationSolver::GetLatestModelAssetPath());
}

void UInterviewerSpeechComponent::EndPlay(const EEndPlayReason::Type Reason)
{
    StopSpeaking();
    if (Audio) Audio->DestroyComponent();
    Super::EndPlay(Reason);
}

void UInterviewerSpeechComponent::Speak(const FString& AudioUrl, const FString& UtteranceId)
{
    StopSpeaking();
    CurrentId = UtteranceId;
    LastError.Empty();
    LastGeneratedFrameCount = 0;
    LastPlaybackEvent = "preparing";
    // Local demonstration only; fetch the matching temporary backend capability.
    const bool bLocal = AudioUrl.StartsWith("http://127.0.0.1:8765/api/speech/audio/")
        || AudioUrl.StartsWith("http://localhost:8765/api/speech/audio/");
    FGuid Capability;
    if (!bLocal || !FGuid::Parse(CurrentId, Capability) || !AudioUrl.EndsWith("/" + CurrentId + "/"))
    { Fail(TEXT("Invalid local speech URL or utterance ID")); return; }
    if (!SolverModel) { Fail(TEXT("StreamingADA solver model is missing; check cooked content")); return; }
    const uint32 ExpectedGeneration = Generation;
    Request = FHttpModule::Get().CreateRequest();
    Request->SetURL(AudioUrl);
    Request->SetVerb("GET");
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
                if (!IModularFeatures::Get().IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName))
                { Self->Fail(TEXT("Live Link is unavailable")); return; }
                ILiveLinkClient& Client = IModularFeatures::Get().GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
                Self->Source = MakeShared<FInterviewerAudioSource>(Self->SubjectName);
                Client.AddSource(Self->Source);
                Self->bPreparing = true;
                Self->ReadyStarted = FPlatformTime::Seconds();
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

void UInterviewerSpeechComponent::TickComponent(float Delta, ELevelTick TickType, FActorComponentTickFunction* Tick)
{
    Super::TickComponent(Delta, TickType, Tick);
    if (!bPreparing && !bClockStarted) return;
    const double Now = FPlatformTime::Seconds();
    if (bPreparing)
    {
        if (!Source || !Source->IsReady())
        {
            if (Now - ReadyStarted > 10) Fail(TEXT("Native audio subject did not become ready"));
            return;
        }
        bPreparing = false;
        bClockStarted = true;
        PlaybackEpoch = Now;
        PushedSamples = 0;
        Wave = NewObject<USoundWaveProcedural>(this);
        Wave->SetSampleRate(SampleRate);
        Wave->NumChannels = 1;
        Wave->Duration = static_cast<float>(Samples.Num()) / SampleRate;
        Wave->QueueAudio(reinterpret_cast<const uint8*>(Samples.GetData()), Samples.Num() * sizeof(int16));
        Audio->SetSound(Wave);
    }
    const double Elapsed = Now - PlaybackEpoch;
    LastGeneratedFrameCount = Source->GetGeneratedFrames();
    if (Elapsed > 2.0 && LastGeneratedFrameCount == 0)
    { Fail(TEXT("Native speech solver produced no animation; inspect the Unreal runtime log")); return; }
    // Feed the solver in 40 ms blocks and pad the tail for lookahead/neutral settling.
    const int32 Target = FMath::Min(static_cast<int32>(Elapsed * SampleRate) + 960, Samples.Num() + 9600);
    while (PushedSamples < Target)
    {
        const int32 Count = FMath::Min(960, Target - PushedSamples);
        if (PushedSamples < Samples.Num())
        {
            const int32 Available = FMath::Min(Count, Samples.Num() - PushedSamples);
            Source->Push(Samples.GetData() + PushedSamples, Available, SampleRate);
            PushedSamples += Available;
        }
        else
        {
            TArray<int16> Silence;
            Silence.AddZeroed(Count);
            Source->Push(Silence.GetData(), Count, SampleRate);
            PushedSamples += Count;
        }
    }
    if (!bPlaying && Elapsed >= AudioDelaySeconds)
    {
        bPlaying = true;
        Audio->Play();
        LastPlaybackEvent = "playback_started";
        OnPlaybackEvent.Broadcast(TEXT("playback_started"), CurrentId);
    }
    const double Duration = static_cast<double>(Samples.Num()) / SampleRate;
    if (bPlaying && Elapsed >= AudioDelaySeconds + Duration) FinishPlayback();
}

void UInterviewerSpeechComponent::ReleaseSource()
{
    if (Source)
    {
        Source->RequestSourceShutdown();
        if (IModularFeatures::Get().IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName))
        {
            auto& Client = IModularFeatures::Get().GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
            Client.RemoveSource(Source->GetSourceGuid());
        }
        Source.Reset();
    }
}

void UInterviewerSpeechComponent::StopSpeaking()
{
    ++Generation;
    const FString StoppedId = CurrentId;
    if (Request) { Request->CancelRequest(); Request.Reset(); }
    if (Audio) Audio->Stop();
    bPreparing = bClockStarted = bPlaying = false;
    ReleaseSource();
    Wave = nullptr;
    Samples.Empty();
    CurrentId.Empty();
    if (!StoppedId.IsEmpty())
    {
        LastPlaybackEvent = "interrupted";
        OnPlaybackEvent.Broadcast(TEXT("interrupted"), StoppedId);
    }
}

void UInterviewerSpeechComponent::FinishPlayback()
{
    const FString CompletedId = CurrentId;
    CurrentId.Empty();
    StopSpeaking();
    LastPlaybackEvent = "playback_finished";
    OnPlaybackEvent.Broadcast(TEXT("playback_finished"), CompletedId);
}

void UInterviewerSpeechComponent::Fail(const FString& Reason)
{
    LastError = Reason;
    const FString FailedId = CurrentId;
    CurrentId.Empty();
    StopSpeaking();
    LastPlaybackEvent = "playback_failed";
    OnPlaybackEvent.Broadcast(TEXT("playback_failed"), FailedId);
}
