"""Thin entrypoint for the AutoSRE webhook server.

Usage:
    python server.py
    uvicorn autosre.webhook:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import uvicorn

from autosre.config import AutoSREConfig
from autosre.webhook import create_app


def main() -> None:
    cfg = AutoSREConfig.from_env()
    app = create_app(cfg)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port)


if __name__ == "__main__":
    main()
