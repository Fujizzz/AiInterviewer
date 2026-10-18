#pragma once

#include "CoreMinimal.h"
#include "AnimGraphNode_Base.h"
#include "AnimNode_InterviewerHeadMotion.h"
#include "AnimGraphNode_InterviewerHeadMotion.generated.h"

UCLASS()
class INTERVIEWERRUNTIMEEDITOR_API UAnimGraphNode_InterviewerHeadMotion : public UAnimGraphNode_Base
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere, Category=Settings) FAnimNode_InterviewerHeadMotion Node;
    virtual FText GetNodeTitle(ENodeTitleType::Type TitleType) const override;
    virtual FText GetTooltipText() const override;
    virtual FText GetMenuCategory() const override;
};
