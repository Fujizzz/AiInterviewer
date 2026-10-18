#include "InterviewerMotionEditorLibrary.h"
#include "AnimGraphNode_InterviewerHeadMotion.h"
#include "AnimGraphNode_Root.h"
#include "Animation/AnimBlueprint.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNodeUtils.h"
#include "Kismet2/BlueprintEditorUtils.h"

bool UInterviewerMotionEditorLibrary::AppendBodyHeadMotion(UAnimBlueprint* Blueprint, bool Apply, FString& Detail)
{
    Detail = TEXT("Expected one AnimGraph output with one existing source");
    if (!Blueprint) return false;
    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);
    UEdGraph* Graph = nullptr;
    for (auto* Candidate : Graphs) if (Candidate->GetFName() == TEXT("AnimGraph")) Graph = Candidate;
    if (!Graph) return false;
    UAnimGraphNode_Root* Root = nullptr;
    UAnimGraphNode_InterviewerHeadMotion* Existing = nullptr;
    int32 Roots = 0, MotionNodes = 0;
    for (UEdGraphNode* Node : Graph->Nodes)
    {
        if (auto* Candidate = Cast<UAnimGraphNode_Root>(Node)) { Root = Candidate; ++Roots; }
        if (auto* Candidate = Cast<UAnimGraphNode_InterviewerHeadMotion>(Node)) { Existing = Candidate; ++MotionNodes; }
    }
    if (Roots != 1 || MotionNodes > 1) return false;
    UEdGraphPin* Result = Root->FindPin(TEXT("Result"));
    if (!Result || Result->LinkedTo.Num() != 1) return false;
    if (Existing)
    {
        UEdGraphPin* Source = Existing->FindPin(TEXT("Source"));
        if (Result->LinkedTo[0]->GetOwningNode() != Existing || !Source || Source->LinkedTo.Num() != 1) return false;
        Detail = TEXT("Existing Body head-motion node is connected; no change required");
        return true;
    }
    if (!Apply)
    {
        Detail = FString::Printf(TEXT("Ready to append after %s; all existing graph nodes and post-process are retained"),
            *Result->LinkedTo[0]->GetOwningNode()->GetName());
        return true;
    }
    Blueprint->Modify();
    Graph->Modify();
    Root->Modify();
    UEdGraphPin* Prior = Result->LinkedTo[0];
    FGraphNodeCreator<UAnimGraphNode_InterviewerHeadMotion> Creator(*Graph);
    auto* Added = Creator.CreateNode();
    Added->NodePosX = Root->NodePosX - 220;
    Added->NodePosY = Root->NodePosY;
    Creator.Finalize();
    auto* Source = Added->FindPin(TEXT("Source"));
    UEdGraphPin* Output = nullptr;
    for (auto* Pin : Added->Pins) if (Pin->Direction == EGPD_Output) Output = Pin;
    if (!Source || !Output) { Graph->RemoveNode(Added); return false; }
    Result->BreakLinkTo(Prior);
    const UEdGraphSchema* Schema = Graph->GetSchema();
    if (!Schema->TryCreateConnection(Prior, Source) || !Schema->TryCreateConnection(Output, Result))
    {
        Source->BreakAllPinLinks();
        Output->BreakAllPinLinks();
        Schema->TryCreateConnection(Prior, Result);
        Graph->RemoveNode(Added);
        Detail = TEXT("Head-motion insertion failed; original output connection restored");
        return false;
    }
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    Detail = TEXT("Appended native Body head-motion node; compile and save after validation");
    return true;
}
