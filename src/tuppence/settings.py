"""Runtime settings for one process: how and where it runs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Mode = Literal["local", "desktop", "server"]

_DEFAULT_HOST: dict[str, str] = {"local": "127.0.0.1", "desktop": "127.0.0.1", "server": "0.0.0.0"}  # noqa: S104
_DEFAULT_PORT: dict[str, int] = {"local": 8040, "desktop": 0, "server": 8040}


class RuntimeSettings(BaseModel):
    mode: Mode
    host: str
    port: int
    data_dir: Path
    web_dir: Path | None = None
    launch_token: str | None = None
    secure_cookies: bool = False

    @classmethod
    def for_mode(
        cls,
        mode: Mode,
        *,
        data_dir: Path,
        host: str | None = None,
        port: int | None = None,
        web_dir: Path | None = None,
        launch_token: str | None = None,
        secure_cookies: bool = False,
    ) -> RuntimeSettings:
        return cls(
            mode=mode,
            host=host or _DEFAULT_HOST[mode],
            port=_DEFAULT_PORT[mode] if port is None else port,
            data_dir=data_dir,
            web_dir=web_dir,
            launch_token=launch_token,
            secure_cookies=secure_cookies,
        )
