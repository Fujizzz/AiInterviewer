#include "InterviewerMotionComponent.h"
#include "InterviewerFaceAnimInstance.h"
#include "InterviewerSpeechComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/Actor.h"

UInterviewerMotionComponent::UInterviewerMotionComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.TickGroup = TG_PrePhysics;
}

void UInterviewerMotionComponent::Configure(USkeletalMeshComponent* Body, USkeletalMeshComponent* Face,
    UInterviewerSpeechComponent* Speech, AActor* Camera)
{
    BodyMesh = Body;
    FaceMesh = Face;
    SpeechComponent = Speech;
    InterviewCamera = Camera;
    if (Speech) AddTickPrerequisiteComponent(Speech);
    if (Body) Body->AddTickPrerequisiteComponent(this);
    if (Body && Face) Face->AddTickPrerequisiteComponent(Body);
    if (Face) Face->AddTickPrerequisiteComponent(this);
}

UE::Interviewer::EExpressionApplyResult UInterviewerMotionComponent::ApplyListeningActivity(
    const UE::Interviewer::FListeningActivity& Activity, FString& Detail)
{
    check(IsInGameThread());
    const auto* Face = FaceMesh.IsValid() ? Cast<UInterviewerFaceAnimInstance>(FaceMesh->GetAnimInstance()) : nullptr;
    return Listening.Apply(Activity, Face ? Face->GetExpressionSnapshot() : UE::Interviewer::FExpressionSnapshot(), Detail);
}

void UInterviewerMotionComponent::TickComponent(float Delta, ELevelTick TickType, FActorComponentTickFunction* Tick)
{
    Super::TickComponent(Delta, TickType, Tick);
    const auto* Face = FaceMesh.IsValid() ? Cast<UInterviewerFaceAnimInstance>(FaceMesh->GetAnimInstance()) : nullptr;
    UE::Interviewer::FExpressionSnapshot Plan = Face ? Face->GetExpressionSnapshot() : UE::Interviewer::FExpressionSnapshot();
    Plan.Now = FPlatformTime::Seconds();
    const auto Rhythm = SpeechComponent.IsValid() ? SpeechComponent->GetSpeechRhythm(Plan.Now) : UE::Interviewer::FSpeechRhythm();
    Motion = Runtime.Step(Plan, Listening.Snapshot(Plan), Rhythm, Delta);
    DisplayedHeadMotion = Motion.Head;
    AudibleRms = Rhythm.Rms;
    ComponentHeadRotation = FQuat::Identity;
    if (BodyMesh.IsValid() && InterviewCamera.IsValid())
    {
        // Camera-relative axes avoid assuming a MetaHuman mesh faces UE's +X axis.
        FVector Forward = InterviewCamera->GetActorLocation() - BodyMesh->GetComponentLocation();
        Forward.Z = 0;
        if (Forward.Normalize())
        {
            const FVector Up = FVector::UpVector;
            const FVector Right = FVector::CrossProduct(Up, Forward).GetSafeNormal();
            const FQuat WorldDelta = FQuat(Up, FMath::DegreesToRadians(Motion.Head.Yaw))
                * FQuat(Forward, FMath::DegreesToRadians(Motion.Head.Roll))
                * FQuat(Right, FMath::DegreesToRadians(Motion.Head.Pitch));
            const FQuat BodyRotation = BodyMesh->GetComponentQuat();
            ComponentHeadRotation = BodyRotation.Inverse() * WorldDelta * BodyRotation;
            ComponentHeadRotation.Normalize();
        }
    }
}
