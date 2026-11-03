# Análise do tamanho dos chunks do manual

## Constatação

Na análise do manual de políticas versão 2.1, feita em 2026-11-03 com o parser
`structure-aware-v1`, os chunks gerados já são pequenos. O limite configurado de
**1.000 tokens é um teto, não um tamanho fixo**: o parser respeita as subseções
e só as divide entre blocos completos quando excedem esse limite.

As contagens abaixo incluem o título e a hierarquia do chunk, usando o
tokenizador `cl100k_base`:

| Limite configurado | Chunks gerados | Maior chunk |
|---|---:|---:|
| 1.000 tokens | 25 | 275 tokens |
| 512 tokens | 25 | 275 tokens |
| 256 tokens | 26 | 214 tokens |

**Reduzir o limite de 1.000 para 512 tokens não altera nenhum chunk do manual
atual.** O índice teria o mesmo conteúdo textual segmentado da mesma forma;
qualquer variação de notas entre execuções não poderia ser atribuída a uma
redução efetiva dos chunks.

Com limite de 256 tokens, apenas uma subseção passa a ser dividida, aumentando
o total de 25 para 26 chunks. Além disso, a recuperação pode expandir um chunk
para a subseção completa quando ela cabe no orçamento de contexto de 2.000 tokens.
Assim, mesmo essa divisão pode resultar nas mesmas evidências finais para o agente.

## Consequência para os experimentos

**Diminuir o limite de tokens, nas condições atuais, não deve trazer um ganho
significativo apenas por tornar os chunks menores:** eles já estão muito abaixo
do teto configurado. A redução para 512 não produz mudança; a redução para 256
produz uma alteração pequena, potencialmente neutralizada pela expansão estrutural.

Essa conclusão descreve o baixo potencial de mudança do experimento, não uma
melhoria ou piora de qualidade comprovada por benchmark. A execução com chunks
de 256 tokens (`20261103T140727Z-550c5bbe`) foi interrompida antes de concluir o
primeiro caso para evitar gasto com uma comparação de baixo poder informativo.

A prioridade experimental passou a ser aumentar os candidatos recuperados
(`top_k=5` para `top_k=10`) e comparar estratégias de busca, mantendo o chunking
original, até cinco passagens finais e o orçamento de 2.000 tokens. O foco da
avaliação permanece a **RAG Triad**: relevância do contexto, fundamentação nas
evidências e relevância da resposta.

Essa análise vale para o manual e o parser indicados. Deve ser refeita se o
documento crescer, o parser mudar ou a política de expansão for alterada.

## Referências

- [Linha de base do RAG vetorial](benchmark-baseline-rag-vetorial.md).
- [Metodologia do benchmark](benchmarks.md).
