import json
import subprocess

import pytest
import requests
from requests.adapters import BaseAdapter

from mcp_gitlabpr import config, server
from mcp_gitlabpr.config import GitlabConfig

API = "https://gitlab.test/api/v4"
MR = {"iid": 7, "project_id": 3, "title": "Ajuste", "state": "opened", "source_branch": "feat", "target_branch": "dev",
      "author": {"username": "joao"}, "assignee": None, "web_url": "https://gitlab.test/g/p/merge_requests/7"}


class FakeAdapter(BaseAdapter):
    """routes: {(METODO, url_sem_query): (status, body, headers?)}."""

    def __init__(self):
        super().__init__()
        self.routes = {}
        self.calls: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        status, body, *headers = self.routes.get((request.method, request.url.split("?")[0]), (404, "not found"))
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode()
        resp.headers.update(headers[0] if headers else {})
        resp.url, resp.request = request.url, request
        return resp

    def close(self):
        pass


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


@pytest.fixture
def fake(tmp_path_factory, monkeypatch):
    """Sessão falsa, rodando num repositório git cujo origin é o projeto g/p do GitLab de teste."""
    repo = tmp_path_factory.mktemp("repo")
    monkeypatch.chdir(repo)
    git("init", "-q", "-b", "feat")
    git("remote", "add", "origin", "git@gitlab.test:g/p.git")
    adapter = FakeAdapter()
    session = server.GitlabSession(GitlabConfig(url="https://gitlab.test", token="tok"))
    session.session.mount("https://", adapter)
    server.set_session(session)
    yield adapter
    server.set_session(None)


def test_config(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load_config(tmp_path / "x")
    p = tmp_path / "gitlabpr.properties"
    p.write_text("[DEFAULT]\nurl = https://gitlab.test/\n")
    with pytest.raises(ValueError, match="token"):
        config.load_config(p)
    p.write_text("[DEFAULT]\nurl = https://gitlab.test/\ntoken = a%b\n")
    assert config.load_config(p) == GitlabConfig(url="https://gitlab.test", token="a%b")


def test_listar_projeto(fake):
    fake.routes[("GET", f"{API}/users")] = (200, [{"id": 42}])
    fake.routes[("GET", f"{API}/projects/g%2Fp/merge_requests")] = (200, [MR], {"X-Total": "1"})
    out = server.gitlab_listar_mrs(autor="@joao", destino="dev", limite=500)
    req = fake.calls[-1]
    assert req.headers["PRIVATE-TOKEN"] == "tok"
    assert "author_id=42" in req.url and "target_branch=dev" in req.url and "per_page=100" in req.url
    assert out["total"] == 1 and out["proxima_pagina"] is None
    assert out["itens"][0] | {} == server._resumo(MR) and out["itens"][0]["autor"] == "joao"


def test_listar_estado_invalido(fake):
    with pytest.raises(ValueError):
        server.gitlab_listar_mrs(estado="aberto")


def test_obter_com_diffs(fake):
    change = {"new_path": "a.py", "old_path": "a.py", "diff": "x" * 5000}
    fake.routes[("GET", f"{API}/projects/g%2Fp/merge_requests/7/changes")] = (200, MR | {"changes": [change]})
    out = server.gitlab_obter_mr(7, diffs=True)
    assert out["arquivos"][0]["diff_cortado"] and len(out["arquivos"][0]["diff"]) == server.DIFF_MAX


def test_criar(fake):
    fake.routes[("GET", f"{API}/users")] = (200, [{"id": 9}])
    fake.routes[("POST", f"{API}/projects/g%2Fp/merge_requests")] = (201, MR)
    out = server.gitlab_criar_mr("Ajuste", "dev", origem="feat", responsavel="maria", labels=["a", "b"])
    assert json.loads(fake.calls[-1].body) == {"source_branch": "feat", "target_branch": "dev", "title": "Ajuste",
                                               "remove_source_branch": False, "assignee_id": 9, "labels": "a,b"}
    assert out["iid"] == 7


def test_erros(fake):
    fake.routes[("GET", f"{API}/users")] = (200, [])
    with pytest.raises(LookupError):
        server.gitlab_criar_mr("t", "dev", origem="feat", responsavel="ninguem")
    fake.routes[("POST", f"{API}/projects/g%2Fp/merge_requests")] = (409, {"message": ["Another open merge request"]})
    with pytest.raises(RuntimeError, match="409"):
        server.gitlab_criar_mr("t", "dev", origem="feat")
    fake.routes[("GET", f"{API}/projects/g%2Fp/merge_requests/1")] = (401, {"message": "401 Unauthorized"})
    with pytest.raises(RuntimeError, match="token"):
        server.gitlab_obter_mr(1)


def test_mesclar(fake):
    base = f"{API}/projects/g%2Fp/merge_requests/7"
    fake.routes[("GET", base)] = (200, MR | {"merge_status": "can_be_merged"})
    fake.routes[("PUT", base + "/merge")] = (200, MR | {"state": "merged", "merged_by": {"username": "ana"},
                                                        "merge_commit_sha": "abc"})
    out = server.gitlab_mesclar_mr(7, sha="f00", remover_origem=True)
    assert json.loads(fake.calls[-1].body) == {"sha": "f00", "should_remove_source_branch": True}
    assert out["estado"] == "merged" and out["mesclado_por"] == "ana" and out["sha_merge"] == "abc"


@pytest.mark.parametrize("extra, erro", [({"state": "merged"}, "aberto"), ({"work_in_progress": True}, "WIP"),
                                         ({"has_conflicts": True}, "conflito"),
                                         ({"merge_status": "cannot_be_merged"}, "conflito")])
def test_mesclar_recusa(fake, extra, erro):
    fake.routes[("GET", f"{API}/projects/g%2Fp/merge_requests/7")] = (200, MR | extra)
    with pytest.raises(ValueError, match=erro):
        server.gitlab_mesclar_mr(7)
    assert not any(c.method == "PUT" for c in fake.calls)


@pytest.mark.parametrize("url", ["https://gitlab.test/g/sub/p.git", "git@gitlab.test:g/sub/p.git",
                                 "ssh://git@gitlab.test:2222/g/sub/p.git", "https://gitlab.test/g/sub/p"])
def test_caminho_remoto(fake, url):
    assert server._caminho_remoto(url) == "g/sub/p"


def test_caminho_remoto_subcaminho(fake):
    server.set_session(server.GitlabSession(GitlabConfig(url="https://host/gitlab", token="t")))
    assert server._caminho_remoto("https://host/gitlab/g/p.git") == "g/p"


@pytest.mark.parametrize("url", ["https://github.com/g/p.git", "git@github.com:g/p.git",
                                 "ssh://git@github.com/g/p.git"])
def test_remote_de_outro_host(fake, url):
    with pytest.raises(ValueError, match="não é do GitLab configurado"):
        server._caminho_remoto(url)


def test_projeto_e_branch_do_git(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="raiz do workspace"):
        server.gitlab_listar_mrs()
    with pytest.raises(ValueError, match="pergunte ao usuário a branch"):
        server.gitlab_criar_mr("t", "dev")
    git("init", "-q", "-b", "feat")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    git("remote", "add", "origin", "git@gitlab.test:g/p.git")
    fake.routes[("POST", f"{API}/projects/g%2Fp/merge_requests")] = (201, MR)
    server.gitlab_criar_mr("t", "dev")
    assert json.loads(fake.calls[-1].body)["source_branch"] == "feat"


def test_pasta_do_workspace(fake, tmp_path, monkeypatch):
    """Com `pasta`, o projeto vem do repositório dela, não do cwd (o Kiro não inicia o MCP de um power no workspace)."""
    repo = tmp_path / "ws"
    repo.mkdir()
    monkeypatch.chdir(repo)
    git("init", "-q", "-b", "feat")
    git("remote", "add", "origin", "https://gitlab.test/outro/ws.git")
    monkeypatch.chdir(tmp_path)
    fake.routes[("GET", f"{API}/projects/outro%2Fws/merge_requests")] = (200, [])
    server.gitlab_listar_mrs(pasta=str(repo))
    assert "/projects/outro%2Fws/" in fake.calls[-1].url
    with pytest.raises(ValueError, match="não existe"):
        server.gitlab_listar_mrs(pasta=str(tmp_path / "nada"))


@pytest.mark.parametrize("url", ["https://gitlab.test/g/p", "https://gitlab.test/g/p/", "https://gitlab.test/g/p.git",
                                 "https://gitlab.test/g/p/merge_requests/7", "https://gitlab.test/g/p/-/tree/dev"])
def test_url_projeto_colada(fake, tmp_path, monkeypatch, url):
    """Fora de um repositório, a pessoa cola a URL do projeto; vale a de um MR ou de outra página dele."""
    monkeypatch.chdir(tmp_path)
    fake.routes[("GET", f"{API}/projects/g%2Fp/merge_requests")] = (200, [])
    server.gitlab_listar_mrs(url_projeto=url)
    assert "/projects/g%2Fp/merge_requests" in fake.calls[-1].url


def test_url_projeto_recusada(fake):
    for url in ("https://github.com/g/p", "g/p", "123"):
        with pytest.raises(ValueError):
            server.gitlab_listar_mrs(url_projeto=url)
    with pytest.raises(ValueError, match="branch de origem"):
        server.gitlab_criar_mr("t", "dev", url_projeto="https://gitlab.test/g/p")
