"""Poll <base>/health until it reports ok. Used by every packaging smoke test."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base", help="e.g. http://127.0.0.1:8040")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--expect-mode", default=None)
    args = p.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(args.base.rstrip("/") + "/health", timeout=3) as r:  # noqa: S310
                body = json.loads(r.read().decode())
            if body.get("status") == "ok" and (args.expect_mode in (None, body.get("mode"))):
                print(f"smoke: ok {body}")
                return 0
            last = f"unexpected body {body}"
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError) as exc:
            last = str(exc)
        time.sleep(1)
    print(f"smoke: FAILED after {args.timeout}s: {last}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
