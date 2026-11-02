# Interface de atendimento

O Gradio está montado na mesma aplicação FastAPI em **`/ui`**. Com os serviços
preparados conforme [Docker](docker.md), acesse `http://localhost:8000/ui/`.
O endpoint `POST /chat`, `/docs` e `/health` continuam disponíveis.

## Conversar

1. Digite a mensagem e clique em **Enviar** ou pressione Enter.
2. Aguarde a resposta; os controles ficam desabilitados durante o atendimento.
3. Continue enviando mensagens na mesma conversa ou clique em **Nova conversa**.

O UUID da conversa aparece em um campo somente leitura. Cada sessão de navegador
mantém seu próprio UUID, mensagens visuais e eventual solicitação pendente.
Cada envio novo recebe um `request_id` novo.

Os callbacks usam o mesmo `app.state.chat_service` que a API, sem uma chamada HTTP
de loopback. O serviço carrega o histórico interno pelo UUID e persiste o turno
completo no PostgreSQL. O histórico visual nunca é enviado ao agente como fonte
de contexto: mostra somente texto do cliente e resposta pública final, sem
argumentos, resultados de tools ou metadados internos.

Os callbacks síncronos executam nos workers do Gradio. Sessões diferentes podem
executar em paralelo; o serviço serializa turnos da mesma conversa no banco.

## Falhas e retries

- Se a execução falhar sem entregar uma resposta, **Tentar novamente** reutiliza
  a mesma mensagem, `conversation_id` e `request_id`. O texto do cliente não é
  duplicado no histórico visual. Novos envios ficam bloqueados até resolver a
  pendência ou iniciar outra conversa.
- Se o turno já tiver sido salvo antes da falha, o serviço recupera a resposta
  sem repetir modelo ou tools.
- Uma resposta concluída com `unavailable` ou `limit_reached` aparece normalmente
  no chat. Esses turnos já foram salvos: para tentar outro atendimento, envie uma
  nova mensagem, que receberá outro `request_id`.
- Ao atingir o limite de histórico ou encontrar uma conversa inválida, a interface
  orienta iniciar uma nova conversa. Não trunca mensagens nem repete uma chamada
  que está em conflito.

## Estado da interface

**Nova conversa** troca o UUID e limpa apenas o estado visual; os turnos anteriores
permanecem no banco. Atualizar a página ou abrir outra sessão inicia uma nova
conversa. A retomada visual de conversas anteriores por UUID ainda não está
implementada.

A interface é de demonstração, sem autenticação ou controle de titularidade.
Não usa links públicos de compartilhamento do Gradio e mantém a coleta de
analytics desabilitada. O acesso ocorre pela mesma porta do FastAPI.

## Validação

```bash
uv run pytest tests/test_chat_ui.py
```

Os testes verificam a montagem HTTP e o lifespan do FastAPI, processamento de
eventos do Gradio, isolamento de sessões, respostas públicas, nova conversa,
validação de entrada e controles durante a execução. Com `TEST_DATABASE_URL`,
também verificam retomada de contexto e retry após perda de uma resposta já salva.
