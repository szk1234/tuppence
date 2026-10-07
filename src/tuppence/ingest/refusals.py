"""Refusals the sandbox child can report by name.

The child process handles untrusted files, so nothing it says is trusted: an error crosses
the process boundary as the exception's class name only, and the parent looks the plain
message up here. A refusal has a fixed message (no file-dependent text), so the lookup
table is all the parent ever shows.
"""

from __future__ import annotations

from typing import ClassVar

from tuppence.core.errors import UserFacing

MESSAGES: dict[str, str] = {}


class Refusal(UserFacing):
    """Mixin for exceptions with a fixed, plain message, safe to show."""

    message: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "message" in cls.__dict__:
            MESSAGES[cls.__name__] = cls.message

    def __init__(self) -> None:
        super().__init__(self.message)  # type: ignore[call-arg]


class PdfPasswordProtected(Refusal, ValueError):
    message = "This PDF is password-protected. Remove the password and upload it again."


class PdfDamaged(Refusal, ValueError):
    message = "This PDF couldn't be read. The file may be damaged."


class PdfTooManyPages(Refusal, ValueError):
    message = (
        "This PDF has far more pages than Tuppence reads at once. "
        "Split it and upload the part you need."
    )


class PdfPageTooMuchText(Refusal, ValueError):
    message = "A page in this PDF has too much text to read safely."


class PdfPageTooManyWords(Refusal, ValueError):
    message = "A page in this PDF has too many words to read safely."


class PdfPageHasNoSize(Refusal, ValueError):
    message = "This PDF has a page with no size, so it can't be read."


class ImageTooLarge(Refusal, ValueError):
    message = (
        "This image is too large to read (over 40 megapixels). Make it smaller and upload it again."
    )


class ReplyTooLarge(Refusal, ValueError):
    message = "This file contains more text than Tuppence can handle."


class OutOfMemory(Refusal, MemoryError):
    message = "This file needs more memory to read than Tuppence allows."
