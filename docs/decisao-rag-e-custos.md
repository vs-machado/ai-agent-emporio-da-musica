# Decisão de RAG e estimativas de custo

## Decisão atual de RAG

**Vamos manter a busca vetorial com `top_k=5`**, com fallback textual em caso de
erro da busca vetorial. A configuração escolhida usa até cinco passagens e
2.000 tokens de contexto recuperado por consulta.

A decisão se apoia nos experimentos com DeepSeek como agente e MiMo-V2.6-Flash
como avaliador, com foco na **RAG Triad**:

- Aumentar `top_k` de 5 para 10 não melhorou a relevância do contexto: 89,6%
  em ambas as execuções.
- A fundamentação caiu de 82,2% para 77,7%; a relevância da resposta subiu
  de 71,4% para 73,6%, sem ganho consistente no conjunto da tríade.
- A execução com `top_k=10` registrou mais tokens, embora parte dessa diferença
  possa decorrer da variabilidade do agente e de buscas repetidas de catálogo.
- A busca híbrida com BM25 e fusão RRF, sem reranker, também não mostrou ganho
  consistente na tríade: relevância do contexto de 88,9%, fundamentação de 78,2%
  e relevância da resposta de 72,3%. A redução de tokens observada é interessante,
  mas não comprova benefício causado pelo BM25 isoladamente.
- Os chunks do manual já são pequenos: o maior tem 275 tokens. Reduzir o teto
  de 1.000 para 512 tokens não altera a segmentação; reduzir para 256 altera
  apenas uma subseção e pode ser neutralizado pela expansão estrutural.

A correção factual é um diagnóstico complementar: sua nota pode refletir
limitações nas referências e na definição do benchmark, além de possíveis erros
reais. O foco atual da avaliação é relevância do contexto, fundamentação e
relevância da resposta.

Esta escolha não prova superioridade universal de `top_k=5` nem inferioridade
geral da busca híbrida. Nesta rodada, os ganhos não foram suficientes para
justificar a troca da configuração. Implementações experimentais podem continuar
em branches próprias; esta seção registra a configuração escolhida para orientar
a consolidação do projeto.

### Comparação resumida dos três benchmarks

Cada execução processou os mesmos 38 casos, usando DeepSeek como agente,
MiMo-V2.6-Flash como avaliador, o mesmo dataset, prompt, índice e orçamento de
2.000 tokens, com até cinco passagens finais. No híbrido, foram recuperados até
cinco candidatos vetoriais e dez BM25, combinados por RRF, sem reranker.

| Métrica | [Vetorial, top_k=5](benchmark-baseline-rag-vetorial.md) | [Híbrido BM25 + RRF](resultados-busca-hibrida.md) | [Vetorial, top_k=10](benchmark-comparacao-top-k.md) |
|---|---:|---:|---:|
| Relevância do contexto | **89,6%** | 88,9% | **89,6%** |
| Fundamentação nas evidências | **82,2%** | 78,2% | 77,7% |
| Relevância da resposta | 71,4% | 72,3% | **73,6%** |
| Plano de ferramentas aprovado | 31/38 | 32/38 | 32/38 |
| Tokens registrados do agente | 777.818 | **689.476** | 942.823¹ |
| Tokens registrados do avaliador | 620.887 | 567.612¹ | 686.459¹ |
| Erros nas métricas da RAG Triad | 0 | 0 | 0 |
| Status do relatório | `completed` | `partial`² | `partial`² |

Relevância do contexto e fundamentação têm 36 casos avaliados em cada execução;
relevância da resposta tem 38. Os dois casos sem contexto foram pulados nas duas
primeiras métricas, não aprovados automaticamente.

¹ Consumo parcial por chamadas/mensagens sem usage. Embeddings não foram
contabilizados em nenhuma execução. Diferenças de tokens também refletem o
número de chamadas e o comportamento do agente, não somente a estratégia de busca.

² Um timeout de correção factual em cada execução, sem erros na RAG Triad.
Há apenas uma execução por configuração; as diferenças observadas não demonstram
causalidade nem significância estatística.

### Tokens e custo: vetorial versus híbrido

O consumo abaixo soma **todas as chamadas ao DeepSeek capturadas** nos 38 casos,
incluindo instruções, schemas das ferramentas, histórico, tool calls, resultados
das ferramentas, evidências do RAG e respostas. Não considera apenas as mensagens
visíveis ao cliente. Consultas SQL, BM25 e RRF não geram tokens do DeepSeek por si só.

**Os totais de tokens e as estimativas de custo incluem o tool calling:** a geração
dos nomes/argumentos das ferramentas, os resultados enviados de volta ao modelo
e as chamadas intermediárias necessárias antes da resposta final. Esses tokens
já estão contabilizados na tabela; não é necessário acrescentar outro valor para
o tool calling. A execução da ferramenta em si pode ter custos de infraestrutura
ou de APIs externas, que não fazem parte do custo de tokens do DeepSeek.

| Consumo do agente no benchmark | Vetorial, `top_k=5` | Híbrido BM25 + RRF |
|---|---:|---:|
| Chamadas ao DeepSeek | 91 | 84 |
| Tokens de entrada | 754.461 | 671.031 |
| Tokens de saída | 23.357 | 18.445 |
| Total de tokens | 777.818 | 689.476 |
| Tokens de entrada lidos de cache, já incluídos na entrada | 524.800 | 437.248 |
| Custo estimado dos 38 casos, sem desconto de cache | US$ 0,0456 | US$ 0,0394 |

Tarifas consultadas em **3 de novembro de 2026** para
`deepseek/deepseek-v4.1-flash` no [OpenRouter](https://openrouter.ai/api/v1/models):
US$ 0,045 por milhão de tokens de entrada sem cache, US$ 0,012 por milhão de
entrada com cache e US$ 0,50 por milhão de saída.

Para projetar o atendimento, foram consultadas 20 conversas do banco, com
129 perguntas e 129 respostas: média de **6,45 perguntas respondidas por conversa**,
ou 12,9 mensagens visíveis. Assumindo uma conversa por pessoa, 20 pessoas por dia
e 30 dias de atendimento por mês:

| Projeção de custo do DeepSeek | Vetorial, `top_k=5` | Híbrido BM25 + RRF |
|---|---:|---:|
| Por atendimento, sem desconto de cache | US$ 0,0077 | US$ 0,0067 |
| Por dia, sem desconto de cache | US$ 0,155 | US$ 0,134 |
| Por mês, sem desconto de cache | **US$ 4,65** | **US$ 4,01** |
| Por mês, com a proporção de cache observada em cada benchmark | **US$ 2,88** | **US$ 2,55** |

**Nesse volume, a redução de gasto é irrisória em termos absolutos:** cerca de
**US$ 0,34 a US$ 0,63 por mês**, usando os valores não arredondados. A redução
estimada é de 11,7% com o cache observado ou 13,6% sem desconto de cache, mas não
justifica sozinha a complexidade adicional da solução híbrida.

**A busca vetorial possui uma estrutura mais simples:** um caminho principal de
recuperação sem normalização lexical adicional, busca BM25 e etapa de fusão RRF.
O fallback textual permanece disponível em caso de erro vetorial. Para o corpus
atual, essa simplicidade de implementação, manutenção e diagnóstico, somada à
ausência de ganho consistente na RAG Triad, sustenta manter o vetorial com
`top_k=5`.

As projeções extrapolam o consumo médio por turno dos benchmarks para o tamanho
médio das conversas; não são um replay de conversas completas nem uma fatura.
Mais histórico pode aumentar o consumo por turno, e a amostra do banco é pequena.
A diferença observada também inclui menos chamadas e respostas menores no híbrido,
não sendo prova de economia causada pelo BM25. Os valores cobrem somente o
DeepSeek no atendimento: excluem embeddings Google, avaliador do benchmark,
infraestrutura e taxas de pagamento/câmbio.

### Resultados e justificativas

- [Linha de base da busca vetorial](benchmark-baseline-rag-vetorial.md).
- [Resultados da busca híbrida BM25 + RRF](resultados-busca-hibrida.md).
- [Comparação de top_k 5 versus 10](benchmark-comparacao-top-k.md).
- [Análise do tamanho dos chunks](analise-tamanho-chunks.md).
- [Metodologia do benchmark](benchmarks.md).
