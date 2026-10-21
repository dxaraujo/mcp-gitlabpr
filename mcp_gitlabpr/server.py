"""Servidor MCP para merge requests do GitLab: listar, obter (com diffs), criar e mesclar.

API REST v4 com personal access token (header PRIVATE-TOKEN, escopo `api`). Só usa parâmetros que já existem na
GitLab CE 10.8: autor e responsável vão por id (`author_username` é de versões novas), resolvidos por username.

Sem `projeto`, ele vem do remote do git da pasta em que o MCP roda (origin, ou o primeiro remote).
"""
from __future__ import annotations

import functools
import ssl
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import GitlabConfig, default_path, load_config

mcp = MCPServer(
    "gitlabpr",
    instructions=(
        "Merge requests (MR, o 'PR' do GitLab). `projeto` é o caminho 'grupo/projeto' ou o id numérico; sem ele, vem "
        "do remote do repositório git da pasta atual e `origem` vem da branch atual. Fora de um repositório git a tool "
        "falha: pergunte ao usuário o projeto (e a branch de origem) e informe-os. `iid` é o número do MR dentro do "
        "projeto (o !123 da tela). Autor e responsável são usernames. Liste com "
        "gitlab_listar_mrs, detalhe (e veja os diffs) com gitlab_obter_mr, crie com gitlab_criar_mr e faça o merge com gitlab_mesclar_mr."
    ),
)

EXPECTED = (RuntimeError, OSError, LookupError, ValueError)
TIMEOUT = 60
LIMITE_TETO = 100
DIFF_MAX = 4000
ESTADOS = ("opened", "closed", "merged", "all")


def tool(fn):
    """Registra `fn` como tool (ToolError com o texto da exceção) e devolve `fn` intacta."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EXPECTED as exc:
            raise ToolError(str(exc)) from exc

    mcp.tool()(wrapper)
    return fn


# ---------------------------------------------------------------- HTTP

def ca_bundle() -> Path:
    """CAs extras (ex.: cadeia interna do GitLab), ao lado do gitlabpr.properties."""
    return default_path().parent / "gitlabpr-bundle.pem"


class _TlsAdapter(HTTPAdapter):
    """CAs do sistema + gitlabpr-bundle.pem, se existir."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        if ca_bundle().is_file():
            ctx.load_verify_locations(ca_bundle())
        super().init_poolmanager(*args, ssl_context=ctx, **kwargs)


class GitlabSession:
    def __init__(self, cfg: GitlabConfig):
        self.cfg = cfg
        self.api = f"{cfg.url}/api/v4"
        self.session = requests.Session()
        self.session.mount("https://", _TlsAdapter())
        self.session.headers.update({"PRIVATE-TOKEN": cfg.token, "Accept": "application/json"})

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self.api + path
        try:
            resp = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.exceptions.SSLError as exc:
            raise RuntimeError(
                f"Certificado do servidor não confiável em {url}. Grave a cadeia de certificados (PEM) em "
                f"{ca_bundle()} (ou aponte REQUESTS_CA_BUNDLE para ela) e reinicie o MCP. Detalhe: {exc}") from exc
        if resp.status_code == 401:
            raise RuntimeError(f"HTTP 401 em {method} {url}: token inválido ou expirado (precisa do escopo `api`).")
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code} em {method} {url}: {resp.text[:500].strip()}")
        return resp


_instance: GitlabSession | None = None
_lock = threading.Lock()


def get_session() -> GitlabSession:
    """Sessão única por processo; carrega o gitlabpr.properties na primeira chamada."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = GitlabSession(load_config())
        return _instance


def set_session(session: GitlabSession | None) -> None:
    """Substitui a sessão global (testes)."""
    global _instance
    _instance = session


# ---------------------------------------------------------------- helpers

# ponytail: usa o cwd do processo MCP; se o cliente não o iniciar na pasta do workspace, informe `projeto`
def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def _caminho_remoto(url: str) -> str:
    """'grupo/projeto' de https://host[/prefixo]/g/p.git, ssh://git@host:22/g/p.git ou git@host:g/p.git."""
    if "://" in url:
        path = urlsplit(url).path
        prefixo = urlsplit(get_session().cfg.url).path.rstrip("/")  # GitLab sob subcaminho (https://host/gitlab)
        if prefixo and path.startswith(prefixo + "/") and url.startswith(("http://", "https://")):
            path = path[len(prefixo):]
    else:
        path = url.partition(":")[2]
    path = path.strip("/")
    return path[:-4] if path.endswith(".git") else path


def _projeto(projeto: str | int | None) -> str:
    """'grupo/projeto' vai URL-encoded no path (grupo%2Fprojeto); id numérico passa como está; sem ele, o remote do git."""
    p = str(projeto).strip().strip("/") if projeto is not None else ""
    if not p:
        remotes = (_git("remote") or "").split()
        remote = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
        url = remote and _git("remote", "get-url", remote)
        if not url:
            raise ValueError("Pasta atual não é um repositório git com remote: pergunte ao usuário o projeto "
                             "('grupo/projeto' ou id numérico) e informe-o em `projeto`.")
        p = _caminho_remoto(url)
    return quote(p, safe="")


def _branch_atual() -> str:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        raise ValueError("Sem branch atual no git (fora de um repositório ou HEAD destacado): pergunte ao usuário a "
                         "branch de origem e informe-a em `origem`.")
    return branch


def _usuario_id(username: str) -> int:
    nome = username.strip().lstrip("@")
    users = get_session().request("GET", "/users", params={"username": nome}).json()
    if not users:
        raise LookupError(f"Usuário '{nome}' não encontrado no GitLab.")
    return users[0]["id"]


def _login(user: dict | None) -> str | None:
    return user and user.get("username")


def _resumo(mr: dict) -> dict:
    return {
        "iid": mr["iid"],
        "projeto_id": mr.get("project_id"),
        "titulo": mr.get("title"),
        "estado": mr.get("state"),
        "origem": mr.get("source_branch"),
        "destino": mr.get("target_branch"),
        "autor": _login(mr.get("author")),
        "responsavel": _login(mr.get("assignee")),
        "wip": mr.get("work_in_progress"),
        "merge_status": mr.get("merge_status"),
        "criado_em": mr.get("created_at"),
        "atualizado_em": mr.get("updated_at"),
        "url": mr.get("web_url"),
    }


# ---------------------------------------------------------------- tools

@tool
def gitlab_listar_mrs(projeto: str | None = None, todos_projetos: bool = False, estado: str = "opened",
                      busca: str | None = None, origem: str | None = None, destino: str | None = None,
                      autor: str | None = None, responsavel: str | None = None, limite: int = 20,
                      pagina: int = 1) -> dict:
    """Lista merge requests de `projeto` ('grupo/projeto' ou id; sem ele, o do repositório git atual) ou, com
    todos_projetos=True, de todos os projetos visíveis.

    estado: opened (padrão), closed, merged ou all. busca: texto no título/descrição.
    origem/destino: branches. autor/responsavel: usernames. limite ≤ 100 por página.
    """
    if estado not in ESTADOS:
        raise ValueError(f"estado inválido: '{estado}'. Use {', '.join(ESTADOS)}.")
    params = {"state": estado, "per_page": max(1, min(limite, LIMITE_TETO)), "page": max(1, pagina),
              "order_by": "updated_at", "sort": "desc"}
    if busca:
        params["search"] = busca
    if origem:
        params["source_branch"] = origem
    if destino:
        params["target_branch"] = destino
    if autor:
        params["author_id"] = _usuario_id(autor)
    if responsavel:
        params["assignee_id"] = _usuario_id(responsavel)
    if todos_projetos:
        path, params["scope"] = "/merge_requests", "all"  # padrão do endpoint global é created-by-me
    else:
        path = f"/projects/{_projeto(projeto)}/merge_requests"
    resp = get_session().request("GET", path, params=params)
    return {
        "total": int(resp.headers["X-Total"]) if resp.headers.get("X-Total") else None,
        "pagina": params["page"],
        "proxima_pagina": int(resp.headers["X-Next-Page"]) if resp.headers.get("X-Next-Page") else None,
        "itens": [_resumo(mr) for mr in resp.json()],
    }


@tool
def gitlab_obter_mr(iid: int, projeto: str | None = None, diffs: bool = False) -> dict:
    """Detalhes de um merge request (descrição, labels, merge); `projeto` padrão: o do repositório git atual.
    Com diffs=True, inclui os arquivos alterados e o diff de cada um (cortado em 4000 caracteres)."""
    base = f"/projects/{_projeto(projeto)}/merge_requests/{int(iid)}"
    mr = get_session().request("GET", base + ("/changes" if diffs else "")).json()
    out = _resumo(mr) | {
        "descricao": mr.get("description"),
        "labels": mr.get("labels"),
        "conflitos": mr.get("has_conflicts"),
        "remover_origem": mr.get("force_remove_source_branch"),
        "mesclado_por": _login(mr.get("merged_by")),
        "mesclado_em": mr.get("merged_at"),
        "sha": mr.get("sha"),
    }
    if diffs:
        out["arquivos"] = [{
            "caminho": c.get("new_path"),
            "caminho_anterior": c.get("old_path") if c.get("renamed_file") else None,
            "novo": c.get("new_file"),
            "removido": c.get("deleted_file"),
            "diff": c.get("diff", "")[:DIFF_MAX],
            "diff_cortado": len(c.get("diff", "")) > DIFF_MAX,
        } for c in mr.get("changes", [])]
    return out


@tool
def gitlab_criar_mr(titulo: str, destino: str, origem: str | None = None, projeto: str | None = None,
                    descricao: str | None = None, responsavel: str | None = None, labels: list[str] | None = None,
                    remover_origem: bool = False) -> dict:
    """Cria um merge request de `origem` (padrão: branch atual do git) para `destino` no `projeto` (padrão: o do
    repositório git atual). A branch de origem precisa estar no GitLab (git push). responsavel: username.
    remover_origem: apaga a branch de origem após o merge. Falha (HTTP 409) se já existe MR aberto entre as branches."""
    if not titulo.strip():
        raise ValueError("Informe `titulo`.")
    data = {"source_branch": origem or _branch_atual(), "target_branch": destino, "title": titulo,
            "remove_source_branch": remover_origem}
    if descricao:
        data["description"] = descricao
    if responsavel:
        data["assignee_id"] = _usuario_id(responsavel)
    if labels:
        data["labels"] = ",".join(labels)
    mr = get_session().request("POST", f"/projects/{_projeto(projeto)}/merge_requests", json=data).json()
    return _resumo(mr)


@tool
def gitlab_mesclar_mr(iid: int, projeto: str | None = None, sha: str | None = None,
                      remover_origem: bool | None = None, mensagem: str | None = None) -> dict:
    """Faz o merge de um merge request aberto. Antes confere que ele não está em WIP, não tem conflito e que o GitLab
    o considera mesclável. sha: o commit aprovado; se a branch recebeu commits depois, o GitLab recusa (HTTP 409).
    remover_origem: apaga a branch de origem (padrão: o que o MR já define). mensagem: do commit de merge."""
    base = f"/projects/{_projeto(projeto)}/merge_requests/{int(iid)}"
    mr = get_session().request("GET", base).json()
    if mr.get("state") != "opened":
        raise ValueError(f"MR !{iid} não está aberto (estado: {mr.get('state')}).")
    if mr.get("work_in_progress"):
        raise ValueError(f"MR !{iid} está em WIP/Draft: tire o prefixo antes do merge.")
    if mr.get("has_conflicts") or mr.get("merge_status") == "cannot_be_merged":
        raise ValueError(f"MR !{iid} tem conflito com {mr.get('target_branch')}: atualize a branch antes do merge.")
    data = {}
    if sha:
        data["sha"] = sha
    if remover_origem is not None:
        data["should_remove_source_branch"] = remover_origem
    if mensagem:
        data["merge_commit_message"] = mensagem
    mr = get_session().request("PUT", base + "/merge", json=data).json()
    return _resumo(mr) | {"mesclado_por": _login(mr.get("merged_by")), "mesclado_em": mr.get("merged_at"),
                          "sha_merge": mr.get("merge_commit_sha")}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
