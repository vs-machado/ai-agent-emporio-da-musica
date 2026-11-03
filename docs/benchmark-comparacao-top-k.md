# Benchmark: busca vetorial com top_k 5 versus 10

## Objetivo

Verificar se aumentar os candidatos da busca vetorial de `top_k=5` para
`top_k=10` melhora o atendimento. O foco principal é a **RAG Triad**:
relevância do contexto, fundamentação nas evidências e relevância da resposta.

## Condições da comparação

| Configuração | Linha de base | Experimento |
|---|---|---|
| Execução | `20261103T123611Z-5b61c2dd` | `20261103T140727Z-ba11368a` |
| Branch do experimento | — | `experiment/rag-top-k-10` |
| Estratégia | Vetorial com fallback textual em erro | Mesma estratégia |
| `top_k` | 5 | 10 |
| Teto por chunk | 1.000 tokens | 1.000 tokens |
| Contexto recuperado por consulta | Até 2.000 tokens e cinco passagens | Mesmo limite |
| Agente | DeepSeek-V4.1-Flash via OpenRouter | Mesmo modelo e configuração |
| Avaliador | MiMo-V2.6-Flash via OpenRouter | Mesmo modelo e configuração |
| Embeddings | Gemini Embedding 001, 768 dimensões | Mesmo modelo |
| Casos processados | 38 | 38 |
| Status da execução | `completed` | `partial` |

Foram conferidos e são iguais os hashes do dataset, do prompt e o fingerprint
do índice, além das configurações do agente e do avaliador. Os experimentos
usaram worktrees, ambientes Python e bancos isolados. O banco do teste de
`top_k=10` foi clonado da linha de base, preservando o índice original.

Em ambas as execuções houve **14 consultas de políticas, todas vetoriais**, sem
fallback textual. O status `partial` do experimento decorre de um timeout em
`factual_correctness`, no caso `frete_caldas_novas`. Não houve erros de avaliação
nas três métricas da RAG Triad.

## Resultados da RAG Triad

| Métrica | `top_k=5` | `top_k=10` | Variação aproximada |
|---|---:|---:|---:|
| Relevância do contexto | 89,6% | 89,6% | Sem alteração |
| Fundamentação nas evidências | 82,2% | 77,7% | −4,5 pontos percentuais |
| Relevância da resposta | 71,4% | 73,6% | +2,2 pontos percentuais |

Relevância do contexto e fundamentação foram avaliadas em 36 casos de cada
execução, com dois casos pulados por ausência de contexto recuperado.
Relevância da resposta foi avaliada nos 38 casos de cada execução.
Essas métricas consideram as evidências efetivamente acessíveis ao agente,
inclusive resultados de outras ferramentas, não somente o ranking vetorial.

O aumento de candidatos **não trouxe melhora consistente na RAG Triad**:
o contexto manteve a mesma relevância, a fundamentação caiu e a relevância da
resposta apresentou uma melhora pequena.

## Diagnósticos complementares

| Métrica | `top_k=5` | `top_k=10` |
|---|---:|---:|
| Plano de ferramentas aprovado | 31/38 (81,6%) | 32/38 (84,2%) |
| Argumentos válidos frente aos schemas | 100% em 36 casos | 100% em 36 casos |
| Execução das ferramentas | 100% em 36 casos | 100% em 36 casos |
| Status operacional das respostas | 38 `ok` | 36 `ok`, 2 `limit_reached` |
| Correção factual | 31,9% em 20 casos | 29,6% em 19 casos; 1 timeout |

No experimento, `modelos_teclado_contextual` e `modelos_flauta` atingiram o limite
de execução após buscas repetidas de catálogo. Esses fluxos não dependem
diretamente da busca vetorial de políticas. Portanto, não se pode atribuir essas
falhas ao aumento do `top_k` com base apenas nesta execução.

Correção factual permanece um diagnóstico complementar. Seu resultado pode
ser influenciado por referências e critérios ainda pouco calibrados, além de
possíveis erros reais do atendimento. Não representa uma taxa global de respostas
incorretas. O foco atual do benchmark continua sendo a RAG Triad.

## Consumo registrado

| Componente | `top_k=5` | `top_k=10` | Variação |
|---|---:|---:|---:|
| Tokens do agente | 777.818 | 942.823 | +21,2% |
| Tokens do avaliador | 620.887 | 686.459 | +10,6% |

A linha de base registrou usage em todas as chamadas capturadas. No experimento,
houve duas mensagens do agente e uma tentativa do avaliador sem usage, de modo
que seus totais representam o consumo conhecido. Embeddings não foram
contabilizados e não houve cálculo de custo monetário.

O aumento observado de consumo **não pode ser atribuído integralmente ao
`top_k`**: o agente realizou mais chamadas, inclusive as buscas repetidas de
catálogo. Recuperar dez candidatos não dobra automaticamente os tokens, pois o
contexto final continua limitado a 2.000 tokens e cinco passagens.

## Decisão

**Manter busca vetorial com `top_k=5` como configuração escolhida.**

O teste com dez candidatos não demonstrou benefício consistente na RAG Triad,
e a execução apresentou maior consumo registrado. A pequena melhora de
relevância da resposta e de aprovação dos planos não sustenta, por si só, a
troca do padrão.

Essa é uma decisão prática baseada nas evidências disponíveis, não uma prova
de que `top_k=10` seja universalmente pior. Há apenas uma execução por configuração,
sujeita à variabilidade do agente e do avaliador. O resultado também não isola
nem determina o desempenho de uma busca híbrida.

## Artefatos e documentos relacionados

- Linha de base: `benchmark-results/20261103T123611Z-5b61c2dd/`.
- Experimento: `benchmark-results/20261103T140727Z-ba11368a/`.
- Em cada diretório: `manifest.json`, `events.jsonl`, `partial.json` e `report.json`.
- [Resultado detalhado da linha de base](benchmark-baseline-rag-vetorial.md).
- [Análise do tamanho dos chunks](analise-tamanho-chunks.md).
- [Metodologia do benchmark](benchmarks.md).

Os artefatos detalhados permanecem locais e são ignorados pelo Git; este
documento preserva a comparação resumida no repositório.
