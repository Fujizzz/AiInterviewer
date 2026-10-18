#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "InterviewerController.h"
#include "InterviewerSpeechComponent.h"
#include "InterviewerFaceAnimInstance.h"
#include "Components/AudioComponent.h"
#include "Features/IModularFeatures.h"
#include "ILiveLinkClient.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimInstance.h"
#include "UObject/UnrealType.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerAvatarBinding,
    "Interviewer.AvatarBinding", EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerAvatarBinding::RunTest(const FString& Parameters)
{
    for (const FWorldContext& Context : GEngine->GetWorldContexts())
    {
        if (Context.WorldType != EWorldType::Game && Context.WorldType != EWorldType::PIE) continue;
        for (TActorIterator<AInterviewerController> It(Context.World()); It; ++It)
        {
            if (!TestNotNull(TEXT("Scene has the assembled avatar"), It->Avatar.Get())) return false;
            TestNotNull(TEXT("Scene has an interview camera"), It->InterviewCamera.Get());
            TArray<USkeletalMeshComponent*> Meshes;
            It->Avatar->GetComponents(Meshes);
            USkeletalMeshComponent* Face = nullptr;
            USkeletalMeshComponent* Body = nullptr;
            for (auto* Mesh : Meshes)
            {
                if (Mesh->GetName() == TEXT("Face")) Face = Mesh;
                if (Mesh->GetName() == TEXT("Body")) Body = Mesh;
            }
            if (!TestNotNull(TEXT("Avatar has the face mesh"), Face)) return false;
            auto* FaceInstance = Cast<UInterviewerFaceAnimInstance>(Face->GetAnimInstance());
            if (!TestNotNull(TEXT("Avatar uses the persistent face animation"), FaceInstance)) return false;
            UAnimInstance* BodyInstance = Body ? Body->GetAnimInstance() : nullptr;
            TestNotNull(TEXT("Avatar keeps its body animation"), BodyInstance);
            It->SetState(TEXT("speaking"));
            TestTrue(TEXT("Speaking keeps the same face instance"), Face->GetAnimInstance() == FaceInstance);
            TestTrue(TEXT("Speaking requests speech presentation"), FaceInstance->bSpeakingTarget);
            TestEqual(TEXT("Face animation uses the TTS subject"), FaceInstance->SubjectName, It->Speech->SubjectName);
            It->SetState(TEXT("listening"));
            TestTrue(TEXT("Listening keeps the same face instance"), Face->GetAnimInstance() == FaceInstance);
            TestFalse(TEXT("Listening requests recorded expression presentation"), FaceInstance->bSpeakingTarget);
            It->SetState(TEXT("thinking"));
            TestTrue(TEXT("Thinking keeps the same face instance"), Face->GetAnimInstance() == FaceInstance);
            if (Body) TestTrue(TEXT("State changes keep the body instance"), Body->GetAnimInstance() == BodyInstance);
            It->SetState(TEXT("listening"));
            return true;
        }
    }
    AddError(TEXT("Run AvatarBinding in the assembled L_Interview game scene"));
    return false;
}

// Requires Tools/audio_smoke_fixture.py, which serves a labelled synthetic tone.
// Tests real HTTP, WAV playback and native animation inference without cloud calls
// or an assembled avatar. Visual lip synchronisation requires a separate human test.
DEFINE_LATENT_AUTOMATION_COMMAND_TWO_PARAMETER(FWaitInterviewerSpeech,
    TWeakObjectPtr<UInterviewerSpeechComponent>, Speech, FAutomationTestBase*, Test);

bool FWaitInterviewerSpeech::Update()
{
    if (!Speech.IsValid()) { Test->AddError(TEXT("Speech component disappeared")); return true; }
    if (Speech->LastPlaybackEvent == "playback_failed")
    { Test->AddError(Speech->LastError); return true; }
    if (Speech->LastPlaybackEvent == "playback_finished")
    {
        Test->TestTrue(TEXT("Native solver emitted animation frames"), Speech->LastGeneratedFrameCount > 0);
        UE_LOG(LogTemp, Display, TEXT("Interviewer native smoke: %d animation frames"), Speech->LastGeneratedFrameCount);
        return true;
    }
    if (FPlatformTime::Seconds() - StartTime > 25)
    { Speech->StopSpeaking(); Test->AddError(TEXT("Native audio smoke timed out")); return true; }
    return false;
}

DEFINE_LATENT_AUTOMATION_COMMAND_TWO_PARAMETER(FStartSecondInterviewerSpeech,
    TWeakObjectPtr<UInterviewerSpeechComponent>, Speech, FAutomationTestBase*, Test);
bool FStartSecondInterviewerSpeech::Update()
{
    if (!Speech.IsValid()) { Test->AddError(TEXT("Speech component disappeared")); return true; }
    Speech->Speak(TEXT("http://127.0.0.1:8765/api/speech/audio/00000000-0000-0000-0000-000000000002/"),
        TEXT("00000000-0000-0000-0000-000000000002"));
    return true;
}

DEFINE_LATENT_AUTOMATION_COMMAND_TWO_PARAMETER(FInterruptInterviewerSpeech,
    TWeakObjectPtr<UInterviewerSpeechComponent>, Speech, FAutomationTestBase*, Test);
bool FInterruptInterviewerSpeech::Update()
{
    if (!Speech.IsValid()) { Test->AddError(TEXT("Speech component disappeared")); return true; }
    if (Speech->LastPlaybackEvent == "playback_failed")
    { Test->AddError(Speech->LastError); return true; }
    if (Speech->LastPlaybackEvent == "playback_started" && Speech->LastGeneratedFrameCount > 0)
    {
        Speech->StopSpeaking();
        Test->TestEqual(TEXT("Interruption event"), Speech->LastPlaybackEvent, FString("interrupted"));
        TArray<UAudioComponent*> AudioComponents;
        Speech->GetOwner()->GetComponents(AudioComponents);
        for (auto* Audio : AudioComponents) Test->TestFalse(TEXT("Audio stops on interruption"), Audio->IsPlaying());
        UE_LOG(LogTemp, Display, TEXT("Interviewer native smoke: second WAV interrupted successfully"));
        return true;
    }
    if (FPlatformTime::Seconds() - StartTime > 25)
    { Speech->StopSpeaking(); Test->AddError(TEXT("Second audio did not start")); return true; }
    return false;
}

DEFINE_LATENT_AUTOMATION_COMMAND_ONE_PARAMETER(FCheckInterviewerSourceRemoved, FAutomationTestBase*, Test);
bool FCheckInterviewerSourceRemoved::Update()
{
    if (FPlatformTime::Seconds() - StartTime < 0.2) return false;
    auto& Client = IModularFeatures::Get().GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
    for (const auto& Key : Client.GetSubjects(true, true))
        if (Key.SubjectName == FLiveLinkSubjectName("InterviewerAudio"))
            Test->AddError(TEXT("Interrupted animation subject was not removed"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerAudioSmoke,
    "Interviewer.NativeAudioSmoke", EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter);

bool FInterviewerAudioSmoke::RunTest(const FString& Parameters)
{
    for (const FWorldContext& Context : GEngine->GetWorldContexts())
    {
        if (Context.WorldType != EWorldType::Game && Context.WorldType != EWorldType::PIE) continue;
        for (TActorIterator<AInterviewerController> It(Context.World()); It; ++It)
        {
            It->Speech->Speak(TEXT("http://127.0.0.1:8765/api/speech/audio/00000000-0000-0000-0000-000000000001/"),
                TEXT("00000000-0000-0000-0000-000000000001"));
            ADD_LATENT_AUTOMATION_COMMAND(FWaitInterviewerSpeech(It->Speech, this));
            ADD_LATENT_AUTOMATION_COMMAND(FStartSecondInterviewerSpeech(It->Speech, this));
            ADD_LATENT_AUTOMATION_COMMAND(FInterruptInterviewerSpeech(It->Speech, this));
            ADD_LATENT_AUTOMATION_COMMAND(FCheckInterviewerSourceRemoved(this));
            return true;
        }
    }
    AddError(TEXT("Open L_Interview in -game mode before running the audio smoke test"));
    return false;
}
#endif
