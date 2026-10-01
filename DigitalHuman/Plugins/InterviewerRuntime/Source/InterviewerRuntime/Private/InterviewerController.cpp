#include "InterviewerController.h"
#include "InterviewerSpeechComponent.h"
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
                    Bool->SetPropertyValue_InContainer(Target, bEnabled);
            }
        }
    };
    SetLiveLinkProperties(Avatar);
    // Use the same shared face animation as the assembled Blueprint's LiveLinkSetup.
    // That Blueprint function requires component parameters; toggle only the Face
    // here so the interviewer's separately managed body animation stays independent.
    TArray<USkeletalMeshComponent*> Meshes;
    Avatar->GetComponents(Meshes);
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
            UClass* AnimationClass = bEnabled
                ? LoadClass<UAnimInstance>(nullptr, TEXT("/Game/MetaHumans/Common/Animation/ABP_MH_LiveLink.ABP_MH_LiveLink_C"))
                : IdleFaceAnimationClass.Get();
            if (bEnabled && !AnimationClass)
            {
                UE_LOG(LogTemp, Warning, TEXT("Interviewer: MetaHuman face Live Link animation is missing"));
            }
            else
            {
                Mesh->SetAnimInstanceClass(AnimationClass);
            }
        }
        if (auto* Instance = Mesh->GetAnimInstance()) SetLiveLinkProperties(Instance);
    }
}

void AInterviewerController::SetState(const FString& NewState)
{
    if (NewState != "idle" && NewState != "listening" && NewState != "thinking"
        && NewState != "speaking" && NewState != "interrupted") return;
    State = NewState;
    BindAvatar(State == "speaking");
    OnStateChanged(State);
}

void AInterviewerController::HandleInput(const FString& Descriptor)
{
    if (Descriptor.Len() > 4096) return;
    TSharedPtr<FJsonObject> Message;
    const auto Reader = TJsonReaderFactory<>::Create(Descriptor);
    if (!FJsonSerializer::Deserialize(Reader, Message) || !Message.IsValid()) return;
    FString Type;
    if (!Message->TryGetStringField(TEXT("type"), Type)) return;
    if (Type == "ping")
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
    FString Payload;
    const auto Writer = TJsonWriterFactory<>::Create(&Payload);
    FJsonSerializer::Serialize(Message, Writer);
    if (UFunction* Function = PixelInput->FindFunction(TEXT("SendPixelStreaming2Response")))
    {
        struct FParams { FString Descriptor; } Params{Payload};
        PixelInput->ProcessEvent(Function, &Params);
    }
}
