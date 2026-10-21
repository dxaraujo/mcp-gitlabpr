# mcp-gitlabpr

Servidor MCP para **merge requests** do GitLab (o "PR" do GitLab): listar, obter (com diffs), criar e mesclar. Usa a API REST
v4 com personal access token e só os parâmetros que já existem na **GitLab CE 10.8**
([doc da 10.8.7](https://gitlab.com/gitlab-org/gitlab-foss/-/blob/v10.8.7/doc/api/merge_requests.md)), então também
funciona em versões mais novas.

## Credenciais

| SO | Arquivo |
|---|---|
| Linux/Mac | `~/.config/mcp-gitlabpr/gitlabpr.properties` |
| Windows | `%APPDATA%\mcp-gitlabpr\gitlabpr.properties` |

Para usar outro caminho, defina a variável de ambiente `MCP_GITLABPR_CONFIG`.

```ini
[DEFAULT]
url = https://gitlab.SEU-SERVIDOR
token = SEU_PERSONAL_ACCESS_TOKEN
```

O token sai de *Settings → Access Tokens* com o escopo `api` e vai no header `PRIVATE-TOKEN`.

**Certificados:** o MCP confia nas CAs da máquina e, se existir, em `gitlabpr-bundle.pem` na mesma pasta do
`gitlabpr.properties` (cadeia PEM do servidor). `REQUESTS_CA_BUNDLE` também funciona.

## Instalação

```json
{
  "mcpServers": {
    "gitlabpr": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-gitlabpr@latest"],
      "env": { "UV_SYSTEM_CERTS": "true" }
    }
  }
}
```

Desenvolvimento:

```bash
uv sync
uv run pytest
uv run mcp dev mcp_gitlabpr/server.py   # MCP Inspector
```

## Tools

`projeto` é o caminho `grupo/projeto` (o MCP faz o URL-encode) ou o id numérico. **Sem `projeto`**, ele vem do remote
do git da pasta em que o MCP roda (`origin`, ou o primeiro remote; aceita `https://`, `ssh://` e `git@host:`), e
`origem` vem da branch atual. Fora de um repositório git a tool falha pedindo que o agente pergunte ao usuário. O
cliente MCP precisa iniciar o servidor na pasta do workspace; se não iniciar, informe `projeto`. `iid` é o número do MR no projeto
(o `!123` da tela). Autor e responsável são usernames: o MCP os converte em id por `GET /users?username=`, porque a 10.8
só filtra por `author_id`/`assignee_id`.

| Tool | Para quê |
|---|---|
| `gitlab_listar_mrs(projeto?, todos_projetos=false, estado=opened, busca?, origem?, destino?, autor?, responsavel?, limite≤100, pagina=1)` | MRs do projeto ou, com `todos_projetos`, de todos os visíveis (`scope=all`). Devolve `total`, `proxima_pagina` e os itens resumidos |
| `gitlab_obter_mr(iid, projeto?, diffs=false)` | Detalhes do MR (descrição, labels, conflitos, merge). Com `diffs`, os arquivos alterados e o diff de cada um, cortado em 4000 caracteres |
| `gitlab_criar_mr(titulo, destino, origem?, projeto?, descricao?, responsavel?, labels?, remover_origem=false)` | Cria o MR; a branch de origem precisa já estar no GitLab (`git push`). Se já existe um MR aberto entre as mesmas branches, o GitLab responde 409 |
| `gitlab_mesclar_mr(iid, projeto?, sha?, remover_origem?, mensagem?)` | Faz o merge de um MR aberto; recusa antes de chamar o GitLab se ele está em WIP, tem conflito ou não é mesclável. Com `sha`, o GitLab recusa (409) se a branch recebeu commits depois desse commit |

`estado` aceita `opened`, `closed`, `merged` ou `all`.
