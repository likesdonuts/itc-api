"""Filesystem layout, feed URLs, and credential loading for the data layer."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "documents"
IDS_DIR = DATA_DIR / "ids"
SITE_DIR = ROOT / "site"

ENV_PATH = ROOT / ".env"

# The mapping from IDS field names to what the site shows. Lives at the top
# level, in plain JSON, because it is meant to be edited.
SCHEMA_PATH = ROOT / "ui_schema.json"

RSS_URL = "https://edis.usitc.gov/external/rss/render.rss?criteria=CRITERIONAOIDEL:8:13:CRITERIONANOTIFY:true"

TOKEN_HELP = (
    "Missing EDIS_TOKEN. Put it in a .env file at the repository root:\n"
    "  EDIS_TOKEN=<your token>\n"
    "Tokens come from https://edis.usitc.gov -> profile -> API Token Generator."
)


class MissingTokenError(RuntimeError):
    pass


def load_token(env_path: Path = ENV_PATH) -> str:
    """Read EDIS_TOKEN from .env. Only the data layer ever needs this; the UI
    layer renders from what is already on disk.
    """
    from .client import load_env

    token = load_env(env_path).get("EDIS_TOKEN")
    if not token:
        raise MissingTokenError(TOKEN_HELP)
    return token
