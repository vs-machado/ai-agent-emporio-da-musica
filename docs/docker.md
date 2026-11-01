# Backend e banco de dados com Docker

O Docker Compose disponibiliza a API FastAPI e PostgreSQL 16 com pgvector instalado e um volume persistente para desenvolvimento local. A extensão será habilitada no banco pela primeira migration Alembic, antes da criação de tabelas vetoriais. São necessários Docker e Docker Compose.

Copie a configuração de exemplo e inicie os serviços:

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose ps -a
```

As credenciais no exemplo são apenas para desenvolvimento local. O arquivo `.env` não é versionado, e a porta do banco é exposta somente em `127.0.0.1`. Para conectar pelo host, use a porta configurada em `POSTGRES_PORT`.

O Compose executa a preparação automaticamente, nesta ordem:

1. `db`: aguarda o PostgreSQL ficar saudável.
2. `migrate`: aplica `alembic upgrade head` e encerra.
3. `ingest`: carrega os CSVs e encerra.
4. `api`: inicia após a ingestão terminar com sucesso.

Os três serviços Python compartilham a definição de build e o nome da imagem pelo bloco `x-app`. Isso permite construir qualquer um deles isoladamente, com reaproveitamento do cache de build. `migrate` e `ingest` devem aparecer como `Exited (0)`; isso indica conclusão bem-sucedida. Uma falha na preparação impede a inicialização da API nesta sequência. Para investigar, use `docker compose logs migrate ingest`.

A API e o banco usam `restart: unless-stopped` para reiniciar após o encerramento do processo ou reinício do Docker, respeitando uma parada manual. Os serviços de preparação usam `restart: "no"`. A política de reinício não é acionada apenas por um healthcheck `unhealthy` e não reexecuta as dependências do Compose.

Para verificar a extensão:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = '\''vector'\'';"'
```

Para parar o serviço sem apagar os dados, execute `docker compose down`. As credenciais do PostgreSQL são aplicadas apenas na inicialização de um volume novo; alterá-las no `.env` não modifica automaticamente um banco já inicializado.

A API fica em `http://localhost:8000`, com documentação em `/docs`. O endpoint `/health` executa `SELECT 1` no banco e retorna `{"status":"ok","database":"ok"}`; se a conexão falhar, retorna HTTP 503 sem expor detalhes internos. Essa verificação não confirma se as migrations foram aplicadas; a preparação é responsabilidade dos serviços `migrate` e `ingest`.

`API_PORT` configura a porta da API no host. Dentro do Compose, a API usa o host `db` e a porta `5432`, independentemente de `POSTGRES_HOST` e `POSTGRES_PORT` do host.

Para desenvolvimento sem o container da API:

```bash
docker compose up -d db
uv sync --locked
uv run alembic upgrade head
uv run python -m app.ingestion
uv run uvicorn app.main:app --reload
```

Nesse caso, `POSTGRES_HOST` e `POSTGRES_PORT` do `.env` configuram a conexão local. Para acompanhar os logs no Docker, use `docker compose logs -f api`. Após alterar código ou dependências, execute novamente `docker compose up -d --build --wait`.

A execução completa de `docker compose up` pode executar novamente os serviços de preparação, inclusive após um `down`. A ingestão reaplica os valores dos CSVs, sobrescrevendo alterações nos registros de origem. Para reiniciar apenas a API, use `docker compose restart api`, que não executa a carga novamente.

A carga dos CSVs está detalhada em [Ingestão de dados](ingestao-de-dados.md). A migration inicial habilita `vector` de forma idempotente; seu downgrade não remove a extensão, pois ela pode ser compartilhada por outros componentes.
