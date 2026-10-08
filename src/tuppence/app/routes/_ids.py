"""Shapes shared by the understanding routes: ids and version numbers are checked before
they reach a query, so a malformed one is a plain 422 and an unknown one a 404."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query
from pydantic import Field

ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,80}$"

# A path id (a transaction, category, rule, commitment or refile).
PathId = Annotated[str, Path(pattern=ID_PATTERN)]
# An id inside a request body or the query string.
BodyId = Annotated[str, Field(pattern=ID_PATTERN)]
# The version a person last saw. Bounded so it always fits a database integer.
ExpectedVersion = Annotated[int, Field(ge=0, le=1_000_000_000)]
# An optional id filter in the query string.
QueryId = Annotated[str | None, Query(pattern=ID_PATTERN)]
