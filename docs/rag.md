# RAG das políticas

O manual `kb/políticas_da_loja.pdf` é a única fonte indexada. A implementação está
em `app/rag/` e segue as [decisões técnicas](decisoes-tecnicas.md). Não gera respostas
por LLM: entrega evidências para a tool `search_policy` e para o agente futuro.

## Preparação e execução local

Com PostgreSQL disponível e variáveis de `.env.example` configuradas:

```bash
uv sync --locked
uv run alembic upgrade head
uv run python -m app.rag --inspect
```

`--inspect` não exige configuração de banco nem chave do Gemini. Extrai o PDF,
valida sua estrutura e informa seções, páginas e contagens aproximadas de tokens
com o limite padrão de 1.000 tokens por chunk. Para o manual 2.1 são 28 seções e
25 chunks. A capa não é uma unidade de política; títulos ancestrais sem corpo
permanecem na hierarquia. Cabeçalhos e rodapés repetidos são removidos.

Configure `GOOGLE_API_KEY` em `.env` para gerar embeddings reais e execute:

```bash
uv run python -m app.rag
uv run python -m app.rag --query "Produto em promoção tem desconto adicional no PIX?"
uv run python -m app.rag --evaluate
```

Use `--pdf caminho/manual.pdf` para indicar outra fonte com a mesma estrutura
numerada. O parser é orientado ao layout do manual atual, não um extrator universal
para PDFs arbitrários ou digitalizados. Fontes com estrutura diferente exigem
inspeção e adaptação. Fora da sequência do Compose, a API pode iniciar sem índice
ativo; nesse caso, `search_policy` retorna `unavailable`.

No Docker, o PDF e o cache do tokenizer são incluídos na imagem. Configure
`GOOGLE_API_KEY` no `.env` e suba os serviços; a indexação já faz parte da preparação:

```bash
docker compose up -d --build --wait
docker compose run --rm --no-deps api python -m app.rag --inspect
docker compose run --rm --no-deps index-policies
```

`index-policies` aguarda as migrations e executa em paralelo à ingestão dos CSVs.
A API aguarda o sucesso de ambas as etapas; falhas de indexação bloqueiam sua
inicialização nessa sequência. O comando `run` acima permite repetir apenas a
indexação, com banco e migrations já preparados. Um índice idêntico existente é
reutilizado sem chamar o provedor.

As variáveis do Compose incluem a chave e os parâmetros RAG. `OPENROUTER_API_KEY`
é opcional e está reservada ao futuro fallback de chat DeepSeek via OpenRouter;
não é usada nos embeddings. Não há indexação no lifespan da API nem na ingestão dos CSVs.
Fora do Docker, o tiktoken pode baixar o vocabulário em sua primeira utilização;
depois reutiliza seu cache local.

## Configuração inicial

| Variável | Padrão | Uso |
|---|---|---|
| `GOOGLE_API_KEY` | vazia | Necessária para novos embeddings; não é armazenada no índice |
| `OPENROUTER_API_KEY` | vazia | Opcional; reservada ao futuro fallback de chat DeepSeek |
| `RAG_EMBEDDING_MODEL` | `gemini-embedding-001` | Mesmo modelo para documentos e perguntas |
| `RAG_EMBEDDING_DIMENSIONS` | `768` | Dimensão fixa nesta migration |
| `RAG_TOP_K` | `5` | Candidatos antes de expansão e deduplicação |
| `RAG_CHUNK_TOKENS` | `1000` | Limite por unidade indexada, incluindo títulos |
| `RAG_CONTEXT_TOKENS` | `2000` | Orçamento aproximado das evidências com metadados |

A contagem usa `cl100k_base`, não o tokenizer exato dos modelos de chat. O orçamento
deve comportar um chunk mais uma reserva de 256 tokens para referências.
`PolicySearch.limit` tem padrão e máximo 5 e limita apenas as evidências finais.

## Extração, persistência e atualização

- pypdf preserva o layout horizontal; o parser reconstrói tabelas em Markdown,
  itens e parágrafos, unindo seções que atravessam páginas. Números de contato,
  valores e ambiguidades não são corrigidos durante a extração.
- A subseção é a unidade padrão. Se ela exceder o limite, a divisão ocorre entre
  blocos estruturais completos. Blocos indivisíveis grandes demais fazem a
  indexação falhar com indicação da seção, para revisão da divisão.
- TextNodes LlamaIndex incluem hierarquia, seção, fonte, versão e páginas. O
  embedding recebe o texto com títulos; os metadados técnicos não são repetidos
  no texto vetorizado. As tarefas são `RETRIEVAL_DOCUMENT` e `RETRIEVAL_QUERY`.
- `policy_indexes` registra hash, fingerprint de fonte/configuração e versão ativa.
  `policy_sections` guarda unidades completas. `data_policy_vectors` mantém os
  nodes serializados no formato do PGVectorStore, embeddings e índice textual GIN.
  A migration `0004` gerencia essas três tabelas; LlamaIndex não cria schema em runtime.
- Os vetores são gerados antes da transação de escrita. A gravação de todas as
  unidades e a troca da versão ativa são atômicas, com lock de ativação para
  serializar indexações concorrentes. Falhas preservam o índice anterior.
- Fonte e configuração idênticas retornam `unchanged`, sem novas chamadas de
  embeddings. Reexecutar uma versão anterior já armazenada a reativa, sem gerar
  vetores novamente. Versões inativas são mantidas, mas nunca participam das buscas.
- Alterar parser, chunking, modelo ou dimensão exige nova indexação. Alterar a
  dimensão também exige migration. Top-k e orçamento de saída não alteram os vetores.

## Recuperação e integração

`LlamaIndexPolicyRetriever` implementa `PolicyRetriever.search(query, limit)`:

1. Identifica uma versão ativa para toda a consulta.
2. Usa `VectorStoreIndex` e `PGVectorStore` com filtro dessa versão e distância cosseno.
3. Se o embedding ou a consulta vetorial falhar, usa full-text search PostgreSQL
   em português, com `websearch_to_tsquery` e ordenação por `ts_rank_cd`.
4. Expande candidatos para a subseção completa quando ela couber no orçamento.
   Se não couber, tenta o bloco original completo. Deduplica unidades cobertas e
   preserva a ordem do candidato mais bem classificado.
5. Retorna até cinco `PolicyPassage`, com `text`, `source`, `document_version`,
   `section`, `title`, `page_start` e `page_end`. As páginas são a localização da
   seção de origem, inclusive quando apenas um bloco dela é retornado.

Sem chave ou com modelo diferente do índice ativo, a busca usa fallback textual.
Uma consulta vetorial vazia não dispara fallback por si só. O fallback não é busca
híbrida e pode perder cobertura com paráfrases ou termos ausentes do documento.
Sem banco, nenhum dos caminhos funciona; a tool retorna indisponibilidade.

No manual atual, as unidades já cabem em um único chunk; a expansão normalmente
não adiciona texto. Se unidades maiores precisarem ser divididas, ela recompõe
sua subseção quando possível. Não há relações semânticas manuais nem expansão
automática de exceções em seções distantes; a cobertura depende da recuperação
dos múltiplos chunks e deve ser medida no benchmark.

O lifespan de `app/main.py` constrói o retriever e as tools em `app.state`.
Para uso separado da API, veja a construção em [Tools](tools.md).

## Avaliação

`--evaluate` executa dez perguntas em `app/rag/evaluation.py`, com seções esperadas
para horários, PIX/promoções, parcelamento, devoluções, trocas, frete, garantia,
privacidade e reclamações. O relatório JSON inclui:

- caminho real utilizado (`vector` ou `text_fallback`);
- seções esperadas, recuperadas e ausentes;
- recall de seções por pergunta e médio;
- proporção de perguntas com todas as seções esperadas;
- quantidade de candidatos, tokens de contexto e latência.

O relatório mede cobertura de seções, não a qualidade das respostas geradas nem
a presença de cada frase necessária dentro de chunks parciais. Referências,
condições e exceções ainda devem ser conferidas. Compare `RAG_TOP_K=3`, `5` e `8`
com o mesmo orçamento e limite final. Registre se o caminho foi vetorial ou fallback
para não atribuir resultados textuais ao Gemini. A qualidade semântica real só
pode ser validada após indexar e consultar com embeddings do provedor.

## Testes locais

```bash
uv run pytest tests/test_rag_parsing.py tests/test_tools_contracts.py
TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/emporio_test uv run pytest
```

O banco de teste deve ter nome terminado em `_test`; as fixtures aplicam migrations
e limpam suas tabelas. Os testes de integração usam embeddings locais determinísticos
para validar LlamaIndex/pgvector, isolamento de versões, idempotência, concorrência,
rollback, expansão, orçamento, fallback e integração com a tool. Esses embeddings
não substituem a avaliação de qualidade com `gemini-embedding-001`.
