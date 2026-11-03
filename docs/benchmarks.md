# Benchmark do agente com Ragas

`app/benchmarks/` avalia respostas reais do atendimento, sem substituir o benchmark
de cobertura de seções em `app/rag/evaluation.py`. O Ragas 0.4.3 já faz parte das
dependências do projeto; a integração usa a API `ragas.metrics.collections`.

## Dataset baseado no banco

`app/benchmarks/cases.json` contém 38 casos: os 31 anteriores (28 derivados dos
56 turnos revisados do banco e três cenários novos) e sete cenários sintéticos
adicionais para avaliar as 11 tools e fluxos com múltiplas chamadas. O caso de
violão de sete cordas substitui o de eletroacústico exemplificado nas instruções;
seu gabarito consulta somente modelos de sete cordas. Esse exemplo não é incluído
no prompt nem nas descrições das ferramentas. Cada caso registra origem, pergunta,
contexto necessário ao replay, tools esperadas, chamadas de referência e critérios
de correção. A versão `2026-11-02.6` acrescenta planos admissíveis de tools,
consultas complementares e cenários de detalhes, pagamento, carrinho/frete,
reembolso, reclamação, saudação e privacidade de pedidos. O CEP 79002-000 foi
confirmado no ViaCEP na revisão anterior.
As perguntas foram verificadas no banco, incluindo contexto de instrumento, nylon/aço,
orçamento de teclado, recuperação de buscas vazias, flautas, frete e rastreamento.

As perguntas cobrem os seis relatos: horários, garantia, arrependimento, estoque
de baterias, exemplos de catálogo e promoções. A exclusão de garantia também tem
um caso próprio para detectar a mistura com regras de troca. As referências das
políticas foram verificadas no manual 2.1; **respostas antigas do agente não são
usadas como gabarito**. Perguntas selecionadas não contêm identidades de clientes.

- `historical`: lê o turno e todo o histórico anterior da conversa no banco,
  avaliando a resposta e as consultas que realmente ocorreram, sem reexecutá-las.
  Valida que o ID corresponde à pergunta revisada; outro banco pode exigir um
  dataset com IDs de origem correspondentes.
- `live`: executa perguntas novas com o agente atual. Usa o histórico mínimo
  revisado de cada caso; não é replay integral do histórico original. O caso
  `bateria_contextual` preserva a necessidade de interpretar “Quais opções você tem?”.
  Os históricos são independentes e não são gravados em `conversation_turns`.

Casos com `auth_customer_id` resolvem uma conta `users` realmente vinculada ao
cliente indicado (preparada pelo seed) e passam sua identidade ao agente, sem
extrair autorização da pergunta. Conta não provisionada gera erro explícito
`authenticated_fixture_missing`. Nenhum vínculo é criado pelo benchmark.
Esses casos são `skipped: requires_live_authenticated_context` em modo histórico:
o turno antigo não comprova o contexto autenticado atual. Isso também vale para
`rastreamento_sem_identificadores`, cujo novo gabarito consulta a conta sem pedir
número ou e-mail, preservando o ID 25 apenas como procedência da pergunta.

Os casos não seguem uma sequência entre si. Em `live`, cada caso começa somente
com seu campo `history` (vazio para perguntas independentes). Quando uma pergunta
é reformulada, `source_question` preserva o texto original para validar a origem
no banco. O modo histórico avalia a pergunta original e sua resposta já salva;
o modo `live` usa a formulação revisada. O relatório distingue `question`
(entrada avaliada) de `benchmark_question` (formulação atual do dataset).
Casos novos têm `origin: user_requested` e nenhum `source_turn_id`: são executados
em `live` e marcados `skipped: no_historical_source` em `historical`, sem inventar
uma conversa de origem. O caso de violão acústico aceita modelos eletroacústicos
disponíveis, que devem ser identificados como instrumentos com captação.

O replay oferece as **mesmas 11 ferramentas do atendimento**, diretamente de
`build_tools`, com schemas, descrições e implementações reais:

- `search_policy`, `get_current_datetime`;
- `search_products`, `get_product_details`, `get_active_promotions`;
- `get_order_status`, `calculate_refund`, `calculate_payment`;
- `lookup_postal_code`, `calculate_shipping`, `register_complaint`.

Todas aparecem no manifest, e todos os resultados entram na captura de evidências.
As ferramentas usam o banco configurado. A implementação real de
`register_complaint` executa dentro de uma transação externa com savepoint,
revertida após capturar cada resultado: as reclamações do benchmark não permanecem
no banco. O protocolo retornado é somente evidência dessa execução isolada;
idempotência entre chamadas não é avaliada por esse wrapper. Os turnos não são
gravados como conversas. O modo histórico somente lê as chamadas já realizadas.

O conjunto revisado exige cada uma das 11 ferramentas em pelo menos um plano.
Isso é cobertura de seleção, não prova de cobertura funcional completa. O manifest
e o relatório guardam `tool_coverage`, inclusive ferramentas não exigidas por
nenhum dos casos selecionados. Nomes desconhecidos no dataset são rejeitados antes
da execução. Os schemas registrados são os visíveis ao modelo: `request_id` da
reclamação é injetado pelo middleware e não é um argumento obrigatório da LLM.

### Contrato de tools por caso

- `expected_tools`: tools obrigatórias do caminho padrão, quando não há planos.
- `optional_tools`: consultas complementares permitidas, não obrigatórias.
- `tool_plans`: caminhos alternativos, cada um com nome, `required_tools`,
  `optional_tools` próprias e `ordered` (padrão falso).
- `forbidden_tools`: chamadas explicitamente proibidas para aquele cenário.
- `reference_calls`: uma sequência representativa para diagnósticos do Ragas;
  não é mais tratada como a única forma válida de resolver a pergunta.

Por exemplo, buscar baterias e depois consultar detalhes de um modelo é válido,
assim como refazer uma busca mais ampla. Na consulta de promoções, tanto a tool de
campanhas quanto uma consulta de catálogo que retorne ofertas podem ser caminhos
válidos. Já localizar um pedido e calcular seu reembolso requer as duas tools, na
ordem correta; a política pode ser consultada em diferentes pontos do fluxo.
Cada plano deve ser justificado pelo cenário; não se permite indiscriminadamente
qualquer uma das 11 tools em todos os casos.

## Métricas

| Dimensão | Implementação | Entrada |
|---|---|---|
| Relevância do contexto | Ragas `ContextRelevance` | Pergunta e contextos efetivamente expostos |
| Fundamentação | Ragas `Faithfulness` | Pergunta, resposta e contextos efetivamente expostos |
| Relevância da resposta | Ragas `AnswerRelevancy` | Pergunta, resposta e embeddings das perguntas geradas |
| Correção factual adicional | Ragas `FactualCorrectness` | Resposta e referência revisada, quando disponível |
| Plano de tools | `tool_plan_success`, `tool_plan_f1` | Melhor caminho admissível, obrigatórias/opcionais e ordem quando exigida |
| Cobertura das obrigatórias | `required_tool_recall` | Tools necessárias do plano escolhido versus chamadas |
| Precisão da seleção | `allowed_tool_precision` | Tools permitidas versus chamadas, sem penalizar complementares válidas |
| Contratos dos argumentos | `tool_schema_validity` | Todas as chamadas versus schemas públicos do registro real |
| Execução das tools | `tool_execution_success` | Pareamento por ID, resultado JSON e erros operacionais |
| Diagnóstico de referência | Ragas `ToolCallAccuracy`, ordem flexível | Uma sequência de referência, marcada `diagnostic` |
| Diagnóstico exato | Ragas `ToolCallF1` | Igualdade exata de nomes/argumentos, marcada `diagnostic` |

As três primeiras formam a **RAG Triad**. Contextos incluem trechos do manual e
resultados das ferramentas. Os relatórios guardam separadamente passagens,
resultados das tools e diagnósticos do retriever para distinguir RAG de catálogo.
Evidências de turnos anteriores também são consideradas, pois estavam acessíveis
ao agente. Somente chamadas do turno avaliado entram nas métricas de tools.

Não há recuperação adicional escondida para “ajudar” a fundamentação: se o agente
não consultou a fonte e não havia evidência no histórico, `Faithfulness` e
`ContextRelevance` ficam `skipped: no_retrieved_context`. Isso é uma lacuna de
cobertura, não uma aprovação. O relatório mostra média, quantidade avaliada,
quantidade pulada e erros para cada métrica. `--no-judge` não avalia a tríade.

**Limites das métricas de tools:** consultas equivalentes podem ter argumentos
diferentes. O F1 exato e a accuracy do Ragas ficam como diagnósticos, não gates.
No Ragas 0.4.3, `ToolCallAccuracy` verifica as chaves de referência, mas ignora
argumentos extras: `query=bateria, category=instrumentos musicais` pode receber
accuracy 1 mesmo estando incorreto. `ToolCallF1` detecta essa diferença, enquanto
recall dos nomes revela que a ferramenta certa foi chamada. Nenhuma dessas
métricas, isoladamente, comprova uso correto do resultado. O plano mede nomes e
ordem, não equivalência semântica de argumentos; a validação de schema detecta
tipos/campos inválidos, mas não prova que uma categoria ou ID válido seja o certo.
Oracles, checks de evidência e avaliação factual continuam necessários.

Consultas repetidas à mesma tool não são penalizadas apenas por repetir o nome.
Resultados `not_found` podem ser corretos; `unavailable`, `invalid_input`,
`request_conflict`, erros de tool e resultados ausentes/duplicados são falhas de
execução. Se nenhuma chamada ocorrer, contrato e execução ficam `skipped:
no_tool_calls`, sem inflar a média com sucesso fictício. A ausência correta de
chamadas é avaliada pelo plano (por exemplo, na saudação). `ToolCallF1` continua
`skipped: no_expected_calls` quando não há sequência de referência.

Seleção, schema e execução devem ser lidos em conjunto: um plano pode estar
correto mesmo com falha do provedor ou argumentos inválidos, e isso aparece nas
métricas separadas. A versão `admissible-plans-v1` é registrada; suas notas não
devem ser comparadas diretamente com relatórios anteriores sem reavaliá-los.

Há checks objetivos adicionais: prazo/marco inicial, referência a seção de ajuda,
pedido de cidade, seção recuperada, apresentação de um modelo disponível e opção
mais barata no catálogo genérico. Em `live`, um oracle independente consulta o
catálogo atual; esse resultado é persistido, mas **não entra nas evidências de
fundamentação**. No modo histórico, o oracle atual é apenas diagnóstico e não é
usado para aprovar/reprovar estoque antigo. Checks de texto são heurísticos e
devem ser analisados junto das métricas e das evidências.

## Executar

Com `.env`, banco acessível e migrations aplicadas:

```bash
# Diagnóstico histórico local: tools e checks, sem chamadas ao avaliador
uv run python -m app.benchmarks --mode historical --no-judge

# Avaliar fontes históricas compatíveis; casos novos/autenticados ficam skipped
uv run python -m app.benchmarks --mode historical

# Conversas novas com o agente, tools e índice atuais
uv run python -m app.benchmarks --mode live

# Seleção de casos e parâmetros do avaliador
uv run python -m app.benchmarks --mode live \
  --case horario_sabado --case bateria_contextual \
  --judge-model xiaomi/mimo-v2.6-flash --judge-max-tokens 16384 \
  --metric-timeout 90 --output benchmark-results
```

O avaliador padrão é **MiMo-V2.6-Flash via OpenRouter**, usando
`OPENROUTER_API_KEY`. `GOOGLE_API_KEY` continua necessária para os embeddings
da busca vetorial e de `AnswerRelevancy`; não é usada pelo avaliador padrão.
O agente `live` usa a chave do seu
`CHAT_PRIMARY_MODEL` (por padrão, `OPENROUTER_API_KEY` para DeepSeek). Casos
autenticados exigem as contas provisionadas conforme [Autenticação](autenticacao.md).
O avaliador
é separado do modelo do atendimento; pode ser selecionado com `--judge-model`
(ID do catálogo do provedor, por exemplo `xiaomi/mimo-v2.6-flash`, sem prefixo
`openrouter/`). `--judge-provider openrouter|google` seleciona endpoint e chave;
o padrão Google, quando escolhido explicitamente, é `gemini-2.5-flash`.
Modelo, provedor, endpoint e provedor de embeddings ficam registrados nos relatórios.
O padrão é `xiaomi/mimo-v2.6-flash`, temperatura 0, até 16.384 tokens, timeout de 90s por
métrica. O limite comporta também tokens de raciocínio; saída truncada é erro,
não nota zero. Uma execução chama o provedor várias vezes por caso.

Exemplo de seleção explícita do avaliador anterior:

```bash
uv run python -m app.benchmarks --mode live \
  --judge-provider google --judge-model gemini-2.5-flash
```

Mantenha o mesmo modelo/provedor de avaliação ao comparar retrieval atual e
híbrido. Notas de MiMo e Gemini não são diretamente intercambiáveis; a execução
parcial anterior com Gemini permanece preservada como diagnóstico.

`CHAT_*` e `RAG_*` seguem `Settings`. Por exemplo, para comparar top-k:

```bash
RAG_TOP_K=3 uv run python -m app.benchmarks --mode live --case horario_sabado
RAG_TOP_K=8 uv run python -m app.benchmarks --mode live --case horario_sabado
```

Isso **não** altera o tamanho dos chunks do índice existente. Mudanças em
`RAG_CHUNK_TOKENS` exigem nova indexação. O manifest distingue a configuração
solicitada da configuração efetivamente armazenada no índice ativo. Mantenha
dataset, índice, modelo e demais parâmetros constantes ao comparar top-k.
Repita execuções para avaliar variabilidade; temperatura 0 não garante determinismo.

### Comparar retrieval atual e busca híbrida

O atendimento, as CLIs e o benchmark constroem o retriever pela mesma fábrica:
`app.rag.retrieval.build_policy_retriever(engine, settings)`. `RAG_STRATEGY=hybrid`
seleciona vetorial + BM25 + RRF (padrão); `vector_with_text_fallback` seleciona o
baseline anterior, sem alterações no benchmark nem no dataset.
O retriever deve manter estes métodos:

- `search(query, limit)`: retorna `list[PolicyPassage]` para a tool de políticas.
- `metadata()`: retorna um dicionário JSON público com `strategy`, `parameters`
  e, opcionalmente, uma `version` da implementação/algoritmo. Deve descrever os
  parâmetros efetivos, sem credenciais, clientes HTTP ou objetos de runtime.
- `retrieve(query, limit)`: retorna `RetrievalResult(passages, mode,
  candidate_count, diagnostics={})`. `mode` identifica o caminho realmente usado;
  `diagnostics` aceita campos JSON arbitrários, como candidatos por etapa,
  tempo de fusão, uso de reranker ou motivos de fallback.

O benchmark não enumera estratégias nem interpreta parâmetros de fusão/reranking.
Por exemplo, um retriever híbrido pode informar:

```json
{
  "strategy": "hybrid",
  "version": 1,
  "parameters": {
    "vector": {"top_k": 10, "metric": "cosine"},
    "bm25": {"top_k": 10, "k1": 1.2, "b": 0.75},
    "fusion": {"algorithm": "rrf", "k": 60, "weights": [1, 1]},
    "context_tokens": 2000,
    "max_passages": 5
  }
}
```

A implementação híbrida informa `hybrid`, com modelo/dimensão de embeddings,
top-k vetorial e BM25, tokenizador português, k1/b/IDF do BM25 e parâmetros RRF,
além de orçamento de contexto, expansão, deduplicação e limite final de passagens.
Os diagnósticos por consulta registram contagens por caminho, falhas, ranks e
scores de fusão. O baseline informa `vector_with_text_fallback` e usa `ts_rank_cd`
somente em erro vetorial.

Manifest e relatório guardam os metadados em `configuration.rag.retrieval` e seu
hash canônico em `configuration.rag.retrieval_sha256` (ordem das chaves não altera
o hash). Cada consulta também registra a configuração informada naquele instante
e seus diagnósticos. O hash identifica a configuração declarada, não prova
identidade do código: commit e hashes dos fontes continuam registrados.

Para comparação direta, faça duas execuções **live** com o mesmo dataset/hash,
casos selecionados, manual/índice, modelo/prompt, avaliador e orçamento final de
contexto, alterando apenas o retrieval desejado. Compare métricas e checks dos
mesmos `case_id`, latência e consumo de tokens. Não é necessário alterar o gabarito
quando mudar de vetorial para híbrido. Não há métricas de ranking como MRR/nDCG:
a avaliação existente mede qualidade final e cobertura das seções esperadas.

### Consumo de tokens

Cada caso e o resumo final incluem `token_usage`, separado em `agent` e `judge`,
com entrada, saída, total, quantidade de chamadas com dados disponíveis e totais
por modelo. As chamadas brutas preservam também os detalhes de cache/raciocínio
informados pelo provedor; esses detalhes não são somados de novo ao total.

- **Agente:** usa `AIMessage.usage_metadata` das chamadas do turno, incluindo
  rodadas intermediárias de tools e modelos de fallback que geraram mensagens.
  Tentativas que falharam sem retornar mensagem/usage não são recuperáveis por
  essa captura; os totais não equivalem necessariamente à fatura do provedor.
- **Avaliador:** captura usage das requisições HTTP de chat do cliente dedicado,
  inclusive tentativas descartadas/repetidas pelo Ragas. Requisição sem resposta
  ou resposta sem usage permanece desconhecida.
- **Embeddings:** o adaptador atual não expõe usage ao benchmark; o consumo fica
  `not_recorded`, com total nulo, tanto para retrieval quanto para o avaliador.
- **Custo monetário:** não é calculado sem preços configurados. Compare tokens
  por modelo e aplique as tarifas correspondentes, considerando cache e descontos.

`partial` indica que há chamadas/casos sem consumo registrado; os totais então
são apenas a soma conhecida. Ausência de usage é `null`, não zero.
`no_calls` representa zero chamadas capturadas (por exemplo, judge desabilitado).
Em `historical`, os tokens do agente são os salvos no turno antigo, quando
disponíveis; os tokens do judge são gastos novos dessa avaliação.

## Persistência e diagnóstico

Cada execução cria `benchmark-results/<timestamp>-<uuid>/`, ignorado pelo Git:

- `manifest.json`: dataset e hash, casos selecionados, modelos, versões,
  parâmetros do avaliador, schemas das tools, commit e estado dirty, hashes dos
  fontes relevantes e do prompt. Inclui estratégia de chunking, parser,
  tokenizer, tamanho de chunk, embedding/dimensão, top-k, orçamento, expansão,
  limites e versão/hash/configuração do índice ativo.
- `events.jsonl`: eventos incrementais com início/fim dos casos, captura de
  resposta, chamadas, argumentos, resultados, evidências e métricas. Escrita com
  flush/fsync preserva os casos anteriores se a execução falhar.
- `partial.json`: último conjunto de resultados concluídos, atualizado por caso.
- `report.json`: resultado final, médias com denominadores, checks e detalhes por
  caso, histórico público, modelo efetivo/fallback, uso de tokens e latência.
  As métricas exatas do Ragas são identificadas como diagnósticas também no resumo;
  cada caso inclui seu contrato e o plano selecionado, tools faltantes, inesperadas
  ou proibidas, violações de schema e falhas de execução, sem expor valores inválidos.

Em `live`, cada consulta de política guarda query, limite final, modo informado
pelo retriever (atualmente `vector` ou `text_fallback`), configuração, diagnósticos,
candidatos, passagens e latência. Em `historical`, o modo
original e a configuração completa antiga não foram persistidos; aparecem como
desconhecidos. O snapshot é explicitamente da configuração **atual** e não deve
ser atribuído retroativamente à conversa antiga. Alteração do índice ativo ou
dos metadados de retrieval entre os snapshots inicial/final gera evento e marca
a execução como parcial.

`completed` significa execução concluída, **não atendimento aprovado**. Erro de
caso, modelo indisponível ou métrica com falha produz `partial` e exit code 1.
Interrupções preservam relatório `interrupted` quando o processo permite tratar
a interrupção. Valores não finitos não são serializados como notas válidas.

Os relatórios não contêm `.env`, URL/credenciais do banco ou chaves do provedor.
E-mails/telefones recebem minimização básica e erros de SDK só registram a classe.
Isso não substitui revisão de dados pessoais ao acrescentar novos casos. Não
exporte históricos completos de clientes indiscriminadamente. Analytics do Ragas
é desabilitado por padrão para este módulo.

## Baseline observado

Execução histórica `20261102T134941Z-523dcf83`, dez casos, avaliador
`gemini-2.5-flash`, sem falhas de métricas:

Esse baseline corresponde à versão inicial de dez perguntas e ao replay com cinco
tools. Novas execuções usam o dataset de 31 perguntas e as 11 tools, conforme o manifest;
as médias não devem ser comparadas diretamente sem igualar casos e configuração.

| Métrica | Média | Avaliados / pulados |
|---|---:|---:|
| Relevância da resposta | 0,377 | 10 / 0 |
| Relevância do contexto | 0,583 | 3 / 7 |
| Fundamentação | 0,364 | 3 / 7 |
| Correção factual | 0,255 | 6 / 4 |
| Recall das tools necessárias | 0,200 | 10 / 0 |
| ToolCallAccuracy | 0,200 | 10 / 0 |
| ToolCallF1 | 0,100 | 10 / 0 |

São resultados dessa amostra/juiz, não estimativas de todo o atendimento.
Os sete casos sem contexto não entram na média de fundamentação. A baixa
cobertura de tools indica que o diagnóstico não deve se limitar ao retriever.

Também foi executado o modo `live` em quatro casos. No caso de exclusões de
garantia (`20261102T135831Z-ca07a5d9`), a consulta foi vetorial, com cinco candidatos;
as três métricas da tríade foram calculadas. Na execução de sábado, bateria
contextual e promoções (`20261102T135603Z-edb2205b`), o agente voltou a pedir
cidade para horário e não consultou promoções. A consulta de bateria retornou
três modelos disponíveis, mas o turno terminou `unavailable`; a execução ficou
`partial`, preservando a consulta e o resultado. Isso é uma falha do atendimento
registrada pelo benchmark, não um resultado omitido.

## Testes

```bash
uv run pytest tests/test_benchmarks.py tests/test_rag_parsing.py tests/test_tools_contracts.py
```

Os testes locais usam as métricas de tools reais do Ragas, sem LLM avaliador.
Validam falta de evidência, argumentos indevidos, separação entre oracle e
evidências, falha parcial, redação de credenciais e persistência. O teste que
consulta turnos usa a fixture PostgreSQL isolada e requer `TEST_DATABASE_URL`
com nome terminado em `_test`, conforme os demais testes de integração.
