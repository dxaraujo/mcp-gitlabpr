"""Carrega URL, token e certificado do GitLab das variáveis de ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("GITLAB_API_URL", "GITLAB_API_TOKEN")


@dataclass(frozen=True)
class GitlabConfig:
    url: str
    token: str = field(repr=False)


def ca_bundle() -> Path | None:
    """Cadeia PEM extra apontada por GITLAB_CA_BUNDLE, somada às CAs da máquina."""
    valor = os.environ.get("GITLAB_CA_BUNDLE", "").strip()
    if not valor:
        return None
    path = Path(valor).expanduser()
    if not path.is_file():
        raise ValueError(f"GITLAB_CA_BUNDLE aponta para um arquivo que não existe: {path}")
    return path


def load_config() -> GitlabConfig:
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        raise ValueError(f"Variável(is) de ambiente ausente(s): {', '.join(missing)}")
    ca_bundle()  # arquivo errado aparece já na carga
    url = os.environ["GITLAB_API_URL"].strip().rstrip("/").removesuffix("/api/v4")
    return GitlabConfig(url=url, token=os.environ["GITLAB_API_TOKEN"].strip())
