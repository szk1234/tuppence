"""Tiny synthetic PDFs that stress the extraction limits (no real data)."""

from __future__ import annotations

import zlib
from pathlib import Path


def _write(path: Path, objects: list[bytes]) -> Path:
    """objects[i] is the body of object i+1; object 1 is the catalogue."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))
    return path


def many_pages(path: Path, count: int) -> Path:
    kids = " ".join(f"{i} 0 R" for i in range(4, 4 + count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {count} /Kids [{kids}] >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"] * count
    return _write(path, objects)


def big_page(path: Path, points: int) -> Path:
    return _write(
        path,
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {points} {points}] >>".encode(),
        ],
    )


def word_flood(path: Path, words: int) -> Path:
    """One page whose content stream draws `words` short words."""
    ops = [b"BT /F1 1 Tf"]
    for i in range(words):
        ops.append(
            f"1 0 0 1 {(i % 100) * 5 + 10} {830 - (i // 100) % 800} Tm (w{i % 10}) Tj".encode()
        )
    ops.append(b"ET")
    stream = zlib.compress(b"\n".join(ops), 9)
    return _write(
        path,
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 1 /Kids [5 0 R] >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
            + stream
            + b"\nendstream",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 14400] /Contents 4 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>",
        ],
    )
