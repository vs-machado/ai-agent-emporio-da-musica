# Ingestão de dados operacionais

Os seis CSVs originais de `agent-data/` alimentam as tabelas `categories`, `customers`, `products`, `orders`, `order_items` e `promotions`. Os arquivos de origem são preservados. A ingestão é um comando de carga dos dados de demonstração, executado automaticamente por um serviço dedicado no Docker Compose, após as migrations e antes da API.

## Execução

Após configurar o `.env` e iniciar o PostgreSQL:

```bash
uv sync --locked
uv run alembic upgrade head
uv run python -m app.ingestion
```

Também é possível indicar outro diretório com os mesmos seis nomes de arquivo:

```bash
uv run python -m app.ingestion --data-dir /caminho/dos/csvs
```

Pelo Docker, a preparação é automática:

```bash
docker compose up -d --build --wait
docker compose logs migrate ingest
```

O Compose aguarda o PostgreSQL saudável, executa `migrate`, depois `ingest` e inicia a API após ambos terminarem com sucesso. Os serviços de preparação encerram com código 0 e não têm reinício automático. Não é necessário executar migrations e carga manualmente nesse fluxo.

Para reaplicar apenas a carga com o banco e o schema já preparados:

```bash
docker compose run --rm --no-deps ingest
```

Os CSVs são incluídos na imagem; mudanças nos arquivos exigem um novo build. A carga fornecida processa 9 categorias, 50 clientes, 65 produtos, 20 pedidos, 22 itens e 25 promoções: 191 registros. O resumo informa registros processados, não separa inserções, atualizações e linhas sem mudanças. Falhas retornam código de saída 1.

## Validação e transação

O leitor usa `csv` da biblioteca padrão e schemas Pydantic. Antes de gravar, valida todos os arquivos em memória:

- UTF-8, com suporte a BOM, cabeçalhos sem campos faltantes, extras ou duplicados e número de colunas por linha. A ordem das colunas pode variar.
- Presença de registros, campos obrigatórios, IDs inteiros positivos e ausência de chaves duplicadas.
- Valores monetários finitos, não negativos e representáveis em `Numeric(12, 2)`, sem arredondamento silencioso.
- Estoque não negativo, quantidade de itens positiva, status e meios de pagamento reconhecidos.
- JSON de especificações com objeto na raiz, datas válidas e percentual entre 0 e 100, com até duas casas decimais.
- `is_active` estritamente igual a `0` ou `1` e formato básico de e-mail, sem consultar serviços externos.
- Referências de produtos a categorias, pedidos a clientes e itens e promoções a seus registros de origem. Os pais devem estar nos CSVs desta carga, mesmo que já existam no banco.

Após a validação, grava tudo em uma única transação: categorias e clientes, produtos, pedidos, itens e promoções. Qualquer falha reverte todas as gravações da execução. Os erros de validação identificam arquivo, linha e campo sem imprimir valores dos registros. Erros de banco informam o arquivo em processamento, sem expor SQL, parâmetros ou credenciais.

## Reexecução e preservação dos dados

A gravação usa `INSERT ... ON CONFLICT DO UPDATE` pelas chaves primárias da origem. Nos itens, a chave é `(order_id, product_id)`. Reexecutar não duplica registros e atualiza todos os campos não pertencentes à chave com os valores do CSV. Isso também restaura estoque e status da fonte, sobrescrevendo alterações anteriores nesses registros. Não é uma sincronização de snapshots: registros ausentes dos arquivos não são excluídos automaticamente.

Uma nova execução completa de `docker compose up` pode reaplicar a carga, inclusive após remover os containers com `down`. Para reiniciar apenas a API sem reexecutar a preparação, use `docker compose restart api`.

- Preços e totais usam `Decimal` no Python e `Numeric` no PostgreSQL.
- `specs` é convertido para objeto e armazenado como `JSONB`, mantendo chaves e valores, sem normalização por categoria. Por exemplo, `strings` pode representar material ou quantidade de cordas conforme o instrumento.
- As datas são armazenadas sem horário. Células vazias de rastreamento, previsão de entrega e observações tornam-se `NULL`.
- O total dos pedidos é preservado: os itens não têm preço histórico, portanto preços atuais do catálogo não são usados para recalcular compras anteriores.
- Status comercial e estoque permanecem independentes. Divergências entre nome, descrição e especificações não são corrigidas por inferência.
- Não são criados preços históricos, datas de vigência de promoção ou datas reais de recebimento ausentes na fonte.

## Testes

Os testes de validação executam sem banco:

```bash
uv run pytest tests/test_ingestion_validation.py
```

Os testes de integração exigem PostgreSQL com pgvector instalado e um banco dedicado cujo nome termine em `_test`. Eles aplicam as migrations e limpam as seis tabelas entre testes. Nunca aponte essa configuração para um banco com dados a preservar.

```bash
TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/emporio_test uv run pytest
```

Sem `TEST_DATABASE_URL`, os testes de integração são pulados. Eles verificam carga, precisão monetária, JSONB, nulos, idempotência, atualização sem exclusão e rollback real em caso de violação de uma restrição no banco.
