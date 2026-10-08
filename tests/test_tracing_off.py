"""Tracing is off whatever the environment says (R-M3-20): LangGraph would otherwise send
every run, statement text included, to LangSmith."""

import json
import os
import subprocess
import sys

PROBE = """
import json, os
import tuppence
import langsmith.utils as utils
print(json.dumps({
    "tracing": utils.tracing_is_enabled(),
    "endpoint": utils.get_api_url(None),
    "replicas": os.environ.get("LANGSMITH_RUNS_ENDPOINTS"),
}))
"""


def test_importing_tuppence_switches_tracing_off_whatever_the_environment_says():
    env = {
        **os.environ,
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_TRACING_V2": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_TRACING": "true",
        "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
        "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
        "LANGSMITH_API_KEY": "lsv2_pt_example",
        "LANGSMITH_RUNS_ENDPOINTS": '{"https://eu.api.smith.langchain.com": "key"}',
    }
    out = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result == {"tracing": False, "endpoint": "http://127.0.0.1:9", "replicas": None}


def test_the_test_session_has_tracing_off():
    import langsmith.utils

    langsmith.utils.get_env_var.cache_clear()
    assert langsmith.utils.tracing_is_enabled() is False
    assert langsmith.utils.get_api_url(None) == "http://127.0.0.1:9"


def test_a_legacy_tracing_switch_in_the_environment_cannot_break_a_run():
    """M6: LANGCHAIN_HANDLER (the old v1 tracing switch) makes langchain_core refuse every
    graph run. Importing tuppence removes it, so runs work and nothing is traced."""
    probe = (
        "import os, tuppence\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "from typing import TypedDict\n"
        "class S(TypedDict):\n"
        "    n: int\n"
        "g = StateGraph(S)\n"
        "g.add_node('a', lambda s: {'n': s['n'] + 1})\n"
        "g.add_edge(START, 'a')\n"
        "g.add_edge('a', END)\n"
        "print(g.compile().invoke({'n': 1})['n'], os.environ.get('LANGCHAIN_HANDLER'))\n"
    )
    env = {**os.environ, "LANGCHAIN_HANDLER": "langchain", "LANGCHAIN_TRACING": "true"}
    out = subprocess.run(
        [sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "2 None"
