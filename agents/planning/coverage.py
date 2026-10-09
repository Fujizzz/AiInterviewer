"""Only conflicts touching an objective's evidence block that objective."""


def conflicts_for_coverage(analysis, current_quotes, accepted_evidence):
    proofs = analysis.contradiction_evidence
    if not proofs:
        return list(analysis.contradictions)  # Older callers lack scoped proofs; stay conservative.
    prior_quotes = [q for record in accepted_evidence for q in record["supporting_quotes"]]
    return list(
        dict.fromkeys(
            proof.explanation
            for proof in proofs
            if any(
                proof.current_quote in q or q in proof.current_quote for q in current_quotes if q
            )
            or any(proof.earlier_quote in q or q in proof.earlier_quote for q in prior_quotes if q)
        )
    )
