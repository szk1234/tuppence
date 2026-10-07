"""Errors shared by the domain core (no web-framework imports here)."""


class InputError(ValueError):
    """User input that's well-formed but not acceptable (HTTP 422)."""
