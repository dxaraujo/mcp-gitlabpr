"""Carrega URL e token do GitLab a partir de gitlabpr.properties."""
from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("url", "token")
TEMPLATE = "[DEFAULT]\nurl = https://gitlab.SEU-SERVIDOR\ntoken = SEU_PERSONAL_ACCESS_TOKEN"


@dataclass(frozen=True)
class GitlabConfig:
    url: str
    token: str = field(repr=False)


def default_path() -> Path:
    if env := os.environ.get("MCP_GITLABPR_CONFIG"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "mcp-gitlabpr" / "gitlabpr.properties"
    return Path.home() / ".config" / "mcp-gitlabpr" / "gitlabpr.properties"


def load_config(path: Path | None = None) -> GitlabConfig:
    path = path or default_path()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de credenciais não encontrado: {path}\nCrie-o com:\n{TEMPLATE}")
    # interpolation=None: tokens podem conter '%'
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ValueError(f"{path} inválido: {exc}") from exc
    section = parser.defaults()
    missing = [k for k in REQUIRED if not section.get(k, "").strip()]
    if missing:
        raise ValueError(f"Chave(s) ausente(s) em {path} [DEFAULT]: {', '.join(missing)}")
    return GitlabConfig(url=section["url"].strip().rstrip("/"), token=section["token"].strip())
