# Tools de atendimento

As tools seguem os cenários de `casos-cobertos-pelo-bot.md` e as decisões de
`decisoes-tecnicas.md`. São adaptadores `StructuredTool` do LangChain; regras,
consultas e transações ficam em `app/services/`, sem depender de LLM.

## Construção

```python
from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.tools import build_tools

settings = Settings()
engine = create_database_engine(settings)
retriever = LlamaIndexPolicyRetriever(engine, settings)
tools = build_tools(create_session_factory(engine), policy_retriever=retriever)
catalog = next(tool for tool in tools if tool.name == "search_products")
result = catalog.invoke({"category": "violões", "max_price": "1000.00"})
```

O banco deve estar preparado pelas migrations e pela ingestão. O Compose já
executa essa preparação. Fora do Docker, use `uv run alembic upgrade head` e
`uv run python -m app.ingestion`. A migration `0003` acrescenta `complaints`.
A factory não inicia um agente, não chama provedores e não cria endpoints de chat.
Para consultar o manual, prepare também o índice conforme [RAG das políticas](rag.md).

## Cobertura

| Tool | Casos | Comportamento |
|---|---|---|
| `get_current_datetime` | 1, referências temporais | Data/hora atual, dia da semana e fuso da loja, sem consultar feriados ou expediente |
| `search_products` | 2 | Nome, categoria, marca no nome, preço atual e disponibilidade; paginação limitada |
| `get_product_details` | 2, 4 | Preços, estoque, specs e alternativas disponíveis da mesma categoria |
| `get_active_promotions` | 4 | Campanhas ativas e consulta opcional de inativas, sem inventar vigência |
| `get_order_status` | 5, 6, 7, 9 | Pedido e rastreio após conferir ID e e-mail; não aprova trocas ou garantias |
| `calculate_refund` | 6 | Total registrado para devolução integral ou estimativa proporcional de unidades iguais, sempre sujeita à confirmação |
| `calculate_payment` | 3, 4 | Carrinho consultado, PIX não cumulativo, crédito, débito, boleto e elegibilidade de combinação |
| `calculate_shipping` | 5 | Regra local, prazos estimados ou necessidade de informação/cotação |
| `register_complaint` | 8, 9 | Protocolo persistente e idempotência, sem afirmar encaminhamento operacional |
| `search_policy` | 1, 3–10 | Recuperação LlamaIndex/pgvector do manual, expansão estrutural e fallback textual |

## Contratos e regras

- `get_current_datetime` não recebe parâmetros. Usa o relógio do servidor e o fuso
  IANA `America/Campo_Grande`, independentemente do fuso configurado no sistema.
  Retorna data/hora local e UTC em ISO 8601, dia da semana, offset e indicador de
  horário de verão. O offset atual é UTC−4; regras históricas vêm do `zoneinfo`,
  com `tzdata` como base de fusos disponível para ambientes sem dados do sistema.
  A tool funciona sem banco ou chaves. Não informa `is_open`: feriados e horários
  especiais não são verificados, e `holiday_calendar_available=false` explicita isso.
  Os horários regulares devem ser consultados por `search_policy`.
- Schemas ficam em `app/schemas/`, separados por domínio, com descrições em português,
  campos extras proibidos, limites de resultados, quantidades e tamanhos de texto.
- Serviços recebem objetos Pydantic validados; as tools aceitam argumentos JSON.
- Saídas comerciais usam strings decimais com duas casas, sem imprecisão de `float`.
- Buscas ignoram caixa e acentos latinos; `%` e `_` são tratados literalmente.
- Faixas e ordenação de preço consideram a promoção registrada como ativa, antes do PIX.
  Busca e promoções usam `limit`, `offset` e `has_more`, sem truncamento silencioso.
- Disponibilidade exige `status=active` e estoque positivo. Simulações também
  verificam a quantidade solicitada. Nenhuma tool reserva ou reduz estoque.
- Os CSVs atuais têm no máximo uma promoção ativa por produto. As consultas
  pressupõem esse cenário: validação e resolução de múltiplas promoções ativas
  não são tratadas nesta etapa, nem há regra de escolha pelo maior desconto.
  Também não há validação temporal de vigência, pois a fonte não fornece datas.
- PIX aplica 5% apenas aos itens sem promoção. Arredondamento monetário é
  `ROUND_HALF_UP`; o desconto é arredondado no total de cada linha do carrinho.
- Parcelas distribuem centavos entre as primeiras parcelas para preservar o total.
  O menor valor efetivo é usado na checagem de R$ 80,00 e R$ 100,00.
  Condições não permitidas retornam `not_allowed`; não devem ser oferecidas.
- A exceção abaixo de R$ 50,00 no parcelamento em até 3x retorna
  `requires_confirmation`. Pagamento combinado acima de R$ 2.000,00 também
  exige confirmação de distribuição e descontos; não há cálculo automático da divisão.
- A simulação de frete usa o subtotal com promoções, antes do desconto por
  pagamento. Essa é uma convenção do protótipo a confirmar com a equipe.
  Exatamente R$ 500,00 exige confirmação, pois não há regra explícita no manual.
- A região de entrega precisa ser confirmada pelo cliente. Fora da região local,
  nenhum valor é calculado sem CEP, peso, dimensões e integração de cotação.
- ID e e-mail são a verificação inicial de pedidos, não autenticação real.
  E-mail errado e pedido inexistente têm a mesma resposta. Cadastro de clientes,
  notas internas e preços unitários históricos não são retornados.
- A previsão de entrega nunca é convertida em data real de recebimento.
- `calculate_refund` exige pedido e e-mail correspondentes. Omitir `items` ou usar
  `null` significa devolução integral; uma lista identifica os produtos e quantidades
  desejados, sem duplicar produtos nem exceder o que foi comprado. Selecionar todos
  os itens e quantidades também corresponde à devolução integral.
- Na devolução integral, `refund_amount_brl` referencia `orders.total_brl` e
  `amount_kind=recorded_total_reference`. Para devolução parcial de um pedido que
  contém apenas um produto, retorna `amount_kind=proportional_estimate`, calculado
  por total registrado × quantidade a devolver ÷ quantidade comprada. Só o resultado
  final é arredondado para centavos com `ROUND_HALF_UP`. O rateio é convenção do
  protótipo, não fórmula definida no manual nem preço unitário histórico confirmado.
- Pedidos com produtos diferentes não recebem rateio parcial. Pedidos cancelados
  exigem verificar eventual pagamento, sem presumir ausência de pagamento ou
  reembolso já realizado. Nesses casos, `refund_amount_brl=null`, não zero.
- Uma consulta de reembolso bem-sucedida retorna `requires_confirmation`, mesmo
  quando há valor de referência. O total não discrimina frete/descontos e a fonte
  não comprova pagamento, reembolsos anteriores ou elegibilidade. As flags
  `payment_verified`, `eligibility_checked`, `refund_approved` e `refund_executed`
  permanecem falsas. A operação não altera pedidos, estoque ou registros financeiros.
- Reclamações exigem e-mail e relato. Pedido é opcional e, quando informado,
  precisa corresponder ao e-mail. O chamador deve gerar uma `request_id` UUID
  por solicitação e reutilizá-la nos retries. O agente não deve solicitar essa
  chave ao cliente; a integração futura deve fornecer e preservar esse contexto.
  A mesma chave com dados diferentes retorna `request_conflict` sem alterar o registro.
  Na integração do agente, o campo técnico é ocultado do modelo e o middleware
  deriva a chave do `ChatRequest.request_id`, preservando-a em retries do turno.
- Registro não notifica a equipe: `forwarded_to_team=false`. O retorno explicita
  a ausência de garantia operacional de resposta em 24 horas úteis.
- Falhas de banco e transporte retornam `unavailable`, sem SQL, parâmetros,
  credenciais ou valores pessoais. Erros de argumentos do LangChain recebem
  mensagem genérica; o agente deve corrigir a entrada antes de tentar novamente.

## Políticas e próximos passos

`build_tools(sessions, policy_retriever=...)` aceita um objeto que implemente
`PolicyRetriever.search(query, limit)`. Cada `PolicyPassage` contém `text`, `source`,
`page_start`, `page_end`, `section`, `title` e `document_version`. O padrão e o
máximo são cinco evidências finais, dentro do orçamento total configurado em tokens.
O limite antigo de 2.000 caracteres por trecho foi removido para preservar unidades completas.
O serviço não gera respostas, só devolve evidências. Sem retriever, a tool retorna
`unavailable`; sem resultados, retorna `not_found`. Nenhum desses estados permite
inventar políticas ou responder como se a consulta tivesse funcionado.

O lifespan da API já constrói o retriever e as tools em `app.state`, sem indexar
o PDF ou chamar provedores na inicialização. Sem índice ativo, a consulta retorna
`unavailable`; sem chave de embeddings, um índice existente pode ser consultado
pelo fallback textual. O agente e o `ChatService` já estão integrados; veja
[Agente de atendimento](agente.md). Histórico persistente, API de chat, autenticação
e processo humano continuam como etapas próprias. Não há busca baseada no documento
de casos ou nos CSVs.

## Verificação

```bash
uv run pytest tests/test_tools_contracts.py
TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/emporio_test uv run pytest
```

Os testes de integração usam banco dedicado terminado em `_test`, aplicam as
migrations e limpam as tabelas entre cenários, incluindo reclamações.
