"""The test oracle for R-M3-23 (a): no number of six or more digits from a printed line survives
in anything sent, nor the last four digits of one. Written apart from `sensitive` on purpose, so
the guard doesn't share a blind spot with the code it guards: its reading of a join, of money
and of a line's own date is its own (re-review 2 R5 and the coordinator's structural rule).

A number is digits joined by up to three characters at a time that are not letters or digits:
punctuation, symbols or spaces ("20-11-33 87654321", "1234/5678", "8765 | 4321", "8765~4321",
"8765 _ 4321"), whatever letters it is glued to ("ACC87654321"). Characters a scan reads for
digits (O, S, l, I, |, B, Z) count as digits inside a mostly-digit token ("876S4321"), never in a
word ("BOOTS"). Only two things are not numbers:
- a well-formed money token: digits, one point and two decimals, commas only between groups of
  three (12.30, 1234.56, 1,234.56); or a currency sign and whole pounds grouped that way (£250);
- the line's own date at its start (after a day name), and a second date right after it: a
  named month or a four-digit year always, a two-digit year with digits for its month (20/11/33,
  20.11.33, 20 11 33) only when no group of four or more digits is joined after it, unless
  another date is what follows. Four digits after a named month are its year only when they read
  1990 to 2099 with no other group joined after them ("02 Oct 8765 4321" is a date and a
  number)."""

from __future__ import annotations

import contextlib
import re
import unicodedata

MIN_DIGITS = 6
TAIL = 4
_LOOKALIKE = str.maketrans("OoSslI|BZz", "0055111822")
_TOKEN = re.compile(r"[A-Za-z0-9|]+")
_MONEY = re.compile(
    r"[£$€]\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?![\d,])"
    r"|(?<![\d,])(?<!\d\.)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d]|[.,]\d)"
)
_NUMBER = re.compile(r"\d(?:[\W_]{0,3}\d)*")
_JOINED = re.compile(r"[\W_]{1,3}(\d(?:[\W_]{0,3}\d)*)")
_DAY = re.compile(r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+", re.IGNORECASE)
_NUMERIC = re.compile(r"(\d{1,2})([/.\- ])(\d{1,2})\2(\d{4}|\d{2})(?!\d)")
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NAMED = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?[ -]?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\.?(?P<year>[ -]?(?P<digits>\d{4}|\d{2})(?!\d))?",
    re.IGNORECASE,
)


def _digitish(token: str) -> str:
    """A token as a scan's digits: in each piece between its words (two or more letters, one
    of them not a look-alike: "SMITH", "RENT"), look-alikes read as digits when at least half
    the piece is digits."""
    if not any(ch.isdigit() for ch in token):
        return token
    looks = set("OoSslI|BZz")
    out, piece = [], ""
    for chunk in re.split(r"([A-Za-z|]+)", token):
        is_word = len(chunk) >= 2 and chunk.isascii() and any(c not in looks for c in chunk)
        if is_word and chunk[0].isalpha():
            out.append(_as_digits(piece))
            out.append(chunk)
            piece = ""
        else:
            piece += chunk
    out.append(_as_digits(piece))
    return "".join(out)


def _as_digits(piece: str) -> str:
    if piece and 2 * sum(ch.isdigit() for ch in piece) >= len(piece):
        return piece.translate(_LOOKALIKE)
    return piece


def fold(text: str) -> str:
    """Digits in any script or style as ASCII digits; invisible and enclosing marks gone;
    look-alikes in mostly-digit tokens read as digits."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if unicodedata.category(ch) in ("Cf", "Mn", "Me"):
            continue
        if ch.isdigit() and not ch.isascii():
            with contextlib.suppress(TypeError, ValueError):
                ch = str(unicodedata.digit(ch))
        out.append(ch)
    text = " ".join("".join(out).split())
    return _TOKEN.sub(lambda m: _digitish(m.group(0)), text)


def _date_at(text: str, at: int) -> tuple[int, bool] | None:
    """(end, shaped like a sort code) of a real date starting at `at`, else None."""
    if (m := _NUMERIC.match(text, at)) and 1 <= int(m[1]) <= 31 and 1 <= int(m[3]) <= 12:
        return m.end(), len(m[4]) == 2
    if (m := _ISO.match(text, at)) and 1950 <= int(m[1]) <= 2099 and 1 <= int(m[2]) <= 12:
        return m.end(), False
    if (m := _NAMED.match(text, at)) and 1 <= int(m[1]) <= 31:
        if m["year"] and not _a_year(text, m["digits"], m.end()):
            return m.start("year"), False  # the date is "02 Oct"; a number follows
        return m.end(), False
    return None


def _a_year(text: str, digits: str, end: int) -> bool:
    """Four digits after a named month are its year only when they read 1990 to 2099 and no
    group of digits is joined after them, unless another date starts there."""
    if len(digits) == 2:
        return True
    if not 1990 <= int(digits) <= 2099:
        return False
    if _number_after(text, end) == 0:
        return True
    after = end + 1
    return any(p.match(text, after) for p in (_NUMERIC, _ISO, _NAMED))


def _number_after(text: str, at: int) -> int:
    match = _JOINED.match(text, at)
    return sum(ch.isdigit() for ch in match[1]) if match else 0


def own_dates(text: str) -> tuple[int, int] | None:
    """The span of the line's own date or dates at its start (see above), or None."""
    day = _DAY.match(text)
    start = day.end() if day else 0
    first = _date_at(text, start)
    if first is None:
        return None
    end, shaped = first
    second = _date_at(text, end + 1) if text[end : end + 1] == " " else None
    if second is not None and (not second[1] or _number_after(text, second[0]) < 4):
        return start, second[0]
    if second is not None or not shaped or _number_after(text, end) < 4:
        return start, end
    return None


def _cover(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` with each span replaced by letters, which join nothing."""
    chars = list(text)
    for start, end in spans:
        chars[start:end] = "x" * (end - start)
    return "".join(chars)


def _without_money(text: str) -> str:
    return _cover(text, [m.span() for m in _MONEY.finditer(text)])


def _plain(text: str) -> str:
    """`text` folded, with its money tokens and its own dates covered."""
    text = _without_money(fold(text))
    own = own_dates(text)
    return _cover(text, [own] if own else [])


def _numbers(plain: str) -> list[str]:
    return [re.sub(r"\D", "", m.group(0)) for m in _NUMBER.finditer(plain)]


def runs(text: str) -> list[str]:
    """The digits of each number of six or more in `text`, money and its own date left out."""
    return [r for r in _numbers(_plain(text)) if len(r) >= MIN_DIGITS]


def survivors(printed: str, sent: str) -> list[str]:
    """Numbers of `printed` of which six digits in a row, or the last four, still appear among
    the digits of `sent` (each of its numbers read whole, money left out). What is sent keeps
    the leading-date exemption only for the very dates the printed line starts with."""
    printed_text = _without_money(fold(printed))
    shown_text = _without_money(fold(sent))
    own = own_dates(printed_text)
    if own and shown_text.startswith(printed_text[: own[1]]):
        shown_text = _cover(shown_text, [own])
    shown = _numbers(shown_text)
    leaked = []
    for run in runs(printed):
        windows = {run[i : i + MIN_DIGITS] for i in range(len(run) - MIN_DIGITS + 1)}
        if any(w in s for w in windows for s in shown) or any(run[-TAIL:] in s for s in shown):
            leaked.append(run)
    return leaked
