# Banco de dados com Docker

O Docker Compose disponibiliza PostgreSQL 16 com pgvector instalado e um volume persistente para desenvolvimento local. A extensão será habilitada no banco pela primeira migration Alembic, antes da criação de tabelas vetoriais. São necessários Docker e Docker Compose.

Copie a configuração de exemplo e inicie o banco:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

As credenciais no exemplo são apenas para desenvolvimento local. O arquivo `.env` não é versionado, e a porta do banco é exposta somente em `127.0.0.1`. Para conectar pelo host, use a porta configurada em `POSTGRES_PORT`.

Com o banco em execução, aplique as migrations a partir do host e verifique a extensão:

```bash
uv sync
uv run alembic upgrade head
```

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = '\''vector'\'';"'
```

Para parar o serviço sem apagar os dados, execute `docker compose down`. As credenciais do PostgreSQL são aplicadas apenas na inicialização de um volume novo; alterá-las no `.env` não modifica automaticamente um banco já inicializado.

O container da aplicação será configurado quando houver um ponto de entrada executável. A carga dos CSVs e as migrations das tabelas de negócio ainda serão implementadas. A migration inicial habilita `vector` de forma idempotente; seu downgrade não remove a extensão, pois ela pode ser compartilhada por outros componentes.
