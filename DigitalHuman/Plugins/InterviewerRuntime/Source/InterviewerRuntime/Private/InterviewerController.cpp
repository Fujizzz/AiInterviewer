#include "InterviewerController.h"
#include "InterviewerSpeechComponent.h"
#include "InterviewerFaceAnimInstance.h"
#include "InterviewerExpressionPlanHelpers.h"
#include "InterviewerMotionComponent.h"
#include "Async/Async.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimInstance.h"
#include "LiveLinkTypes.h"
#include "UObject/UnrealType.h"
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

AInterviewerController::AInterviewerController()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    Speech = CreateDefaultSubobject<UInterviewerSpeechComponent>(TEXT("Speech"));
}

void AInterviewerController::BeginPlay()
{
    Super::BeginPlay();
    Speech->OnPlaybackEvent.AddDynamic(this, &AInterviewerController::HandlePlayback);
    if (InterviewCamera)
    {
        if (auto* PC = UGameplayStatics::GetPlayerController(this, 0)) PC->SetViewTarget(InterviewCamera);
    }
    // PixelStreaming2 input is an internal class. Use its reflected Blueprint interface
    // rather than linking against private engine headers or changing the engine.
    UClass* InputClass = LoadClass<UActorComponent>(nullptr, TEXT("/Script/PixelStreaming2.PixelStreaming2Input"));
    if (InputClass)
    {
        PixelInput = NewObject<UActorComponent>(this, InputClass);
        if (auto* Property = FindFProperty<FMulticastDelegateProperty>(InputClass, TEXT("OnInputEvent")))
        {
            FScriptDelegate Delegate;
            Delegate.BindUFunction(this, TEXT("HandleInput"));
            Property->AddDelegate(Delegate, PixelInput);
        }
        PixelInput->RegisterComponent();
    }
    BindAvatar(false);
}

void AInterviewerController::BindAvatar(bool bEnabled)
{
    if (!Avatar) return;
    if (LastBoundAvatar.Get() == Avatar && bLastLiveLinkEnabled == bEnabled) return;
    LastBoundAvatar = Avatar;
    bLastLiveLinkEnabled = bEnabled;
    const auto SetLiveLinkProperties = [this, bEnabled](UObject* Target)
    {
        for (TFieldIterator<FProperty> It(Target->GetClass()); It; ++It)
        {
            const FString Name = It->GetName();
            const FString CompactName = Name.Replace(TEXT(" "), TEXT("")).Replace(TEXT("_"), TEXT(""));
            if (FParse::Param(FCommandLine::Get(), TEXT("InterviewDiagnostics"))
                && (CompactName.Contains("LiveLink") || CompactName.Contains("LLink")))
            {
                FString Value;
                It->ExportTextItem_Direct(Value, It->ContainerPtrToValuePtr<void>(Target), nullptr, Target, PPF_None);
                UE_LOG(LogTemp, Display, TEXT("Interviewer binding target=%s property=%s type=%s value=%s enable=%d"),
                    *Target->GetClass()->GetName(), *Name, *It->GetCPPType(), *Value, bEnabled);
            }
            if (auto* Struct = CastField<FStructProperty>(*It))
            {
                if (Struct->Struct == FLiveLinkSubjectName::StaticStruct()
                    && (CompactName.Contains("LiveLink") || CompactName.Contains("LLink")))
                {
                    auto* Value = Struct->ContainerPtrToValuePtr<FLiveLinkSubjectName>(Target);
                    Value->Name = Speech->SubjectName;
                }
            }
            if (auto* Bool = CastField<FBoolProperty>(*It))
            {
                if (CompactName == "bUseLiveLink" || CompactName == "UseLiveLink")
                    // The persistent Face consumes buffered native speech directly.
                    Bool->SetPropertyValue_InContainer(Target, false);
            }
        }
    };
    SetLiveLinkProperties(Avatar);
    // Keep one face instance for all interview states. Replacing it at speech
    // boundaries resets its curves and creates a visible expression cut.
    TArray<USkeletalMeshComponent*> Meshes;
    Avatar->GetComponents(Meshes);
    USkeletalMeshComponent* BodyMesh = nullptr;
    for (auto* Mesh : Meshes) if (Mesh->GetName() == TEXT("Body")) BodyMesh = Mesh;
    for (auto* Mesh : Meshes)
    {
        if (Mesh->GetName() == TEXT("Face"))
        {
            if (BoundFace.Get() != Mesh)
            {
                if (BoundFace.IsValid()) BoundFace->SetAnimInstanceClass(IdleFaceAnimationClass);
                BoundFace = Mesh;
                IdleFaceAnimationClass = Mesh->GetAnimInstance() ? Mesh->GetAnimInstance()->GetClass() : nullptr;
            }
            if (!Cast<UInterviewerFaceAnimInstance>(Mesh->GetAnimInstance()))
            {
                Mesh->SetAnimInstanceClass(UInterviewerFaceAnimInstance::StaticClass());
            }
            if (auto* FaceInstance = Cast<UInterviewerFaceAnimInstance>(Mesh->GetAnimInstance()))
            {
                FaceInstance->SpeechPlayback = Speech;
                FaceInstance->TransitionSeconds = FMath::Clamp(FaceTransitionSeconds, 0.0f, 1.0f);
                FaceInstance->SpeechReleaseSeconds = FMath::Clamp(FaceSpeechReleaseSeconds, 0.0f, 2.0f);
                FaceInstance->RecordedUpperFaceWeight = FMath::Clamp(SpeakingRecordedUpperFaceWeight, 0.0f, 1.0f);
                FaceInstance->SetPresentation(bEnabled, Speech->SubjectName);
                FaceInstance->SetExpressionState(State);
            }
        }
        if (auto* Instance = Mesh->GetAnimInstance()) SetLiveLinkProperties(Instance);
    }
    if (!PresentationMotion || PresentationMotion->GetOwner() != Avatar)
    {
        if (PresentationMotion) PresentationMotion->DestroyComponent();
        PresentationMotion = NewObject<UInterviewerMotionComponent>(Avatar, TEXT("InterviewerPresentationMotion"));
        PresentationMotion->RegisterComponent();
    }
    PresentationMotion->Configure(BodyMesh, BoundFace.Get(), Speech, InterviewCamera);
}

void AInterviewerController::SetState(const FString& NewState)
{
    if (NewState != "idle" && NewState != "listening" && NewState != "thinking"
        && NewState != "speaking" && NewState != "interrupted") return;
    State = NewState;
    BindAvatar(State == "speaking");
    if (BoundFace.IsValid())
        if (auto* Face = Cast<UInterviewerFaceAnimInstance>(BoundFace->GetAnimInstance())) Face->SetExpressionState(State);
    OnStateChanged(State);
}

void AInterviewerController::HandleInput(const FString& Descriptor)
{
    if (!IsInGameThread())
    {
        const TWeakObjectPtr<AInterviewerController> WeakThis(this);
        AsyncTask(ENamedThreads::GameThread, [WeakThis, Descriptor]()
        {
            if (WeakThis.IsValid()) WeakThis->HandleInput(Descriptor);
        });
        return;
    }
    if (Descriptor.Len() > 4096) return;
    TSharedPtr<FJsonObject> Message;
    const auto Reader = TJsonReaderFactory<>::Create(Descriptor);
    if (!FJsonSerializer::Deserialize(Reader, Message) || !Message.IsValid()) return;
    FString Type;
    if (!Message->TryGetStringField(TEXT("type"), Type)) return;
    if (Type == "expression_plan" || Type == "expression_clear")
    {
        FString Id, Detail;
        Message->TryGetStringField(TEXT("utterance_id"), Id);
        auto* Face = BoundFace.IsValid() ? Cast<UInterviewerFaceAnimInstance>(BoundFace->GetAnimInstance()) : nullptr;
        if (!Face)
        {
            Respond(TEXT("expression_rejected"), Id, TEXT("No persistent MetaHuman Face instance"));
            return;
        }
        UE::Interviewer::EExpressionApplyResult Result = UE::Interviewer::EExpressionApplyResult::Rejected;
        if (Type == "expression_plan")
        {
            UE::Interviewer::FExpressionPlan Plan;
            if (UE::Interviewer::ParseExpressionPlan(Message, Plan, Detail)) Result = Face->SetExpressionPlan(Plan, Detail);
        }
        else
        {
            FString PresentationId;
            int32 Generation = 0;
            if (UE::Interviewer::ParseExpressionClear(Message, PresentationId, Generation, Detail))
                Result = Face->ClearExpressionPlan(PresentationId, Generation, Detail);
        }
        Respond(Result == UE::Interviewer::EExpressionApplyResult::Applied
            ? TEXT("expression_applied") : TEXT("expression_rejected"), Id, Detail);
    }
    else if (Type == "listening_activity")
    {
        UE::Interviewer::FListeningActivity Activity;
        FString Detail;
        UE::Interviewer::EExpressionApplyResult Result = UE::Interviewer::EExpressionApplyResult::Rejected;
        if (UE::Interviewer::ParseListeningActivity(Message, Activity, Detail) && PresentationMotion)
            Result = PresentationMotion->ApplyListeningActivity(Activity, Detail);
        Respond(Result == UE::Interviewer::EExpressionApplyResult::Applied
            ? TEXT("listening_activity_applied") : TEXT("listening_activity_rejected"), TEXT(""), Detail);
    }
    else if (Type == "ping")
    {
        Respond(TEXT("avatar_ready"), TEXT(""), Avatar ? TEXT("") : TEXT("Assign an assembled avatar to the controller"));
    }
    else if (Type == "speak")
    {
        FString Url, Id;
        if (Message->TryGetStringField(TEXT("audio_url"), Url)
            && Message->TryGetStringField(TEXT("utterance_id"), Id))
        {
            if (!Avatar) { Respond(TEXT("playback_failed"), Id, TEXT("No assembled MetaHuman assigned")); return; }
            Speech->Speak(Url, Id);
        }
    }
    else if (Type == "stop") { Speech->StopSpeaking(); SetState(TEXT("listening")); }
    else if (Type == "state")
    {
        FString Value;
        if (Message->TryGetStringField(TEXT("state"), Value)) SetState(Value);
    }
}

void AInterviewerController::HandlePlayback(FString Event, FString UtteranceId)
{
    if (BoundFace.IsValid())
    {
        if (auto* Face = Cast<UInterviewerFaceAnimInstance>(BoundFace->GetAnimInstance()))
        {
            if (Event == TEXT("playback_started")) Face->ExpressionPlaybackStarted(UtteranceId);
            else if (Event == TEXT("playback_finished") || Event == TEXT("interrupted") || Event == TEXT("playback_failed"))
                Face->ExpressionPlaybackStopped(UtteranceId, Event == TEXT("playback_finished"));
        }
    }
    if (Event == "playback_started") SetState(TEXT("speaking"));
    else if (Event == "interrupted") SetState(TEXT("interrupted"));
    else SetState(TEXT("listening"));
    Respond(Event, UtteranceId, Event == "playback_failed" ? Speech->LastError : FString());
}

void AInterviewerController::Respond(const FString& Event, const FString& UtteranceId, const FString& Detail)
{
    if (!PixelInput) return;
    auto Message = MakeShared<FJsonObject>();
    Message->SetStringField(TEXT("type"), Event);
    Message->SetStringField(TEXT("utterance_id"), UtteranceId);
    Message->SetStringField(TEXT("detail"), Detail);
    if (FParse::Param(FCommandLine::Get(), TEXT("InterviewDiagnostics"))
        && (Event == TEXT("playback_started") || Event == TEXT("playback_finished") || Event == TEXT("playback_failed")))
    {
        auto Diagnostics = MakeShared<FJsonObject>();
        Diagnostics->SetNumberField(TEXT("prepared_frames"), Speech->LastGeneratedFrameCount);
        Diagnostics->SetNumberField(TEXT("preparation_ms"), Speech->LastSpeechPreparationMs);
        Diagnostics->SetNumberField(TEXT("solve_ms"), Speech->LastSpeechSolveMs);
        Diagnostics->SetNumberField(TEXT("duration_seconds"), Speech->LastSpeechDurationSeconds);
        Diagnostics->SetNumberField(TEXT("sampled_frames"), Speech->LastSpeechSampleCount);
        Diagnostics->SetNumberField(TEXT("audio_seconds"), Speech->LastSpeechAudioSeconds);
        Diagnostics->SetNumberField(TEXT("curve_seconds"), Speech->LastSpeechCurveSeconds);
        Diagnostics->SetNumberField(TEXT("max_curve_gap_ms"), Speech->LastSpeechMaxFrameGapSeconds * 1000.0);
        if (BoundFace.IsValid())
            if (const auto* Face = Cast<UInterviewerFaceAnimInstance>(BoundFace->GetAnimInstance()))
            {
                Diagnostics->SetNumberField(TEXT("curve_count"), Face->LastSpeechCurveCount);
                Diagnostics->SetNumberField(TEXT("face_evaluations"), Face->SpeechEvaluationCount);
                Diagnostics->SetNumberField(TEXT("max_face_gap_ms"), Face->SpeechMaxEvaluationGapSeconds * 1000.0);
                Diagnostics->SetNumberField(TEXT("jaw_min"), Face->SpeechJawMinimum);
                Diagnostics->SetNumberField(TEXT("jaw_max"), Face->SpeechJawMaximum);
            }
        Message->SetObjectField(TEXT("diagnostics"), Diagnostics);
    }
    FString Payload;
    const auto Writer = TJsonWriterFactory<>::Create(&Payload);
    FJsonSerializer::Serialize(Message, Writer);
    if (UFunction* Function = PixelInput->FindFunction(TEXT("SendPixelStreaming2Response")))
    {
        struct FParams { FString Descriptor; } Params{Payload};
        PixelInput->ProcessEvent(Function, &Params);
    }
}
