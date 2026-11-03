# Linha de base — RAG vetorial com fallback textual

## Objetivo e escopo

Este documento registra a linha de base vetorial avaliada no commit
`041fe5309a422a3ed9da918b38459f692dea83ab`, estratégia mantida na `develop`.
O foco do benchmark neste momento é a **RAG Triad**:

1. **Relevância do contexto:** o contexto recuperado é relevante para a pergunta?
2. **Fundamentação (faithfulness):** a resposta está sustentada pelas evidências?
3. **Relevância da resposta:** a resposta atende à pergunta do usuário?

Correção factual e métricas de ferramentas são diagnósticos complementares.
Não há uma nota global de aprovação do atendimento. A metodologia está descrita
em [Benchmark do agente com Ragas](benchmarks.md).

## Identificação da execução

| Campo | Valor |
|---|---|
| Execução | `20261103T123611Z-5b61c2dd` |
| Modo | `live`, com avaliador habilitado |
| Início (UTC) | 2026-11-03 12:36:11 |
| Término (UTC) | 2026-11-03 12:55:53 |
| Duração aproximada | 19 minutos e 42 segundos |
| Casos processados | 38 de 38 |
| Status da execução | `completed` |
| Status operacional das respostas | 38 respostas `ok` |
| Erros de métricas | 0 |
| Agente | `openrouter/deepseek/deepseek-v4.1-flash` |
| Avaliador | `xiaomi/mimo-v2.6-flash`, via OpenRouter |
| Avaliador: temperatura / limite / timeout | 0 / 16.384 tokens / 90 s por métrica |
| Embeddings | `gemini-embedding-001`, Google, 768 dimensões |
| Ragas | 0.4.3 |
| Versão de avaliação de tools | `admissible-plans-v1` |

A execução usou um worktree fixo no commit indicado, ambiente Python próprio e
clone do banco em container separado. Alterações paralelas na implementação
híbrida, nas dependências ou no banco de desenvolvimento não alteraram essa base.
`completed` e respostas `ok` indicam conclusão operacional, não correção integral.

## Configuração de recuperação

- Estratégia declarada: `vector_with_text_fallback`, versão 1.
- Busca vetorial por similaridade de cosseno, `top_k=5`.
- Fallback PostgreSQL FTS em português, ranking `ts_rank_cd`, `top_k=5`,
  acionado em erro da busca vetorial; não há fusão entre as duas buscas.
- Chunks estruturais de até 1.000 tokens, sem overlap.
- Expansão para subseção completa quando cabe no orçamento, com deduplicação
  por seção/node.
- Orçamento final de contexto: 2.000 tokens; até cinco passagens por consulta.
- Manual de políticas: versão 2.1, com vetores previamente indexados.

Os eventos registraram **14 consultas de políticas, todas em modo `vector`**.
Não houve fallback textual nesta execução. Essa contagem foi extraída dos eventos
`case_captured` em `events.jsonl`.

## Resultado principal: RAG Triad

As notas abaixo são médias em escala de 0 a 1, também apresentadas em percentual.

| Dimensão | Métrica | Média | Percentual | Avaliados | Pulados | Erros |
|---|---|---:|---:|---:|---:|---:|
| Relevância do contexto | `context_relevance` | 0,8958 | **89,6%** | 36 | 2 | 0 |
| Fundamentação nas evidências | `faithfulness` | 0,8218 | **82,2%** | 36 | 2 | 0 |
| Relevância da resposta | `answer_relevancy` | 0,7141 | **71,4%** | 38 | 0 | 0 |

As duas primeiras métricas foram puladas nos casos sem contexto recuperado; isso
não conta como aprovação nem entra na média. As evidências avaliadas podem incluir
resultados de outras ferramentas e histórico acessível ao agente, além dos trechos
do manual. Portanto, estas são métricas do fluxo de atendimento com RAG, não um
teste isolado de ranking do retriever.

## Correção factual: diagnóstico complementar

`factual_correctness` teve média **0,3185 (31,9%)**, em **20 casos avaliados**;
18 foram pulados por não terem referência factual revisada. Não houve erros
operacionais dessa métrica.

**Essa nota baixa pode ser explicada, em parte, por um benchmark ainda não tão
bem definido para avaliar correção factual.** Referências incompletas, diferenças
de granularidade entre gabarito e resposta e critérios ainda pouco calibrados
podem influenciar a nota. Essa é uma hipótese metodológica, não uma causa já
comprovada pela execução, e não descarta erros reais nas respostas.

O benchmark, neste estágio, **se concentra na avaliação da RAG Triad**. A correção
factual deve ser interpretada como sinal para investigação e revisão dos casos,
não como taxa global de respostas incorretas ou conclusão isolada sobre a qualidade
do retrieval. É necessário analisar respostas, referências e julgamentos caso a
caso antes de atribuir a nota ao agente, ao retriever ou ao avaliador.

## Diagnósticos de ferramentas

| Métrica | Resultado | Casos avaliados |
|---|---:|---:|
| Sucesso do plano admissível | **81,6% — 31/38** | 38 |
| F1 do plano | 92,6% | 38 |
| Recall das ferramentas obrigatórias | 96,1% | 38 |
| Precisão das ferramentas permitidas | 94,1% | 38 |
| Validade dos argumentos frente aos schemas | 100% | 36 |
| Sucesso de execução das ferramentas | 100% | 36 |

Nos dois casos sem chamadas, validade de argumentos e execução foram puladas.
As sete reprovações de plano foram:

| Caso | Motivo registrado |
|---|---|
| `horario_sabado` | Consulta adicional a `get_current_datetime` |
| `endereco_horario` | Consulta adicional a `get_current_datetime` |
| `alternativas_aco` | Consulta adicional a `get_active_promotions` |
| `desconto_contextual` | Ausência de `search_policy` exigida pelo plano |
| `violao_sete_cordas` | Consulta adicional a `get_active_promotions` |
| `reembolso_autenticado_sem_numero` | Consulta adicional ao relógio e ordem divergente |
| `reclamacao_autenticada` | Ausência de `get_order_status` e ordem divergente |

Chamadas adicionais podem ser úteis ao atendimento; parte dessas reprovações
pode refletir contratos restritivos do gabarito. Não devem ser interpretadas
automaticamente como respostas incorretas. Alterações futuras desses contratos
exigem reavaliar ambas as implementações para manter a comparação justa.

## Consumo registrado

| Componente | Chamadas | Tokens de entrada | Tokens de saída | Total |
|---|---:|---:|---:|---:|
| Agente DeepSeek | 91 | 754.461 | 23.357 | **777.818** |
| Avaliador MiMo Flash | 338 | 520.814 | 100.073 | **620.887** |

Todas as chamadas capturadas acima tiveram usage registrado. O consumo de
embeddings não foi contabilizado pelo adaptador atual. Custo monetário não foi
calculado; os totais não constituem conciliação da fatura dos provedores.

## Referências para comparação com busca híbrida

Manter constantes o dataset e os casos selecionados, os dados de atendimento,
manual, chunking e embeddings do índice, modelo/prompt do agente, avaliador MiMo
e seus parâmetros, orçamento final de contexto e critérios de avaliação.
Alterar somente os aspectos de retrieval que se deseja comparar.

| Identificador | Valor |
|---|---|
| Hash do dataset | `19159a5bfb63997c17685b617c77fb4b02108c7a90ae99c863e906a96a854298` |
| Hash do prompt | `246e6c8237bdd1d1723ec76c1ba7373cc358b857a10825b428cf161d9c02fb6d` |
| Fingerprint do índice | `6acbb3d67c183287618a5ce09741fa4f1a8d665731800b695d67355168dc9d02` |
| Hash da configuração de retrieval | `790a81ccefd31caea503d4a689796b7dfa78bee40971ffc485593dd70938210f` |

Comparar primeiro a RAG Triad nos mesmos casos e com os respectivos denominadores,
depois os diagnósticos, latência e consumo. Uma única execução não mede toda a
variabilidade do agente ou do avaliador. Não comparar diretamente estas notas
às da execução parcial anterior com Gemini, pois o avaliador e as condições de
disponibilidade dos embeddings eram diferentes.

## Artefatos de origem

Diretório local, ignorado pelo Git:
`benchmark-results/20261103T123611Z-5b61c2dd/`.

- `manifest.json`: identificação, configuração, dataset e schemas das ferramentas.
- `events.jsonl`: eventos incrementais e diagnósticos de retrieval por consulta.
- `partial.json`: resultados individuais persistidos ao longo da execução.
- `report.json`: consolidação final, métricas e consumo registrado.

Este Markdown preserva o resumo da linha de base no repositório; os artefatos
JSON/JSONL detalhados permanecem locais.
