#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "InterviewerSpeechComponent.generated.h"

class UAudioComponent;
class USoundWaveProcedural;
class FInterviewerAudioSource;
class IHttpRequest;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FInterviewerPlaybackEvent, FString, Event, FString, UtteranceId);

/** One audio buffer drives both audible speech and the native MetaHuman solver. */
UCLASS(ClassGroup=(Interview), meta=(BlueprintSpawnableComponent))
class INTERVIEWERRUNTIME_API UInterviewerSpeechComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UInterviewerSpeechComponent();
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    virtual void TickComponent(float Delta, ELevelTick TickType, FActorComponentTickFunction* Tick) override;

    UFUNCTION(BlueprintCallable, Category="Interview")
    void Speak(const FString& AudioUrl, const FString& UtteranceId);

    UFUNCTION(BlueprintCallable, Category="Interview")
    void StopSpeaking();

    UPROPERTY(BlueprintAssignable, Category="Interview")
    FInterviewerPlaybackEvent OnPlaybackEvent;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview")
    FName SubjectName = "InterviewerAudio";

    /** Delay audible playback to compensate for solver lookahead and computation. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview", meta=(ClampMin="0.08", ClampMax="1.0"))
    float AudioDelaySeconds = 0.16f;

    UPROPERTY(BlueprintReadOnly, Category="Interview")
    FString LastError;

    /** Runtime diagnostics also used by the offline native solver smoke test. */
    UPROPERTY(BlueprintReadOnly, Category="Interview")
    int32 LastGeneratedFrameCount = 0;

    UPROPERTY(BlueprintReadOnly, Category="Interview")
    FString LastPlaybackEvent;

private:
    UPROPERTY(Transient) TObjectPtr<UAudioComponent> Audio;
    UPROPERTY(Transient) TObjectPtr<USoundWaveProcedural> Wave;
    UPROPERTY(Transient) TObjectPtr<UObject> SolverModel;
    TSharedPtr<FInterviewerAudioSource> Source;
    TSharedPtr<IHttpRequest, ESPMode::ThreadSafe> Request;
    TArray<int16> Samples;
    FString CurrentId;
    uint32 Generation = 0;
    double ReadyStarted = 0;
    double PlaybackEpoch = 0;
    int32 PushedSamples = 0;
    int32 SampleRate = 24000;
    bool bPreparing = false;
    bool bClockStarted = false;
    bool bPlaying = false;

    bool DecodeWave(const TArray<uint8>& Bytes);
    void Fail(const FString& Reason);
    void ReleaseSource();
    void FinishPlayback();
};
