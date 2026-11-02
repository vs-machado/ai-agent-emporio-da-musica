# Benchmark do agente com Ragas

`app/benchmarks/` avalia respostas reais do atendimento, sem substituir o benchmark
de cobertura de seções em `app/rag/evaluation.py`. O Ragas 0.4.3 já faz parte das
dependências do projeto; a integração usa a API `ragas.metrics.collections`.

## Dataset baseado no banco

`app/benchmarks/cases.json` contém 31 casos: 29 derivados dos 56 turnos revisados
do banco em 02/11/2026 e dois cenários novos solicitados pelo usuário. Cada caso registra origem, pergunta,
contexto necessário ao replay, tools esperadas, chamadas de referência e critérios
de correção. A versão `2026-11-02.4` revisa a ampliação dos dez casos iniciais:
reformula endereço/horário e catálogo de hoje, remove uma pergunta redundante e
adiciona frete para Campo Grande por cidade e pelo CEP 79002-000, confirmado no ViaCEP.
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
As ferramentas usam o banco configurado; `register_complaint` pode gravar uma
reclamação se for chamada com dados válidos. Para avaliar cenários de escrita,
configure uma cópia isolada do banco. Os turnos de benchmark não são gravados como
conversas. O modo histórico somente lê as chamadas já realizadas.

As 31 perguntas não representam cobertura funcional de todas as 11 ferramentas:
faltam, por exemplo, casos completos de reembolso, pagamento e reclamação. Ainda
assim, todas ficam disponíveis para medir também escolhas desnecessárias de tools.

## Métricas

| Dimensão | Implementação | Entrada |
|---|---|---|
| Relevância do contexto | Ragas `ContextRelevance` | Pergunta e contextos efetivamente expostos |
| Fundamentação | Ragas `Faithfulness` | Pergunta, resposta e contextos efetivamente expostos |
| Relevância da resposta | Ragas `AnswerRelevancy` | Pergunta, resposta e embeddings das perguntas geradas |
| Correção factual adicional | Ragas `FactualCorrectness` | Resposta e referência revisada, quando disponível |
| Chamadas de tools | Ragas `ToolCallAccuracy`, ordem flexível | Chamadas do turno e chamadas de referência |
| Chamadas exatas | Ragas `ToolCallF1` | Conjuntos de nomes e argumentos exatos |
| Consulta às tools necessárias | `required_tool_recall`, determinístico | Nomes esperados e nomes chamados no turno |

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
diferentes. F1 exato é um diagnóstico estrito, não o critério único de sucesso.
No Ragas 0.4.3, `ToolCallAccuracy` verifica as chaves de referência, mas ignora
argumentos extras: `query=bateria, category=instrumentos musicais` pode receber
accuracy 1 mesmo estando incorreto. `ToolCallF1` detecta essa diferença, enquanto
recall dos nomes revela que a ferramenta certa foi chamada. Nenhuma dessas
métricas, isoladamente, comprova uso correto do resultado. Nos casos em que nenhuma
chamada é esperada (por exemplo, pedir número/e-mail antes de consultar um pedido),
F1 fica `skipped: no_expected_calls`; `ToolCallAccuracy` e o recall de nomes
avaliam a ausência correta ou a presença indevida de chamadas.

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

# Avaliar os 29 casos com origem histórica; os dois novos ficam marcados como skipped
uv run python -m app.benchmarks --mode historical

# Conversas novas com o agente, tools e índice atuais
uv run python -m app.benchmarks --mode live

# Seleção de casos e parâmetros do avaliador
uv run python -m app.benchmarks --mode live \
  --case horario_sabado --case bateria_contextual \
  --judge-model gemini-2.5-flash --judge-max-tokens 16384 \
  --metric-timeout 90 --output benchmark-results
```

`GOOGLE_API_KEY` habilita o avaliador Gemini pela API compatível com OpenAI,
os embeddings usados em `AnswerRelevancy` e o agente no modo `live`. O avaliador
é separado do modelo do atendimento; pode ser selecionado com `--judge-model`.
O padrão é `gemini-2.5-flash`, temperatura 0, até 16.384 tokens, timeout de 90s por
métrica. O limite comporta também tokens de raciocínio; saída truncada é erro,
não nota zero. Uma execução chama o provedor várias vezes por caso.

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

Em `live`, cada consulta de política guarda query, limite final, modo `vector`
ou `text_fallback`, candidatos, passagens e latência. Em `historical`, o modo
original e a configuração completa antiga não foram persistidos; aparecem como
desconhecidos. O snapshot é explicitamente da configuração **atual** e não deve
ser atribuído retroativamente à conversa antiga. Alteração do índice ativo
durante o benchmark gera evento e marca a execução como parcial.

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
