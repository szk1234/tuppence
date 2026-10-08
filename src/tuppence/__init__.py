"""Tuppence: a private AI money coach for UK households."""

import os as _os
import sys as _sys

__version__ = "0.2.0.dev1"

# LangGraph sends every run, statement text included, to LangSmith when the environment turns
# tracing on. Tuppence never traces. This runs before langgraph or langsmith can be imported
# (every entry point imports this package first): tracing is switched off and any LangSmith
# endpoint points at a closed port on this machine, whatever the environment says. The ingest
# service also runs every graph inside `langsmith.tracing_context(enabled=False)`.
NO_TRACING_ENDPOINT = "http://127.0.0.1:9"
TRACING_OFF = {
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_ENDPOINT": NO_TRACING_ENDPOINT,
    "LANGCHAIN_ENDPOINT": NO_TRACING_ENDPOINT,
}


def disable_tracing() -> None:
    """Force LangSmith tracing off for this process (and the processes it starts)."""
    _os.environ.update(TRACING_OFF)
    # Extra destinations, and the legacy v1 switch (langchain_core refuses every run with it).
    for name in ("LANGSMITH_RUNS_ENDPOINTS", "LANGCHAIN_RUNS_ENDPOINTS", "LANGCHAIN_HANDLER"):
        _os.environ.pop(name, None)
    utils = _sys.modules.get("langsmith.utils")
    if utils is not None:  # already imported: forget the environment it read
        utils.get_env_var.cache_clear()


disable_tracing()
