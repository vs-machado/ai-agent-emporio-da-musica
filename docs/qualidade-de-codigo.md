# Qualidade de código

O projeto usa Ruff para lint e formatação de código Python, com Python 3.12 como versão-alvo e comprimento de linha de 100 caracteres. As regras incluem erros de Python (`E` e `F`), organização de imports (`I`) e problemas comuns (`B`). A regra `E402` exige imports no topo do módulo; exceções justificadas devem ser explícitas.

Após clonar o repositório, instale as dependências e ative os hooks locais:

```bash
uv sync --locked
uv run pre-commit install
```

Em cada commit, o pre-commit executa o Ruff para verificar e corrigir problemas suportados e formatar o código. Se um hook alterar arquivos, revise e adicione essas alterações ao commit novamente. Corrija todos os problemas reportados antes de concluir o commit.

Para executar as mesmas verificações manualmente:

```bash
uv run pre-commit run --all-files
```

## Verificações locais

Para desenvolvimento e testes, instale Python 3.12 e uv:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Sem `TEST_DATABASE_URL`, os testes dependentes de banco são pulados. Para executá-los,
prepare PostgreSQL com pgvector em banco dedicado terminado em `_test`:

```bash
TEST_DATABASE_URL='postgresql+psycopg://usuario:senha@localhost:5432/emporio_test' uv run pytest
```

As fixtures aplicam migrations e limpam as tabelas desse banco. Consulte
[Ingestão de dados](ingestao-de-dados.md#testes) para detalhes dos testes de banco.
Os testes com modelos simulados não substituem a avaliação semântica; veja
[Benchmarks](benchmarks.md) e os [resultados publicados](decisao-rag-e-custos.md).

## Versões das dependências

As dependências em `pyproject.toml` têm limites inferiores e superiores explícitos,
por major ou minor conforme a biblioteca. Python aceita versões de 3.12 até antes da 3.14.
A restrição de `langchain-community<0.4` preserva a compatibilidade atual com Ragas.
Esses intervalos reduzem atualizações incompatíveis, mas não garantem compatibilidade
de todas as versões permitidas.

Para reproduzir o ambiente, incluindo versões exatas das dependências transitivas,
use `uv sync --locked` com o `uv.lock` versionado. O Docker também usa esse modo.
Uma instalação sem o lock pode selecionar versões mais novas dentro dos intervalos.
Mudanças de limites e atualizações do lock devem ser acompanhadas das verificações
de qualidade e dos testes relevantes.

O hook é instalado por clone e não substitui verificações de CI. Quando a CI for configurada, ela deverá executar as mesmas verificações. Avisos de dependências em tempo de execução também devem ser investigados; o Ruff não detecta todos eles.
