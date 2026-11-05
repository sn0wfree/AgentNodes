"""Agent output validators (truthfulness L2).

Currently contains the claim validator — a structural check that
quantitative claims in an assistant's final answer can be traced to
actual tool results from the same conversation.
"""
from .claim_validator import (
    DEFAULT_METRIC_KEYWORDS,
    ClaimValidationResult,
    validate_claims,
)

__all__ = [
    "ClaimValidationResult",
    "DEFAULT_METRIC_KEYWORDS",
    "validate_claims",
]
