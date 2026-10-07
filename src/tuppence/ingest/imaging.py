"""Image size limits and clean re-encoding. Runs inside the sandbox.

Every image that is read or rendered obeys one pixel cap, and every image that leaves for
a vision model is re-encoded first so that no camera, location or colour-profile metadata
goes with it.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

from tuppence.core.errors import UserFacing

MAX_PIXELS = 40_000_000
MAX_VISION_SIDE = 2000
JPEG_QUALITY = 85


class ImageRefused(UserFacing, ValueError):
    """An image or page too big to read safely. The message is safe to show."""


def render_scale(width: float, height: float, dpi: int) -> float:
    """The pdfium scale for a page of `width` x `height` points: `dpi`, lowered so the
    rendered image never exceeds MAX_PIXELS."""
    scale = dpi / 72
    if width <= 0 or height <= 0:
        raise ImageRefused("This PDF has a page with no size, so it can't be read.")
    if width * height * scale * scale > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / (width * height)) * 0.999
    return scale


def open_checked(path: str) -> Any:
    """Open and fully load an image, refusing anything over MAX_PIXELS, with its EXIF
    orientation applied."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 2  # Pillow's own bomb error is a second line
    try:
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ImageRefused(
                    f"This image is too large to read (over {MAX_PIXELS // 1_000_000} "
                    "megapixels). Make it smaller and upload it again."
                )
            image.load()
            return ImageOps.exif_transpose(image)
    except Image.DecompressionBombError:
        raise ImageRefused(
            "This image is too large to read. Make it smaller and upload it again."
        ) from None


def encode_for_vision(image: Any, *, jpeg: bool = False) -> tuple[bytes, str]:
    """(bytes, media type): at most MAX_VISION_SIDE px on the long side, pixels only.

    The new image is built from raw pixel data, so no EXIF, GPS, XMP, ICC or text chunk
    can come along.
    """
    from PIL import Image

    if image.mode in ("RGBA", "LA", "P"):
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = flat
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > MAX_VISION_SIDE:
        image = image.copy()
        image.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE))
    clean = Image.frombytes(image.mode, image.size, image.tobytes())
    out = io.BytesIO()
    if jpeg:
        clean.save(out, "JPEG", quality=JPEG_QUALITY)
        return out.getvalue(), "image/jpeg"
    clean.save(out, "PNG")
    return out.getvalue(), "image/png"


def vision_image(path: str) -> tuple[bytes, str]:
    """An uploaded image, checked and re-encoded for the vision model."""
    suffix = Path(path).suffix.lower()
    return encode_for_vision(open_checked(path), jpeg=suffix in {".jpg", ".jpeg"})
