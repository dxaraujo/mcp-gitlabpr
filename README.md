# mcp-gitlabpr

Servidor MCP para **merge requests** do GitLab (o "PR" do GitLab): listar, obter (com diffs), criar e mesclar. Usa a API REST
v4 com personal access token e só os parâmetros que já existem na **GitLab CE 10.8**
([doc da 10.8.7](https://gitlab.com/gitlab-org/gitlab-foss/-/blob/v10.8.7/doc/api/merge_requests.md)), então também
funciona em versões mais novas.

## Configuração

Só por variáveis de ambiente, no `env` do servidor MCP:

| Variável | Obrigatória | O que é |
|---|---|---|
| `GITLAB_API_URL` | sim | URL do GitLab (com ou sem `/api/v4`) |
| `GITLAB_API_TOKEN` | sim | Personal access token com o escopo `api`, enviado no header `PRIVATE-TOKEN` (sai de *Settings → Access Tokens*) |
| `GITLAB_CA_BUNDLE` | não | Caminho de um arquivo PEM com a cadeia do servidor, somada às CAs da máquina |

## Instalação

```json
{
  "mcpServers": {
    "gitlabpr": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-gitlabpr@latest"],
      "env": {
        "GITLAB_API_URL": "https://gitlab.SEU-SERVIDOR",
        "GITLAB_API_TOKEN": "${GITLAB_API_TOKEN}",
        "UV_SYSTEM_CERTS": "true"
      }
    }
  }
}
```

**Como passar as variáveis:** defina-as no bloco `env` do servidor MCP, como no exemplo. Para não gravar segredos em
texto puro, use referências: `${VAR}` no Kiro IDE, `${env:VAR}` no Kiro CLI e `{env:VAR}` no opencode, e deixe o valor
no gerenciador de segredos ou no ambiente de onde a ferramenta é aberta. Não há arquivo de configuração nem caminho padrão.
No macOS, um app aberto pelo Dock não herda o `export` do shell: abra a ferramenta pelo terminal ou defina a variável no
ambiente da sessão. Evite `export` no perfil do shell quando o agente puder rodar comandos, porque ele conseguiria ler o valor.

Desenvolvimento:

```bash
uv sync
uv run pytest
uv run mcp dev mcp_gitlabpr/server.py   # MCP Inspector
```

## Tools

As tools só trabalham no **projeto do workspace**: não há como consultar outro projeto nem MRs de todos os projetos.
O projeto vem do remote do git (`origin`, ou o primeiro remote; aceita `https://`, `ssh://` e `git@host:`) da `pasta`
informada, o caminho absoluto da raiz do workspace, e `origem` vem da branch atual dela. O agente deve passar `pasta`
sempre: o Kiro não inicia o MCP de um power na pasta do workspace, e sem `pasta` vale o cwd do processo. Fora de um
repositório, a pessoa cola a URL do projeto no GitLab (ex.: `https://gitlab.SEU-SERVIDOR/grupo/projeto`; a URL de um MR do
projeto também serve) e o agente a passa em `url_projeto`; aí `origem` é obrigatória no `gitlab_criar_mr`. O host do
remote ou da URL precisa ser o de `GITLAB_API_URL`; se não for, a tool recusa. `iid` é o número do MR no projeto (o `!123`
da tela). Autor e responsável são usernames: o MCP os converte em id por `GET /users?username=`, porque a 10.8 só
filtra por `author_id`/`assignee_id`.

| Tool | Para quê |
|---|---|
| `gitlab_listar_mrs(pasta?, url_projeto?, estado=opened, busca?, origem?, destino?, autor?, responsavel?, limite≤100, pagina=1)` | MRs do projeto. Devolve `total`, `proxima_pagina` e os itens resumidos |
| `gitlab_obter_mr(iid, pasta?, url_projeto?, diffs=false)` | Detalhes do MR (descrição, labels, conflitos, merge). Com `diffs`, os arquivos alterados e o diff de cada um, cortado em 4000 caracteres |
| `gitlab_criar_mr(titulo, destino, pasta?, url_projeto?, origem?, descricao?, responsavel?, labels?, remover_origem=true)` | Cria o MR, já com "apagar a branch de origem após o merge" marcado (passe `remover_origem=false` para manter a branch); a branch de origem precisa já estar no GitLab (`git push`). Se já existe um MR aberto entre as mesmas branches, o GitLab responde 409 |
| `gitlab_mesclar_mr(iid, pasta?, url_projeto?, sha?, remover_origem?, mensagem?)` | Faz o merge de um MR aberto; recusa antes de chamar o GitLab se ele está em WIP, tem conflito ou não é mesclável. Com `sha`, o GitLab recusa (409) se a branch recebeu commits depois desse commit |

`estado` aceita `opened`, `closed`, `merged` ou `all`.
