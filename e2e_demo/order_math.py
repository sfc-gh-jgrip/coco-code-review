"""Throwaway demo module used to verify pre-existing vs in-diff review routing.

This file is intentionally created on the E2E base branch. The PR branch will
modify ONLY ``apply_discount`` (introducing an in-diff bug) and will leave
``average_price`` untouched so its bug stays pre-existing relative to the diff.
"""


def average_price(prices):
    """Return the mean of a list of prices.

    NOTE: This has a latent divide-by-zero bug when ``prices`` is empty. It is
    deliberately left untouched by the PR so the reviewer must flag it as a
    pre-existing defect (outside the PR's changed lines).
    """
    total = 0
    for price in prices:
        total += price
    return total / len(prices)


def apply_discount(price, pct):
    """Return ``price`` reduced by ``pct`` percent.

    Reworked to express the discount as a divisor. This introduces an in-diff
    divide-by-zero bug when ``pct`` is 100 (full discount), which the reviewer
    should flag as an inline comment on these changed lines.
    """
    return price / (100 - pct)
