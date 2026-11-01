# Backend e banco de dados com Docker

O Docker Compose disponibiliza a API FastAPI e PostgreSQL 16 com pgvector instalado e um volume persistente para desenvolvimento local. A extensão será habilitada no banco pela primeira migration Alembic, antes da criação de tabelas vetoriais. São necessários Docker e Docker Compose.

Copie a configuração de exemplo e inicie os serviços:

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose ps
```

As credenciais no exemplo são apenas para desenvolvimento local. O arquivo `.env` não é versionado, e a porta do banco é exposta somente em `127.0.0.1`. Para conectar pelo host, use a porta configurada em `POSTGRES_PORT`.

Com os serviços em execução, aplique as migrations e verifique a extensão:

```bash
docker compose exec api alembic upgrade head
```

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = '\''vector'\'';"'
```

Para parar o serviço sem apagar os dados, execute `docker compose down`. As credenciais do PostgreSQL são aplicadas apenas na inicialização de um volume novo; alterá-las no `.env` não modifica automaticamente um banco já inicializado.

A API fica em `http://localhost:8000`, com documentação em `/docs`. O endpoint `/health` executa `SELECT 1` no banco e retorna `{"status":"ok","database":"ok"}`; se a conexão falhar, retorna HTTP 503 sem expor detalhes internos. Essa verificação não confirma se as migrations foram aplicadas. A API aguarda o healthcheck do PostgreSQL no Compose, mas não aplica migrations automaticamente.

`API_PORT` configura a porta da API no host. Dentro do Compose, a API usa o host `db` e a porta `5432`, independentemente de `POSTGRES_HOST` e `POSTGRES_PORT` do host.

Para desenvolvimento sem o container da API:

```bash
docker compose up -d db
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Nesse caso, `POSTGRES_HOST` e `POSTGRES_PORT` do `.env` configuram a conexão local. Para acompanhar os logs no Docker, use `docker compose logs -f api`. Após alterar código ou dependências, execute novamente `docker compose up -d --build --wait`.

A carga dos CSVs e as migrations das tabelas de negócio ainda serão implementadas. A migration inicial habilita `vector` de forma idempotente; seu downgrade não remove a extensão, pois ela pode ser compartilhada por outros componentes.
