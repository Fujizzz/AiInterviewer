#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "InterviewerMotionEditorLibrary.generated.h"

class UAnimBlueprint;

UCLASS()
class INTERVIEWERRUNTIMEEDITOR_API UInterviewerMotionEditorLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    /** Validates the existing output; Apply inserts one node without saving assets. */
    UFUNCTION(BlueprintCallable, Category="Interview|Editor")
    static bool AppendBodyHeadMotion(UAnimBlueprint* Blueprint, bool Apply, FString& Detail);
};
