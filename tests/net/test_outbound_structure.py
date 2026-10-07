"""Outbound network access is built only in `tuppence.net`, behind the guard.

No other module may construct an HTTP client or transport, an urllib opener, an http.client
connection or a raw socket, or import a different HTTP library. The few local-only uses
outside `tuppence.net` are listed in ALLOWED with the reason they are safe.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tuppence"

FORBIDDEN_CALLS = {
    *(f"httpx.{n}" for n in ("Client", "AsyncClient", "HTTPTransport", "AsyncHTTPTransport")),
    *(f"httpx.{n}" for n in ("get", "post", "put", "patch", "delete", "head", "options")),
    "httpx.request",
    "httpx.stream",
    "urllib.request.urlopen",
    "urllib.request.urlretrieve",
    "urllib.request.build_opener",
    "urllib.request.install_opener",
    "urllib.request.Request",
    "urllib.request.OpenerDirector",
    "http.client.HTTPConnection",
    "http.client.HTTPSConnection",
    "socket.socket",
    "socket.create_connection",
    "socket.fromfd",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "ssl.wrap_socket",
}
FORBIDDEN_MODULES = {
    "aiohttp",
    "ftplib",
    "http.client",
    "httpcore",
    "requests",
    "smtplib",
    "urllib.request",
    "urllib3",
    "websocket",
    "websockets",
    "xmlrpc.client",
}

# (path under src/tuppence, dotted name) -> why it's allowed. Every entry must still be used.
ALLOWED = {
    ("desktop/launcher.py", "httpx.Client"): "polls /health of its own 127.0.0.1 server",
    ("desktop/launcher.py", "socket.socket"): "binds the loopback port the app serves on",
    ("cli.py", "socket.getaddrinfo"): "resolves the --host Tuppence will listen on",
    ("cli.py", "socket.socket"): "checks the port is free to listen on",
}


def _aliases(tree: ast.Module) -> dict[str, str]:
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    names[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    base = aliases.get(node.id, node.id)
    return ".".join([base, *reversed(parts)])


def _uses(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _aliases(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        else:
            mods = []
        for mod in mods:
            if any(mod == m or mod.startswith(m + ".") for m in FORBIDDEN_MODULES):
                found.append((f"import {mod}", node.lineno))
        if isinstance(node, ast.Call):
            # The callee, and anything handed over as a factory (partial(httpx.Client, ...)).
            for target in [node.func, *node.args, *(k.value for k in node.keywords)]:
                name = _dotted(target, aliases)
                if name in FORBIDDEN_CALLS:
                    found.append((name, node.lineno))
    return found


def test_only_tuppence_net_builds_outbound_connections():
    problems, used = [], set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("net/"):
            continue
        for name, line in _uses(path):
            if (rel, name) in ALLOWED:
                used.add((rel, name))
            else:
                problems.append(f"{rel}:{line}: {name}")
    assert problems == [], "outbound access outside tuppence.net:\n" + "\n".join(problems)
    assert used == set(ALLOWED), f"stale ALLOWED entries: {set(ALLOWED) - used}"


def test_the_scanner_spots_each_form(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import httpx as h\n"
        "from urllib import request\n"
        "from socket import create_connection\n"
        "import functools\n"
        "h.Client()\n"
        "request.urlopen('http://x')\n"
        "create_connection(('x', 80))\n"
        "functools.partial(h.AsyncClient, timeout=1)\n"
        "def f(c: h.Client) -> None: ...\n"  # an annotation is not a construction
    )
    names = sorted(name for name, _ in _uses(sample))
    assert names == [
        "httpx.AsyncClient",
        "httpx.Client",
        "import urllib.request",
        "socket.create_connection",
        "urllib.request.urlopen",
    ]
