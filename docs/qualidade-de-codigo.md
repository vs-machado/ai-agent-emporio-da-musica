# Qualidade de código

O projeto usa Ruff para lint e formatação de código Python, com Python 3.12 como versão-alvo e comprimento de linha de 100 caracteres. As regras incluem erros de Python (`E` e `F`), organização de imports (`I`) e problemas comuns (`B`). A regra `E402` exige imports no topo do módulo; exceções justificadas devem ser explícitas.

Após clonar o repositório, instale as dependências e ative os hooks locais:

```bash
uv sync
uv run pre-commit install
```

Em cada commit, o pre-commit executa o Ruff para verificar e corrigir problemas suportados e formatar o código. Se um hook alterar arquivos, revise e adicione essas alterações ao commit novamente. Corrija todos os problemas reportados antes de concluir o commit.

Para executar as mesmas verificações manualmente:

```bash
uv run pre-commit run --all-files
```

O hook é instalado por clone e não substitui verificações de CI. Quando a CI for configurada, ela deverá executar as mesmas verificações. Avisos de dependências em tempo de execução também devem ser investigados; o Ruff não detecta todos eles.
