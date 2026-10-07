"""On-device OCR with RapidOCR (Apache-2.0, CPU). Runs inside the sandbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tuppence.ingest.layout_rows import Box, rows_from_boxes

MAX_PIXELS = 40_000_000
MAX_SIDE = 3000
_engine: Any = None


def _ocr_engine() -> Any:
    global _engine
    if _engine is None:
        import rapidocr
        from rapidocr import RapidOCR

        # Explicit paths to the models inside the wheel, so RapidOCR's downloader (the only
        # code in it that touches the network) can never run.
        models = Path(rapidocr.__file__).parent / "models"
        _engine = RapidOCR(
            params={
                "Global.log_level": "error",
                "Det.model_path": str(models / "PP-OCRv6_det_small.onnx"),
                "Cls.model_path": str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
                "Rec.model_path": str(models / "PP-OCRv6_rec_small.onnx"),
            }
        )
    return _engine


def ocr_image(image: Any) -> tuple[list[str], float]:
    """(rows, mean confidence) for a PIL image."""
    import numpy as np

    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    result = _ocr_engine()(np.asarray(image))
    if result.boxes is None or result.txts is None:
        return [], 0.0
    boxes = []
    for points, text in zip(result.boxes, result.txts, strict=True):
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        boxes.append(Box(str(text), min(xs), min(ys), max(xs), max(ys)))
    scores = [float(s) for s in (result.scores or [])]
    return rows_from_boxes(boxes), (sum(scores) / len(scores)) if scores else 0.0


def image_rows(path: str) -> dict[str, Any]:
    """{"rows": [...], "ocr_confidence": float} for a PNG or JPEG file."""
    import warnings

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            image.load()
            rows, confidence = ocr_image(image)
    return {"rows": rows, "ocr_confidence": confidence}
