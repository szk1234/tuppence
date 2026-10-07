# PyInstaller spec — onedir build of the Tuppence desktop app.
# Build: uv run --group build --extra desktop pyinstaller desktop/tuppence.spec --noconfirm --clean
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
WEB = ROOT / "src" / "tuppence" / "web_dist"
MIGRATIONS = ROOT / "src" / "tuppence" / "core" / "migrations"
if not (WEB / "index.html").is_file():
    raise SystemExit("Build the UI first: npm --prefix web ci && npm --prefix web run build")

hidden = collect_submodules("uvicorn") + collect_submodules("tuppence")
try:
    hidden += collect_submodules("webview")
except Exception:  # pywebview not installed: window falls back to the browser
    pass

a = Analysis(  # noqa: F821
    [str(ROOT / "desktop" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(WEB), "tuppence/web_dist"),
        (str(MIGRATIONS), "tuppence/core/migrations"),  # .sql files read via importlib.resources
    ],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
windowed = sys.platform in ("win32", "darwin")
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="Tuppence",
    console=not windowed, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Tuppence")  # noqa: F821
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Tuppence.app", bundle_identifier="org.tuppence.app")  # noqa: F821
