"""Estimate full rounds, with a final question allowed before the start cutoff."""

from math import ceil


def round_cost(context, settings):
    # Feedback receives synchronized wall-clock deltas, including generation,
    # review, candidate time and evaluation. Keep the cold-start estimate until
    # real rounds are observed; never substitute the 30-second absolute floor.
    return max(settings.planning.minimum_question_seconds, ceil(context.estimated_question_seconds))


def final_question_window(context, settings, *, closing_seconds=None):
    """Release reserved closing time instead of ending before the explicit cutoff."""
    closing = context.plan.closing_seconds if closing_seconds is None else closing_seconds
    remaining = context.state.remaining_seconds
    return (
        context.plan.planning_enabled
        and remaining > settings.planning.question_start_cutoff_seconds
        and remaining < closing + round_cost(context, settings)
    )


def admission_cost(context, settings):
    """Allocate remaining time for a final question; its answer can finish later."""
    estimate = round_cost(context, settings)
    if final_question_window(context, settings):
        return min(estimate, context.state.remaining_seconds)
    return estimate
