"""Order pricing helpers for the AgentNexus acceptance sample.

The sample is deliberately tiny: every acceptance task is a small, reviewable
change with deterministic tests. Agents edit this package and its test suite
inside the demo worktree; reviewers verify the diff plus the test evidence.
"""

from .pricing import (
    apply_discount,
    line_total,
    round_money,
    shipping_cost,
    tax_rate,
)

__all__ = [
    "apply_discount",
    "line_total",
    "round_money",
    "shipping_cost",
    "tax_rate",
]

__version__ = "0.1.0"
