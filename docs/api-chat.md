# API de chat

`POST /chat` atende uma mensagem usando o `ChatService` configurado pela aplicação.
O banco deve estar preparado com `alembic upgrade head`. A documentação interativa
fica em `/docs`.

## Iniciar uma conversa

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Quais violões estão disponíveis até R$ 1.000?"}'
```

O corpo aceita somente:

| Campo | Obrigatório | Descrição |
|---|---|---|
| `message` | Sim | Texto com 1 a 4.000 caracteres após remover espaços das extremidades |
| `conversation_id` | Não | UUID da conversa; gerado quando omitido |
| `request_id` | Não | UUID do turno; gerado quando omitido |

Não aceita histórico, papéis, chamadas ou resultados de tools enviados pelo cliente.
O histórico é recuperado do PostgreSQL pelo UUID da conversa.

Exemplo de formato da resposta (conteúdo ilustrativo):

```json
{
  "conversation_id": "77902f8e-06c4-46eb-b9ce-9eb91ca0337d",
  "request_id": "4d0ea984-be10-4cf2-9066-54beee850283",
  "status": "ok",
  "message": "Olá! Como posso ajudar?",
  "model": "gemini/gemini-2.5-flash-lite",
  "used_fallback": false,
  "actions": []
}
```

`actions` contém apenas nome, estado e eventual referência pública de cada operação.
As mensagens internas, argumentos, resultados das tools e metadados do provedor
são persistidos pelo serviço e não são retornados pela API.

## Continuar e repetir um turno

Envie o `conversation_id` retornado para continuar. Cada nova mensagem deve usar
um novo `request_id`, ou omiti-lo para que seja gerado.

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "E no PIX?", "conversation_id": "77902f8e-06c4-46eb-b9ce-9eb91ca0337d", "request_id": "207c8dbf-a68e-4b6a-a6e4-11ff79154420"}'
```

Para retries, reutilize **os dois UUIDs e o mesmo texto**. O serviço devolve o
turno salvo sem repetir modelo ou tools. Para permitir retry mesmo se a primeira
resposta HTTP se perder, gere e envie os dois UUIDs desde a primeira tentativa.
Uma conversa com UUID ainda inexistente é criada no primeiro turno concluído.

## Estados e erros

| HTTP | Significado |
|---|---|
| `200` | Turno concluído e salvo; consulte `status` no corpo |
| `409` | `request_id` já utilizado com outra mensagem na mesma conversa |
| `422` | Entrada inválida ou histórico acima de `CHAT_HISTORY_MAX_MESSAGES` |
| `500` | Histórico interno inválido |
| `503` | Falha no acesso ou gravação da persistência |

Em HTTP 200, `status` pode ser `ok`, `unavailable` (modelo sem configuração ou
falha tratada do provedor) ou `limit_reached` (limite de modelo/tools). Os três
estados são persistidos e recuperados em retries. Para tentar um novo atendimento
após um turno encerrado com indisponibilidade/limite, use outro `request_id`.

Ao exceder o limite de histórico, inicie outra conversa. O histórico não é
truncado e o novo turno não é executado nem salvo.

Erros tratados retornam `{"detail": "mensagem pública"}`. Erros de validação da
entrada usam o formato padrão do FastAPI, com `detail` como lista.

O endpoint é síncrono e executado na thread pool do FastAPI. Turnos da mesma
conversa são serializados no banco. Nesta etapa, o endpoint não implementa
autenticação nem controle de titularidade da conversa; o UUID é um identificador.
