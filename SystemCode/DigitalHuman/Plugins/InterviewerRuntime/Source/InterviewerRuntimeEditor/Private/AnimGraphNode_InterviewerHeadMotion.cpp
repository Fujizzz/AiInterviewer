#include "AnimGraphNode_InterviewerHeadMotion.h"

FText UAnimGraphNode_InterviewerHeadMotion::GetNodeTitle(ENodeTitleType::Type TitleType) const
{
    return NSLOCTEXT("Interviewer", "HeadMotion", "Interviewer Head Motion");
}
FText UAnimGraphNode_InterviewerHeadMotion::GetTooltipText() const
{
    return NSLOCTEXT("Interviewer", "HeadMotionTip", "Adds bounded camera-relative neck and head motion to the existing Body pose.");
}
FText UAnimGraphNode_InterviewerHeadMotion::GetMenuCategory() const
{
    return NSLOCTEXT("Interviewer", "HeadMotionCategory", "Interview");
}
