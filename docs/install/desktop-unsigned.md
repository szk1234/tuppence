# The desktop app

No desktop builds have been published yet. You can build the app yourself from a clone of this
repository; it's what CI does on every push for Windows, macOS and Linux. You need
[uv](https://docs.astral.sh/uv/) and Node.js 22.

```bash
git clone https://github.com/szk1234/tuppence && cd tuppence
npm --prefix web ci && npm --prefix web run build
uv sync --locked --group build --extra desktop
uv run python scripts/build_desktop.py
```

The script builds the app with PyInstaller and starts it once to check it works. You'll find
it in `dist/`:
- **Windows:** `dist\Tuppence\Tuppence.exe`;
- **macOS:** `dist/Tuppence.app`;
- **Linux:** `dist/Tuppence/Tuppence`. The window needs GTK and WebKitGTK
  (`gir1.2-webkit2-4.1` on Debian and Ubuntu); without them Tuppence opens in your browser
  instead.

The app isn't code-signed (signing arrives with v1.0). The desktop app listens only on your own
computer (127.0.0.1) and signs you in with a one-time link, so nobody else on your network can
reach it.
