# Interface de atendimento

O Gradio está montado na mesma aplicação FastAPI em **`/ui`**. Com os serviços
preparados conforme [Docker](docker.md), acesse `http://localhost:8000/` e entre
na sua conta. Cadastro e login ficam em `/auth`; o chat exige autenticação.
Veja [Autenticação](autenticacao.md) para configuração e contas demo com pedidos.
`POST /chat` e `GET /conversations` exigem login; `/docs` e `/health` são públicos.

## Conversar

1. Digite a mensagem e clique em **Enviar** ou pressione Enter.
2. Aguarde a resposta; os controles ficam desabilitados durante o atendimento.
3. Continue enviando mensagens na mesma conversa ou clique em **+ Nova conversa**
   na barra lateral.
4. Selecione qualquer conversa da lista lateral para rever as mensagens e continuar
   de onde parou, sem atualizar a página.

O UUID da conversa aparece em um campo somente leitura. Cada sessão de navegador
mantém a seleção atual e eventuais solicitações pendentes. As conversas salvas
pertencem à conta autenticada.
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

**+ Nova conversa** abre um atendimento com outro UUID e preserva os anteriores na
lista lateral. Os títulos usam o início da primeira mensagem e um trecho do UUID.
Uma conversa nova ainda vazia é reutilizada ao clicar novamente no botão.

A lista é recuperada do PostgreSQL pela conta autenticada ao abrir a página,
inclusive em outro navegador. Somente textos públicos são exibidos. O índice
visual em `gr.State` é transitório; nenhum token fica no localStorage.

Para retomar uma conversa da própria conta pelo UUID,
expanda **Retomar conversa pelo ID**, informe o UUID e clique em **Abrir conversa**.
Ela será adicionada à lista visual. UUIDs de outras contas e conversas anônimas
antigas não concedem acesso. A leitura não executa novamente o agente ou as tools.

Durante o atendimento, criação e troca de conversa ficam desabilitadas. Uma mensagem
pendente de retry permanece associada à sua conversa ao alternar entre atendimentos
na mesma sessão; retomar essa conversa permite usar **Tentar novamente** com o mesmo
`request_id`. A pendência em memória não é preservada ao recarregar a página.

A interface usa autenticação FastAPI com cookie HttpOnly e controle de titularidade
no backend, inclusive na fila de eventos do Gradio.
Não usa links públicos de compartilhamento e mantém a coleta de
analytics desabilitada. O acesso ocorre pela mesma porta do FastAPI.

## Validação

```bash
uv run pytest tests/test_chat_ui.py
```

Os testes verificam a montagem HTTP e o lifespan do FastAPI, processamento de
eventos do Gradio, isolamento de sessões, respostas públicas, criação e alternância
de conversas, validação de entrada e controles durante a execução. Com
`TEST_DATABASE_URL`, também verificam restauração após recarregar, abertura por UUID,
retomada de contexto e retry após alternância e perda de uma resposta já salva.
