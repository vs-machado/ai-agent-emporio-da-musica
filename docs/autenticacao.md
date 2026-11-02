# Cadastro, login e isolamento de dados

A interface continua no **Gradio**, em `/ui/`. Uma página HTML leve, servida
pelo FastAPI em `/auth`, oferece cadastro e login. A raiz `/` encaminha para ela.
Não há build ou serviço separado de frontend.

## Preparação

```bash
uv sync --locked
uv run alembic upgrade head
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use o segredo gerado no `.env`:

```dotenv
AUTH_JWT_SECRET=<segredo aleatório com pelo menos 32 caracteres>
AUTH_COOKIE_SECURE=false
AUTH_TOKEN_MINUTES=120
```

O Compose exige esse segredo. Na execução local, se a variável estiver ausente,
o processo gera um segredo efêmero: reiniciar invalida os logins. Um valor vazio
ou menor que 32 caracteres é rejeitado. Use `AUTH_COOKIE_SECURE=true` quando o
acesso público usar HTTPS. A implantação atual usa um worker: estado e fila do
Gradio ficam em memória.

## Uso

1. Acesse `http://localhost:8000/`.
2. Crie uma conta com e-mail e senha de 10 a 128 caracteres.
3. Entre; a página encaminha para o chat Gradio.
4. As conversas salvas da conta aparecem na barra lateral, inclusive em outro
   navegador após login.
5. **Sair da conta** apaga o cookie e invalida os tokens anteriores dessa conta,
   inclusive em outros navegadores.

O cadastro público cria uma conta com `customer_id=null`. Ele não adota clientes
ou pedidos importados por coincidência de nome/e-mail/telefone. Um vínculo comercial
deve ser provisionado pela equipe após verificar titularidade; o protótipo oferece
provisionamento explícito das contas demo.

## Contas para demonstrar pedidos existentes

Após a ingestão dos CSVs, escolha uma senha de demonstração:

```bash
DEMO_PASSWORD='Demo-musica-2026!' uv run python -m app.seed_users
```

No Docker, configure `DEMO_PASSWORD` no `.env` (10 a 128 caracteres). O serviço
`seed-users` executa automaticamente após `ingest`; a API aguarda sua conclusão
e a indexação das políticas. Para subir o ambiente:

```bash
docker compose up -d --build --wait
docker compose logs seed-users
```

Para reexecutar somente o seed com o banco já preparado:

```bash
docker compose run --rm --no-deps seed-users
```

O seed cria uma conta para **cada e-mail da tabela `customers`**, incluindo
clientes sem pedidos. Na planilha atual são **49 e-mails distintos em 50 clientes**.
O login usa o e-mail
importado, normalizado em minúsculas e sem espaços nas extremidades; o vínculo
usa o respectivo `customer_id`. Novas contas recebem a senha `DEMO_PASSWORD`.

O comando imprime os IDs dos clientes e seus pedidos para orientar o teste.
Usa os dados existentes sem alterar pedidos. É transacional e idempotente:
reexecutar não redefine senhas nem muda vínculos existentes. Contas existentes
com vínculos divergentes (inclusive contas
públicas ainda sem vínculo) interrompem a transação para revisão, sem apropriar
contas ou aplicar alterações parciais.

E-mails repetidos geram uma única conta, vinculada ao cliente de menor ID.
O seed informa os outros IDs na saída, sem combinar os pedidos dos cadastros.
Na planilha atual, `bruno.martins@jmail.com` aparece nos clientes 11 e 43; a conta
fica vinculada ao cliente 11. O cliente 43 não recebe vínculo adicional.

Se `demo.a@example.test` ou `demo.b@example.test` já estiverem vinculadas pelo
seed antigo, o login é atualizado para o e-mail do respectivo cliente, preservando
ID da conta, senha e histórico. Se o e-mail de destino estiver ocupado, o seed
interrompe em vez de mesclar contas. A antiga conta sem vínculo `demo.c` é
preservada caso exista, mas não é criada novamente.

Valide: um cliente consulta um de seus pedidos; outro tenta consultar esse mesmo
pedido e recebe pedido não encontrado. Clientes sem pedidos podem conversar sobre
produtos e políticas. Cada conta tem seu próprio histórico.

## Contratos HTTP

- `POST /auth/register`: JSON `{"email":"...","password":"..."}`; retorna 201.
- `POST /auth/login`: mesmo JSON; define cookie `emporio_session` HttpOnly,
  SameSite=Lax, com validade de duas horas por padrão.
- `GET /auth/me`: identidade autenticada, sem senha/hash/token.
- `POST /auth/logout`: revoga tokens, apaga cookie e redireciona para `/auth`.
- `POST /chat`: exige login; formato da mensagem preservado.
- `GET /conversations`: mapa dos UUIDs da conta para suas mensagens públicas.

Todas as operações HTTP que alteram dados exigem `Origin` igual à origem da API,
inclusive login, cadastro e eventos Gradio. Navegadores enviam esse cabeçalho.
Clientes de terminal devem enviá-lo explicitamente e manter o cookie:

```bash
curl -c /tmp/emporio-cookies -H 'Origin: http://localhost:8000' \
  -H 'Content-Type: application/json' \
  -d '{"email":"lucas.mendes@jmail.com","password":"Demo-musica-2026!"}' \
  http://localhost:8000/auth/login
curl -b /tmp/emporio-cookies -H 'Origin: http://localhost:8000' \
  -H 'Content-Type: application/json' -d '{"message":"Olá"}' \
  http://localhost:8000/chat
```

## Implementação

- SQLAlchemy/Alembic: `users` separado de `customers`; vínculo único opcional.
- `pwdlib[argon2]`: hash de senha, sem persistir a senha original.
- `PyJWT`: HS256 fixo, emissor, audiência, expiração e versão de revogação validados.
- FastAPI: `get_current_user` resolve a conta a partir do cookie.
- Gradio: `auth_dependency` protege o acesso; callbacks revalidam identidade e
  descartam estado visual de outra conta. `ui_sessions` vincula fila/SSE ao dono,
  impedindo reutilizar identificadores entre contas.
- Conversas: dono verificado antes de carregar histórico, responder a retry ou
  chamar o agente. UUID não concede acesso.
- Pedidos, reembolsos e reclamações vinculadas: exigem `customer_id` confiável do
  contexto do turno. O runtime do LangChain transmite a identidade às tools; o
  modelo não a escolhe. E-mail é conferência opcional em consultas, nunca credencial.
  Reclamações mantêm contato obrigatório e idempotência por usuário.
- Conversas anônimas anteriores à migration permanecem sem dono e não aparecem
  na interface autenticada nem são adotadas por novas contas.

CLI/benchmarks sem identidade continuam úteis para catálogo e políticas, mas
não têm autorização para consultar pedidos. A API pública sempre exige login.

## Testes

```bash
uv run pytest tests/test_auth.py tests/test_chat_api.py tests/test_chat_ui.py
```

Testes de banco exigem `TEST_DATABASE_URL` para PostgreSQL dedicado com nome
terminado em `_test`. Cobrem senhas, tokens inválidos/expirados, logout, CSRF,
cadastro sem associação indevida, isolamento de pedidos/históricos/fila e seed.
