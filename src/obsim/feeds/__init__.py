"""Feed adapters. Each turns one venue's wire format into neutral events."""

from .synthetic import Step, SyntheticVenue

__all__ = ["Step", "SyntheticVenue"]
