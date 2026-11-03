# Resultados do experimento de busca híbrida

Data: 3 de novembro de 2026.

## Decisão

Manter na `develop` a recuperação vetorial com fallback textual PostgreSQL já
existente. Preservar a implementação experimental de busca híbrida na branch
`feat/hybrid-bm25-rag`, incluindo configuração, testes e documentação técnica.
Este relatório está presente nas duas branches.

O híbrido funcionou, mas esta rodada não demonstrou ganho global suficiente para
substituir o baseline: a cobertura de políticas empatou e a fundamentação média
das respostas diminuiu. A redução de tokens observada não demonstra, isoladamente,
um benefício causado pelo BM25.

## Implementação avaliada

```text
Busca vetorial pgvector ─┐
                        ├─ RRF → expansão/deduplicação → evidências → agente
Busca lexical BM25 ──────┘
```

- Embeddings: `gemini-embedding-001`, 768 dimensões, similaridade cosseno.
- BM25 Okapi em memória sobre todos os chunks da versão ativa; `k1=1.2`, `b=0.75`
  e IDF positivo `log(1 + (N - df + 0.5) / (df + 0.5))`.
- Normalização portuguesa de documentos e consulta via `ts_debug` do PostgreSQL.
- Top-k vetorial: 5; top-k BM25: 10.
- Fusão RRF por `node_id`, pesos iguais, constante 60.
- Mesma versão do índice nos dois caminhos; expansão e deduplicação após a fusão.
- Até cinco evidências finais, com orçamento aproximado de 2.000 tokens.
- **Sem reranker.** RRF combina posições dos rankings; não usa um modelo adicional
  para avaliar a relevância dos pares pergunta/trecho.
- Falha de um caminho mantém o outro disponível; falha dos dois informa
  indisponibilidade. Não houve migration nem regeneração dos embeddings.

O baseline usa busca vetorial e executa FTS PostgreSQL com `ts_rank_cd` somente em
erro vetorial. Esse fallback não é BM25 nem recuperação híbrida.

## Execuções e condições de comparação

| Item | Baseline vetorial | Híbrido |
|---|---|---|
| Run ID | `20261103T123611Z-5b61c2dd` | `20261103T130206Z-d56220c1` |
| Modo | `live` | `live` |
| Casos executados | 38 | 38 |
| Status do relatório | `completed` | `partial` |
| Modelo do agente | `openrouter/deepseek/deepseek-v4.1-flash` | Mesmo |
| Avaliador | `xiaomi/mimo-v2.6-flash` via OpenRouter | Mesmo |

Ambas usaram o mesmo prompt, dataset, índice ativo e orçamento de contexto:

- Dataset SHA-256: `19159a5bfb63997c17685b617c77fb4b02108c7a90ae99c863e906a96a854298`.
- Índice: `17bdd877-f522-5605-919a-72525bbbdc4b`.
- Fonte: `políticas_da_loja.pdf`, versão 2.1, 25 chunks.

A execução híbrida terminou em `2026-11-03T13:44:46Z`, após aproximadamente
43 minutos. O status `partial` decorre de um timeout de `factual_correctness` do
avaliador no caso `rastreamento_sem_identificadores`. O agente respondeu, e as
demais métricas desse caso foram calculadas. Não foi uma falha de busca híbrida.

Artefatos locais, quando disponíveis:

- `benchmark-results/20261103T123611Z-5b61c2dd/report.json` — baseline.
- `benchmark-results/20261103T130206Z-d56220c1/report.json` — híbrido.
- `events.jsonl` de cada execução — timestamps por etapa.
- `benchmark-results/20261103T130206Z-d56220c1/retrieval-comparison.md` — diagnóstico
  separado de recuperação. O diretório de artefatos é ignorado pelo Git; este
  documento preserva os principais resultados no histórico do repositório.

## Qualidade das respostas e uso de ferramentas

| Métrica | Vetorial | Híbrido |
|---|---:|---:|
| Relevância do contexto (`ContextRelevance`) | 0,8958 | 0,8889 |
| Fundamentação (`Faithfulness`) | 0,8218 | 0,7821 |
| Relevância da resposta (`AnswerRelevancy`) | 0,7141 | 0,7230 |
| Correção factual (`FactualCorrectness`) | 0,3185 | 0,3447 |
| Plano correto de ferramentas | 31/38 | 32/38 |
| Recall das ferramentas obrigatórias | 0,9605 | 0,9605 |
| Precisão das ferramentas permitidas | 0,9408 | 0,9539 |
| Sucesso de execução das ferramentas | 1,0000 | 1,0000 |
| Validade dos schemas de ferramentas | 1,0000 | 1,0000 |

Relevância do contexto e fundamentação tiveram 36 casos avaliados e dois pulados
por ausência de contexto recuperado. Execução e schemas tiveram 36 avaliados e
dois pulados por ausência de chamadas. Correção factual teve 20 avaliados no
baseline e 19 no híbrido, com um timeout e 18 casos sem referência em ambos;
portanto, suas médias não são uma comparação direta sobre a mesma amostra.

As médias incluem cenários de catálogo e pedidos, não somente políticas.
Fundamentação caiu aproximadamente 4 pontos percentuais; não é possível atribuir
essa queda diretamente ao BM25 sem analisar os casos e suas evidências.

## Cobertura de recuperação das políticas

Diagnóstico separado com dez perguntas de `app/rag/evaluation.py` e embeddings
reais, incluindo PIX/promoção, parcelamento, trocas, garantia/exclusões e frete:

| Métrica | Vetorial | Híbrido |
|---|---:|---:|
| Recall médio das seções esperadas | 1,0 | 1,0 |
| Perguntas com todas as seções esperadas | 10/10 | 10/10 |
| Falhas dos caminhos de busca | 0 | 0 |

Ambas respeitaram o orçamento. O conjunto já atingia o teto com o baseline; não
houve ganho demonstrado de cobertura. Esse diagnóstico não mede precisão dos
trechos nem qualidade da resposta gerada.

## Consumo de tokens

| Consumo do agente | Vetorial | Híbrido | Variação |
|---|---:|---:|---:|
| Entrada | 754.461 | 671.031 | −11,06% |
| Saída | 23.357 | 18.445 | −21,03% |
| Total | 777.818 | 689.476 | −11,36% |
| Média total por caso | 20.469 | 18.144 | −11,36% |
| Chamadas ao modelo | 91 | 84 | −7 chamadas |

Economia observada no agente: **88.342 tokens**. As execuções tiveram números
diferentes de chamadas e tamanhos de saída; não há evidência de que BM25 sozinho
tenha causado a redução.

O avaliador registrou 620.887 tokens no baseline e 567.612 no híbrido. A contagem
híbrida é parcial: 337 das 338 chamadas têm uso registrado, devido ao timeout.
O avaliador não participa do atendimento ao cliente. Tokens de embeddings não
foram registrados, e o custo monetário não foi calculado; preços e cache também
influenciam a cobrança. BM25 e RRF não fazem chamadas adicionais a LLMs.

## Latência do agente e duração do benchmark

Tempos estimados pelos timestamps entre `case_started` e `case_captured` dos
38 casos, antes do avaliador. Incluem preparação do caso, execução do agente,
ferramentas e captura de evidências; não medem latência HTTP externa nem tempo
até o primeiro token.

| Tempo até captura da resposta | Vetorial | Híbrido |
|---|---:|---:|
| Média | 3,92 s | 3,25 s |
| Mediana | 2,93 s | 2,94 s |
| P95 (posto mais próximo) | 9,08 s | 6,14 s |
| Máximo | 10,18 s | 7,56 s |

A mediana permaneceu praticamente igual. Média e cauda menores nesta rodada não
demonstram aceleração causada pelo BM25, pois houve menos chamadas ao modelo.

No diagnóstico de recuperação, a mediana foi 242 ms no vetorial e 301 ms no
híbrido: diferença observada de aproximadamente 59 ms por busca. Híbrido foi
executado primeiro, sem aquecimento controlado; rede e provedor não foram
isolados, portanto essa diferença não mede apenas o custo do BM25.

O benchmark completo demorou principalmente pela avaliação: 338 chamadas ao
avaliador, com casos e métricas sequenciais. `elapsed_seconds` do relatório inclui
a avaliação e não deve ser interpretado como tempo de resposta ao cliente.

## Limitações e próximos experimentos

- Uma execução por estratégia, com geração e julgamento sujeitos a variabilidade.
- Corpus pequeno e diagnóstico de recuperação com apenas dez perguntas.
- Nenhum ganho de cobertura observado; médias globais não isolam políticas.
- Um timeout impede comparação completa de correção factual.
- A versão BM25 experimental lê e normaliza o corpus ativo a cada consulta;
  crescimento significativo da base exigiria rever essa estratégia.

Antes de promover o híbrido, revisar casos de políticas com queda de
fundamentação, conferir ruído nas evidências e ampliar perguntas com termos
específicos, paráfrases e exceções. Repetir comparações mantendo condições iguais.
Se houver problema de ordenação, comparar vetorial, vetorial com reranker e
híbrido com reranker, medindo também cobertura de exceções, tokens e latência.
Reranker não foi implementado nem avaliado neste experimento.
