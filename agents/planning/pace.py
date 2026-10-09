"""One admission cost for compilation and execution: a complete observed round."""

from math import ceil


def round_cost(context, settings):
    # Feedback receives synchronized wall-clock deltas, including generation,
    # review, candidate time and evaluation. Keep the cold-start estimate until
    # real rounds are observed; never substitute the 30-second absolute floor.
    return max(settings.planning.minimum_question_seconds, ceil(context.estimated_question_seconds))
