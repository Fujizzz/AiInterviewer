#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "InterviewerPresentationMotion.h"
#include "InterviewerSpeechComponent.generated.h"

class UAudioComponent;
class USoundWaveProcedural;
class FInterviewerSpeechSolverState;
class IHttpRequest;
namespace UE::Interviewer { struct FSpeechTimeline; struct FSpeechPreparationFence; }

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

    UE::Interviewer::FSpeechRhythm GetSpeechRhythm(double Now) const;
    bool GetSpeechCurves(TMap<FName, float>& Out, double Now) const;

    UPROPERTY(BlueprintAssignable, Category="Interview")
    FInterviewerPlaybackEvent OnPlaybackEvent;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview")
    FName SubjectName = "InterviewerAudio";

    /** Retained for existing Blueprint settings; prepared timelines compensate lookahead directly. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Interview", meta=(ClampMin="0.08", ClampMax="1.0"))
    float AudioDelaySeconds = 0.16f;

    UPROPERTY(BlueprintReadOnly, Category="Interview")
    FString LastError;

    /** Runtime diagnostics also used by the offline native solver smoke test. */
    UPROPERTY(BlueprintReadOnly, Category="Interview")
    int32 LastGeneratedFrameCount = 0;

    UPROPERTY(BlueprintReadOnly, Category="Interview")
    FString LastPlaybackEvent;

    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechPreparationMs = 0.0f;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechSolveMs = 0.0f;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechDurationSeconds = 0.0f;
    /** Number of playback-clock updates observed for this utterance. */
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    int32 LastSpeechSampleCount = 0;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechSampleFrame = 0.0f;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechAudioSeconds = 0.0f;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechCurveSeconds = 0.0f;
    UPROPERTY(BlueprintReadOnly, Category="Interview|Diagnostics")
    float LastSpeechMaxFrameGapSeconds = 0.0f;

private:
    UPROPERTY(Transient) TObjectPtr<UAudioComponent> Audio;
    UPROPERTY(Transient) TObjectPtr<USoundWaveProcedural> Wave;
    UPROPERTY(Transient) TObjectPtr<UObject> SolverModel;
    TSharedPtr<FInterviewerSpeechSolverState, ESPMode::ThreadSafe> SolverState;
    TSharedPtr<UE::Interviewer::FSpeechPreparationFence, ESPMode::ThreadSafe> PreparationJob;
    TSharedPtr<UE::Interviewer::FSpeechTimeline, ESPMode::ThreadSafe> SpeechTimeline;
    TSharedPtr<IHttpRequest, ESPMode::ThreadSafe> Request;
    TArray<int16> Samples;
    FString CurrentId;
    uint32 Generation = 0;
    double PreparationStarted = 0;
    double AudiblePlaybackEpoch = 0;
    int32 SampleRate = 24000;
    bool bPreparing = false;
    bool bPlaying = false;

    bool DecodeWave(const TArray<uint8>& Bytes);
    void Fail(const FString& Reason);
    void PrepareSpeech();
    void FinishPlayback();
};
