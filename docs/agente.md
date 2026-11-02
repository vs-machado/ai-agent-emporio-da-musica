# Agente de atendimento

O atendimento usa `ChatService` → agente LangChain (`create_agent`) → modelos
LiteLLM e tools estruturadas. O RAG continua separado, atrás da tool `search_policy`.

## Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `app/schemas/chat.py` | Entrada e resposta pública |
| `app/services/chat.py` | Fachada, contrato do agente e validação do histórico |
| `app/models/conversation.py` | Conversas por UUID e turnos completos em JSON |
| `app/routes/chat.py` | Endpoint HTTP público `POST /chat` |
| `app/ui.py` | Interface Gradio, estado visual e retries |
| `app/agents/factory.py` | Composição das dependências |
| `app/agents/models.py` | Adaptador `langchain-litellm`, configuração e classificação de falhas |
| `app/agents/runner.py` | Agente padrão, fallback, limites e idempotência das reclamações |
| `app/agents/prompt.py` | Instruções de atendimento em português |

O lifespan da API prepara `app.state.chat_service` com as onze tools, sem chamar
provedores. Sem chave Google, o serviço retorna `unavailable`; isso permite
inicializar e testar a aplicação sem inventar respostas de atendimento.

O endpoint `POST /chat` usa esse serviço e retorna somente `ChatResponse`.
Contrato, exemplos e códigos HTTP estão em [API de chat](api-chat.md).
O Gradio está disponível em `/ui/` e usa o mesmo serviço, conforme
[Interface de atendimento](interface-chat.md).
O uso do ViaCEP e o fluxo de atendimento de frete estão descritos em
[Consultas externas](consultas-externas.md).

O prompt orienta o uso natural do nome quando já informado, empatia nas reclamações
e encerramento cordial. Também explicita a interpretação de disponibilidade/status,
a apresentação conjunta dos valores de promoções, consultas complementares para
condições/exceções e a distinção entre preço atual, valor histórico e total sem frete.
Pedidos de exclusão de dados são direcionados ao canal humano recuperado do manual;
não há operação de exclusão ou encaminhamento automático.

`calculate_refund` consulta o total registrado para uma devolução integral ou
calcula uma estimativa proporcional quando o pedido inteiro contém apenas um
produto. Não usa preços atuais nem interpreta essa convenção como regra do manual.
O cliente deve informar número/e-mail do pedido e esclarecer se quer devolver
tudo ou quais produtos e quantidades. Pedidos cancelados exigem verificação de
eventual pagamento; devoluções parciais com produtos diferentes não recebem rateio.
Todos esses resultados exigem confirmação da equipe, sem aprovar ou executar reembolso.

Para referências a hoje/agora e expediente atual, o agente dispõe de
`get_current_datetime`, sem parâmetros, no fuso `America/Campo_Grande` (atualmente
UTC−4, uma hora atrás de Brasília). A tool usa o relógio do servidor convertido por
`zoneinfo`, sem API externa, banco ou dependência do fuso local do container.
Não consulta feriados ou horários especiais. O agente deve combinar o instante
atual com as políticas recuperadas e apresentar o expediente regular como tal
quando essas exceções não estiverem confirmadas, sem garantir abertura ou retorno
humano em uma data/hora específica.

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `GOOGLE_API_KEY` | vazia | Necessária para o agente primário e novos embeddings |
| `OPENROUTER_API_KEY` | vazia | Habilita o fallback opcional DeepSeek |
| `CHAT_PRIMARY_MODEL` | `gemini/gemini-2.5-flash-lite` | Identificador LiteLLM do primário |
| `CHAT_FALLBACK_MODEL` | `openrouter/deepseek/deepseek-v4.1-flash` | Identificador LiteLLM do fallback |
| `CHAT_MODEL_TIMEOUT` | `20` | Timeout em segundos por tentativa de provedor |
| `CHAT_MAX_OUTPUT_TOKENS` | `2048` | Máximo de tokens de saída por chamada |
| `CHAT_MAX_MODEL_CALLS` | `6` | Rodadas de modelo por turno |
| `CHAT_MAX_TOOL_CALLS` | `12` | Execuções de tools por turno |
| `CHAT_HISTORY_MAX_MESSAGES` | `100` | Limite do histórico de entrada |

Os identificadores acima refletem as decisões do projeto; a disponibilidade e o
comportamento dos modelos ainda precisam ser conferidos com os provedores reais.
As chaves são carregadas como segredos em `Settings`, sem exportá-las globalmente.
Chaves vazias são normalizadas para ausência. Apenas a chave OpenRouter não
substitui a configuração obrigatória do primário.

## Executar um turno

Com o banco preparado pelas migrations, ingestão e indexação do manual:

```bash
uv sync --locked
export LITELLM_LOCAL_MODEL_COST_MAP=True
uv run python -m app.agents "Quais violões Yamaha estão disponíveis?"
```

No Docker, depois da preparação dos serviços:

```bash
docker compose run --rm --no-deps api python -m app.agents "Qual é a política de devolução?"
```

O comando imprime a resposta pública em JSON, incluindo `conversation_id`, e encerra
com código 0 em caso de sucesso ou 1 em caso de indisponibilidade/limite. Cada execução
carrega o histórico e persiste o turno no PostgreSQL (migration `0005`). Sem UUID informado,
inicia uma conversa. Para continuar e para repetir uma solicitação já salva:

```bash
uv run python -m app.agents "Quais as condições no PIX?" --conversation-id UUID_DA_CONVERSA
uv run python -m app.agents "Quais as condições no PIX?" --conversation-id UUID_DA_CONVERSA --request-id UUID_DO_TURNO
```

Reutilize ambos os IDs e o mesmo texto em retries. Sem chaves de modelo, o turno
também é salvo, com resposta `unavailable`.

A imagem define `LITELLM_LOCAL_MODEL_COST_MAP=True` para usar o catálogo de custos
empacotado no LiteLLM, sem atualização remota durante a importação. Na execução
local, exporte a variável antes de iniciar o processo, como no exemplo acima.
Isso não interfere nas chamadas de inferência aos provedores quando configuradas.

## Usar o ChatService

```python
from app.agents.factory import build_chat_service
from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.rag.retrieval import LlamaIndexPolicyRetriever
from app.schemas.chat import ChatRequest
from app.tools import build_tools

settings = Settings()
engine = create_database_engine(settings)
try:
    sessions = create_session_factory(engine)
    tools = build_tools(sessions, LlamaIndexPolicyRetriever(engine, settings))
    service = build_chat_service(settings, tools, session_factory=sessions)
    first = service.chat(ChatRequest(message="Quero um violão Yamaha"))
    second = service.chat(
        ChatRequest(
            conversation_id=first.response.conversation_id,
            message="Quais as condições no PIX?",
        )
    )
    print(second.response.model_dump())
finally:
    engine.dispose()
```

`ChatRequest` aceita texto, UUID da conversa e ID do turno, sem papéis de sistema ou
resultados de tools enviados por clientes. Os IDs são gerados quando omitidos.
Com `session_factory`, o serviço recupera o histórico pelo UUID e rejeita `history`
manual. Sem essa dependência, o uso interno offline continua aceitando histórico
confiável em memória.

`conversations` identifica a conversa; `conversation_turns` armazena cada turno em
ordem, com texto original, resposta pública e mensagens LangChain completas.
A serialização preserva chamadas, argumentos, IDs, resultados de tools e metadados
do provedor, incluindo assinaturas necessárias para continuar o diálogo.

`ChatTurn.messages` contém apenas as mensagens novas. O prompt de sistema não
faz parte desse retorno. `ChatTurn.response` contém `conversation_id`, `request_id`, `status`,
`message`, `model`, `used_fallback` e `actions` com nome, estado e referência
da operação quando houver. Não expõe raciocínio interno nem exceções de provedor.
O transcript interno pode conter dados pessoais fornecidos para as tools; não
deve ser publicado como resposta HTTP ou confundido com histórico visual.

O serviço é síncrono. O endpoint HTTP executa na thread pool do FastAPI, fora do
event loop. A instância pode atender turnos independentes;
contadores, chave e rota de fallback são locais a cada invocação. Um lock de linha
no PostgreSQL serializa turnos da mesma conversa durante leitura, execução e gravação,
inclusive entre processos. Conversas diferentes podem executar em paralelo.
Resposta e mensagens são gravadas juntas, sem turnos parciais. O limite de histórico
é validado antes de executar o agente, sem truncar pares de tools.

Falhas inesperadas fazem rollback do turno. Efeitos de tools usam suas próprias
transações: uma reclamação já registrada permanece protegida pela chave de
idempotência mesmo se a gravação final do chat falhar.

## Fallback e interrupções

- Timeout, falha de conexão, rate limit e erros 5xx permitem uma tentativa no
  fallback configurado. Sem sua chave, a falha resulta em `unavailable`.
- Erros 4xx de autenticação/configuração, contexto excedido, argumentos JSON
  malformados e respostas sem texto público não acionam o fallback.
- O fallback recebe as mensagens e resultados já disponíveis. Não reinicia o
  agente nem executa novamente uma tool concluída. Após a troca, continua sendo
  usado naquele turno; o turno seguinte começa pelo primário.
- Retries automáticos ficam desabilitados tanto no adaptador LangChain quanto
  na chamada LiteLLM. Com seis rodadas e fallback, pode haver até sete tentativas
  de provedor, pois apenas a primeira falha do primário permite a troca.
- Ao atingir o limite de tools ou rodadas, o agente encerra com `limit_reached`
  e mensagem em português, mantendo o pareamento dos resultados de tools.
- Se uma reclamação já tiver sido registrada antes de uma falha de modelo ou
  limite, a resposta de interrupção preserva seu protocolo e explicita que não
  houve encaminhamento à equipe.

## Idempotência das reclamações

A aplicação deriva a chave interna por `uuid5(request_id, "register_complaint")`.
O schema apresentado ao modelo omite `request_id`; o middleware injeta o valor
antes de executar a tool e sobrescreve qualquer valor eventualmente gerado pelo
modelo. O schema original da tool e as validações do serviço permanecem intactos.

Repetições da mesma solicitação devem usar o mesmo `ChatRequest.request_id`.
Isso protege a gravação de uma reclamação mesmo se a resposta final do provedor
falhar. Uma chave diferente representa outra solicitação. Duas reclamações com
dados diferentes no mesmo turno conflitam; há uma reclamação por solicitação.
Com persistência, repetir `(conversation_id, request_id)` devolve o turno salvo,
sem chamar modelo ou tools novamente. Texto diferente com a mesma chave é rejeitado.
Isso também vale para turnos salvos com `unavailable` ou `limit_reached`; uma nova
tentativa de atendimento exige outro `request_id`.

## Validação offline e próximos passos

```bash
uv run pytest tests/test_chat_agent.py
TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/emporio_test uv run pytest
```

Os testes usam modelos roteirizados e respostas LiteLLM simuladas. Verificam o
ciclo real do `create_agent`, schemas, fallback, pares de tools, preservação de
assinaturas, limites, isolamento entre turnos e idempotência. Com PostgreSQL,
também exercitam catálogo, pagamento e gravação real de reclamações pelo agente.

Essa validação não mede seleção semântica das tools, aderência real ao prompt ou
qualidade das respostas. Com as chaves disponíveis, será necessário avaliar esses
comportamentos e o RAG. Autenticação e retomada visual de conversas anteriores
permanecem como próximas etapas.
