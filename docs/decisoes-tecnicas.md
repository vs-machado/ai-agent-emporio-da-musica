# Decisões Técnicas

Decisões da implementação atual do assistente de atendimento da Empório da Música.

## Objetivo

Construir um agente de atendimento em português que responda clientes com base nas políticas da loja e em dados operacionais de catálogo, promoções e pedidos. Informações dinâmicas devem ser obtidas por tools, nunca inventadas pelo modelo.

## Linguagem E Comunicação

- Python é a linguagem principal do projeto.
- Documentação, prompt, descrições de tools e respostas ao cliente usam português.
- Identificadores de código, tabelas e variáveis de ambiente usam inglês.

## Backend E Interface

- FastAPI concentra a lógica de aplicação, com endpoints de chat desacoplados da interface.
- Gradio está montado em `/ui` na mesma aplicação FastAPI como interface de demonstração do chat. Seus callbacks usam o mesmo `ChatService` da API, sem chamadas HTTP de loopback.
- A documentação interativa do FastAPI fica em `/docs`.
- O Gradio é preservado; cadastro/login usam uma página HTML leve no FastAPI. A autenticação usa PyJWT, pwdlib/Argon2 e cookie HttpOnly. Veja [Autenticação](autenticacao.md).

O Gradio foi escolhido por permitir uma interface de chat funcional em Python, com pouca implementação de frontend, facilitando a demonstração e a avaliação do agente. Ele é uma interface de protótipo; as regras de negócio e a persistência permanecem no backend para permitir sua substituição futura.

## Agente E Orquestração

- LangChain é usado pelo agente de tool calling com `create_agent`: schemas, ciclo modelo-tool-modelo e execução das tools.
- O histórico é persistido em PostgreSQL em tabelas próprias com SQLAlchemy, identificado por UUID de conversa. A serialização de mensagens do LangChain preserva chamadas, resultados de tools e metadados. Cada turno e sua resposta pública são gravados atomicamente; um lock de linha serializa turnos da mesma conversa.
- O histórico visual do Gradio não substitui essa persistência. UUIDs e solicitações pendentes ficam no estado da sessão; retries reutilizam a chave do turno. Ao abrir a página, conversas são recuperadas pelo dono autenticado. O backend controla acesso ao histórico e à fila de eventos, inclusive na retomada por UUID.
- Não há grafo de negócio customizado nem checkpointer LangGraph. O LangChain usa LangGraph internamente; histórico, tools e serviços determinísticos atendem os fluxos atuais.
- `ChatService` separa API e Gradio da orquestração. Tools, regras comerciais e retrieval permanecem desacoplados do agente.
- O agente usa tools estruturadas, sem agente SQL genérico ou ReAct em texto livre.

### Contrato Do ChatService

- `ChatService` recebe `ChatRequest` com mensagem, `conversation_id` e `request_id` UUID, e um agente injetável. Retorna `ChatTurn`, com resposta pública e as novas mensagens internas do turno, incluindo chamadas e resultados de tools. API e interface consomem a mesma fachada.
- Com persistência configurada, o serviço carrega o histórico pelo UUID e rejeita histórico manual. Sem persistência, o chamador interno pode fornecer turnos completos. A validação preserva pares de chamadas/resultados sem truncamento silencioso. Repetir conversa, solicitação e texto devolve o turno salvo, sem executar modelo ou tools novamente.
- `POST /chat` retorna apenas `ChatResponse`, com execução síncrona na thread pool. Exige login, origem válida e titularidade da conversa. Turnos concluídos usam HTTP 200, incluindo `unavailable` e `limit_reached`; demais códigos estão em [API de chat](api-chat.md). O Gradio mostra somente mensagem do cliente e resposta final; veja [Interface](interface-chat.md).
- O lifespan cria `app.state.chat_service` sem chamar provedores. Sem a chave do provedor primário (`OPENROUTER_API_KEY` no padrão DeepSeek), o serviço fica disponível para composição, mas retorna atendimento `unavailable` sem inferência nem respostas comerciais simuladas.
- O prompt em português fica em `app/agents/prompt.py`: persona HarmonIA, fontes obrigatórias, escopo, ambiguidades e limites comerciais. Não substitui autenticação nem validações determinísticas.
- O atendimento usa o nome quando disponível, sem exigir identificação apenas para personalizar. Distingue preço atual, valor histórico e subtotal sem frete; não simula exclusão de dados nem encaminhamento humano.
- Por padrão, cada turno permite seis rodadas de modelo e doze chamadas de tools, com até 2.048 tokens de saída por chamada e timeout de 20 segundos por tentativa. O histórico de entrada é limitado a cem mensagens. Parâmetros configuráveis por `CHAT_*`; sem streaming.
- A chave de reclamação é derivada de `request_id` por UUID v5 e injetada antes da execução. O campo técnico é removido do schema apresentado ao modelo; valores eventualmente gerados por ele são sobrescritos. Uma solicitação suporta um registro de reclamação, e retries devem reutilizar o mesmo ID. Dados diferentes com a mesma chave continuam sujeitos ao conflito validado pelo serviço.
- Falhas de provedor e limites de execução produzem mensagens públicas em português. Resultados de tools já executadas permanecem no transcript; se uma reclamação já foi registrada, sua referência também aparece na resposta de interrupção, sem afirmar envio à equipe.

## Modelos E Fallback

- LiteLLM é a camada de acesso aos provedores, integrada ao LangChain por `langchain-litellm`. O roteamento de fallback ocorre em middleware por chamada de modelo.
- Modelo primário: DeepSeek V4.1 Flash, acessado via OpenRouter, após melhor comportamento observado no atendimento em comparação com Gemini Flash-Lite.
- Fallback desativado por padrão: `CHAT_FALLBACK_MODEL` ausente, vazio ou só com espaços não cria adaptador nem rota de fallback.
- `OPENROUTER_API_KEY` é necessária para o primário padrão. `GOOGLE_API_KEY` continua sendo usada na indexação e nos embeddings das políticas. Para habilitar fallback, configure um modelo diferente do primário e a chave correspondente ao provedor (`openrouter/` usa OpenRouter; Gemini usa Google).
- O fallback ocorre somente em falhas técnicas recuperáveis, como timeout, rate limit e erros 5xx, preservando mensagens e resultados das tools.

O middleware repete chamadas com falhas transitórias no mesmo modelo antes de tentar o fallback, preservando exatamente o contexto de mensagens e resultados de tools. `CHAT_MODEL_RETRIES=2` permite duas tentativas adicionais por chamada/modelo, com esperas de 1 e 2 segundos; aceita de zero a três retries, com esperas de 1, 2 e 4 segundos. Esgotado o primário, tenta o fallback configurado com o mesmo orçamento. Após a troca, mantém o fallback nas demais rodadas daquele turno; um novo turno volta ao primário. Erros de autenticação, requisição inválida, contexto excedido ou resposta malformada não acionam retry nem fallback. Retries internos dos adaptadores ficam desabilitados para não multiplicar tentativas. Tools concluídas não são reexecutadas. Logs registram a classe da falha e o ID da solicitação sem conteúdo privado.

O adaptador preserva metadados e assinaturas ao reenviar mensagens ao mesmo modelo, sem transportar extensões específicas entre provedores. Valida argumentos JSON antes da execução de tools. O SDK OpenAI trata a hierarquia de erros do LiteLLM; não implica usar um modelo OpenAI. Configuração e testes estão em [Agente](agente.md).

### Justificativa Dos Modelos

Gemini Flash-Lite foi a escolha inicial por custo e suporte a tools, mas apresentou falhas de consulta e interpretação do catálogo. DeepSeek V4.1 Flash tornou-se o primário pelo melhor comportamento observado no atendimento, tool calling e preço competitivo. Isso não representa superioridade universal entre modelos.

A [linha de base com 38 casos](benchmark-baseline-rag-vetorial.md) registra a avaliação real do DeepSeek, com MiMo-V2.6-Flash como juiz independente via OpenRouter. Google permanece nos embeddings de retrieval e de `AnswerRelevancy`. O foco é a RAG Triad; correção factual e planos de tools são diagnósticos complementares. Metodologia e limites estão em [Benchmarks](benchmarks.md).

O fallback de chat não substitui o serviço de embeddings. Quando o Gemini Embedding estiver indisponível, a recuperação de políticas usará o fallback textual descrito abaixo.

## Dados Operacionais

- PostgreSQL é o banco principal; SQLAlchemy acessa os dados e Alembic versiona o schema.
- Os seis CSVs em `agent-data/` alimentam categorias, clientes, produtos, pedidos, itens e promoções.
- O banco também armazena reclamações, contas, sessões da interface e histórico de mensagens do LangChain.
- PostgreSQL foi escolhido em vez de SQLite para representar um cenário mais próximo de produção, com concorrência e persistência adequada para múltiplas conversas.
- A ingestão preserva os arquivos originais, valida tipos e relacionamentos e faz upsert transacional. Veja [Ingestão](ingestao-de-dados.md).

## Tools Do MVP

- `search_products`: pesquisa e filtra produtos, consultando preços, status e estoque; também apoia a busca de alternativas.
- `get_product_details`: consulta detalhes, preço e disponibilidade de um produto.
- `get_active_promotions`: consulta promoções registradas, permitindo verificar a situação de uma campanha solicitada; informa limitações dos dados disponíveis.
- `get_order_status`: sem parâmetros, lista pedidos e rastreamento do cliente autenticado, com paginação. Número e e-mail são filtros opcionais, não requisitos de atendimento. A LLM recebe e-mail e vínculo autenticados do backend no system prompt; o serviço verifica a titularidade no runtime.
- `search_policy`: recupera trechos do manual para responder dúvidas institucionais e sobre políticas.
- `register_complaint`: persiste protocolo e status `open` em `complaints`, sem notificar a equipe.
- `calculate_payment`: consulta produtos e promoções para calcular condições de pagamento de um carrinho, sem receber preços informados pelo modelo.
- `calculate_shipping`: consulta o carrinho e destino, sem tarifas, limites, modalidades ou prazos fixos. Condições locais vêm do manual via `search_policy`; fora da região metropolitana, orienta verificar o valor com um funcionário e informar ao cliente depois.
- `calculate_refund`: consulta o total registrado para devolução integral ou calcula estimativa proporcional para devolução parcial de um pedido composto apenas pelo mesmo produto. Exige pedido do cliente autenticado e confirmação da equipe; não aprova nem executa reembolso.
- `get_current_datetime`: fornece data/hora atual e dia da semana no fuso IANA `America/Campo_Grande`, com offset UTC calculado e indicador de horário de verão. Não recebe fuso ou data do modelo e não consulta banco nem provedor externo.
- `check_holiday`: consulta offline feriados nacionais e de MS com `holidays`, categoria `PUBLIC`, entre 1996 e 2100. Não inclui municípios, pontos facultativos ou horários especiais da loja.
- `lookup_postal_code`: consulta gratuitamente o ViaCEP para validar o CEP e obter município/UF. O cálculo de frete aceita esse CEP, sem inferir cobertura de outras cidades da região metropolitana.

O fuso é `America/Campo_Grande`, atualmente UTC−4, com `zoneinfo` e `tzdata`. O agente combina relógio, calendário BR/MS e expediente de `search_policy`. `holidays` evita dependência de API; municípios ficam fora do escopo. Black Friday não confirma extensão do expediente, e ausência de feriado não garante abertura. Contratos em [Tools](tools.md).

Número do pedido e e-mail não comprovam controle da conta. O vínculo entre conta e cliente vem do backend, nunca de argumentos do modelo. Cadastros públicos não adotam clientes importados; contas demo recebem vínculo explícito por seed.

### Limitações E Complementos

- ViaCEP consulta endereços, sem cotação de frete. Não há busca de peso/dimensões nem tabela regional. Destinos externos exigem verificação com funcionário, sem contato ou retorno automático; veja [Consultas externas](consultas-externas.md).
- O manual informa condições, forma e prazo de reembolso, mas não sua fórmula. A tool de reembolso acrescenta uma convenção explícita do protótipo: para unidades do mesmo produto, valor proporcional = total registrado × quantidade a devolver ÷ quantidade comprada, com arredondamento apenas no resultado final. O valor é estimativa, não preço histórico comprovado ou regra atribuída ao manual. Para devolução integral, o total registrado serve como referência. Não se usam preços ou promoções atuais para reconstruir a compra.
- Devoluções parciais de pedidos com produtos diferentes não podem ser calculadas sem discriminação histórica por item. Pedidos cancelados exigem verificar se houve pagamento e se existe algo a devolver, sem assumir valor zero ou reembolso anterior. Todas as referências de reembolso retornam `requires_confirmation`: a fonte não comprova pagamento, devoluções anteriores nem composição de frete e descontos. Elegibilidade permanece dependente das políticas e da confirmação humana; a tool é somente leitura.
- Pagamento e frete usam serviços determinísticos separados do LangChain. Valores monetários usam `Decimal`, com arredondamento para centavos. Nenhuma dessas tools efetua pagamento, reserva estoque ou cria pedidos.
- A consulta de frete fornece o subtotal com promoções como contexto. Não implementa taxas, gratuidade, modalidades ou prazos; o atendimento consulta a versão vigente do manual para as condições locais, sem reaplicar valores antigos do histórico.
- O manual não define o frete para exatamente R$ 500,00 e não esclarece a exceção abaixo de R$ 50,00 no parcelamento em até 3x. Esses limites devem retornar necessidade de confirmação humana, sem assumir uma condição comercial.
- Promoções não são acumuladas. As consultas pressupõem no máximo uma promoção ativa por produto, como nos CSVs atuais; não resolvem conflitos nem escolhem o maior desconto.
- O CSV não tem coluna de marca; o filtro busca no nome do produto. Alternativas são da mesma categoria, sem garantir equivalência técnica ou sucessão.
- O CSV de promoções contém `is_active`, mas não datas de vigência. A consulta deve informar apenas o estado registrado, sem inventar quando uma promoção terminou.
- O CSV de pedidos contém data estimada de entrega, não necessariamente a data real de recebimento. Não se deve usar a estimativa para concluir se o prazo de arrependimento ainda está aberto; o agente deve solicitar ou confirmar a informação necessária.
- O cadastro da reclamação representa um encaminhamento apenas se houver processo efetivo de acompanhamento pela equipe; sem isso, a interface deve informar a limitação.
- A tool de reclamações retorna protocolo e status `open`, sem envio à equipe ou garantia de retorno em 24 horas úteis. A chave de requisição evita duplicação e é isolada por usuário autenticado.
- A consulta de pedido exige cliente autenticado no contexto do turno e retorna somente dados necessários ao atendimento, sem listar clientes. Conhecer ID e e-mail de terceiros não concede acesso.
- `search_policy` usa uma interface de retrieval injetável, implementada com LlamaIndex/pgvector e fallback textual. A ausência do serviço ou de um índice ativo retorna indisponibilidade, sem respostas fabricadas nem uso dos CSVs como base de políticas.
- Perguntas sobre acessórios e assuntos fora do escopo podem ser tratadas pelo agente com base nas políticas, sem uma tool específica.

## RAG Das Políticas

- O manual de políticas é a fonte de verdade e fica em `kb/políticas_da_loja.pdf`.
- LlamaIndex organiza a ingestão e o retrieval; pgvector armazena os embeddings no mesmo PostgreSQL.
- O modelo de embeddings é `gemini-embedding-001`.
- O mesmo modelo de embedding deve ser usado na indexação do manual e na vetorização de cada pergunta de busca.
- O modelo de chat pode alternar entre Gemini e DeepSeek sem exigir reindexação, porque os trechos recuperados são enviados como texto ao modelo gerador.

### Justificativa Do LlamaIndex

O LlamaIndex foi mantido para organizar a ingestão e a recuperação do manual, separar a busca de conhecimento da orquestração do agente e permitir que os trechos recuperados tenham metadados, como seção e página. Essa estrutura também facilita a evolução da base caso outros documentos sejam adicionados ou as políticas sejam atualizadas.

Para o escopo atual, com apenas um PDF curto, uma solução mais simples também seria viável: extrair o texto e disponibilizar o manual ao modelo ou recuperar seções por busca textual, sem embeddings nem banco vetorial. Portanto, a escolha do LlamaIndex com pgvector não é uma necessidade imposta pelo volume de documentos; ela prioriza demonstrar RAG e preparar a solução para uma base de conhecimento maior, ao custo de mais dependências e configuração.

A implementação mantém busca vetorial com fallback textual e `top_k=5`. Os testes com [BM25 + RRF](resultados-busca-hibrida.md) e [top-k 10](benchmark-comparacao-top-k.md) não demonstraram ganho consistente que justificasse a troca. O híbrido permanece experimental, fora da `develop`.

### Chunking E Recuperação De Contexto

1. **Structure-aware chunking:** subseções ou seções sem subdivisão, respeitando parágrafos, listas e tabelas. O parser recompõe seções entre páginas e remove cabeçalhos/rodapés repetidos. Divide apenas entre blocos completos quando necessário.
2. **Múltiplos chunks:** cinco candidatos por padrão, até cinco passagens finais e orçamento aproximado de 2.000 tokens, sem reranking. A seleção preserva a ordem e não trunca unidades para preencher o orçamento.
3. **Expansão estrutural:** retorna a subseção completa se couber; caso contrário, tenta o chunk original. Deduplica seções/nodes, sem expansão semântica entre seções distantes.

As passagens preservam fonte, versão, seção, hierarquia e páginas; os embeddings incluem títulos. Não há resumos gerados nem sentence window retrieval. Dependências entre seções distintas, como PIX e promoções, exigem recuperar ambas ou uma consulta complementar.

### Indexação, Versionamento E Contratos

- Hash da fonte e configuração identificam a versão do índice. Reexecutar uma versão existente reutiliza seus vetores; mudanças de fonte/processamento exigem indexação.
- Geração de embeddings precede a transação de escrita; seções, nodes e ativação são gravados atomicamente. Falhas preservam a versão anterior, e cada busca usa uma única versão ativa.
- `PolicyPassage` preserva `page_start`, `page_end`, seção, título, versão e origem. `PolicySearch.limit` tem padrão e máximo 5, independente do top-k interno. O orçamento inclui metadados e texto.
- pypdf extrai em modo layout; nodes LlamaIndex representam os chunks. O teto é 1.000 tokens com `cl100k_base`, aproximação operacional. Blocos indivisíveis acima do teto impedem a indexação.
- O manual 2.1 tem 28 seções e 25 chunks, com máximo real de 275 tokens. Reduzir o teto para 512 não altera os chunks; veja [Análise de tamanho](analise-tamanho-chunks.md).
- Embeddings de 768 dimensões, tarefas `RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY` e distância cosseno. A dimensão é fixa no schema; mudá-la exige migration e reindexação. Alembic gerencia as tabelas, sem criação pelo PGVectorStore em runtime.
- Compose executa `index-policies` após migrations, em paralelo à ingestão. A API aguarda indexação e seed; não indexa no lifespan. Novos vetores exigem `GOOGLE_API_KEY`; índices idênticos são reutilizados sem chamadas. Veja [RAG](rag.md) e [Docker](docker.md).

### Avaliação E Alternativas De Retrieval

O diagnóstico de retrieval mede cobertura de seções em dez perguntas; o dataset do agente mantém 38 casos para RAG Triad, tools, checks, latência e tokens. Há 12 tools disponíveis, mas os planos atuais exigem apenas as 11 anteriores, sem cobertura obrigatória de `check_holiday`. Atendimento, CLIs e benchmark usam `build_policy_retriever`; o benchmark registra metadados do retriever e seu hash de configuração.

Uma execução por configuração não estabelece causalidade nem significância. Reranking, decomposição automática e relações explícitas entre políticas não estão implementados; sua adoção depende de falhas demonstradas e ganho medido.

### Fallback Da Busca De Políticas

Erros de embedding ou consulta vetorial acionam FTS PostgreSQL em português, com `websearch_to_tsquery` e ranking `ts_rank_cd`, na mesma versão ativa. Ausência de chave ou incompatibilidade com o índice também leva a esse caminho; resultado vetorial vazio não aciona fallback. Os limites finais são iguais nos dois modos.

Não há fusão híbrida nem chamadas de embeddings no fallback. Sem PostgreSQL, ambos os caminhos falham e a tool retorna indisponibilidade. Os CSVs são consultados como dados operacionais, não documentos indexados.

## Infraestrutura E Dependências

- `uv` gerencia o ambiente; `pyproject.toml` define dependências e tools, e `uv.lock` fixa versões. Docker e execução reproduzível usam `uv sync --locked`.
- Docker Compose prepara banco, migrations, ingestão, índice e contas antes da API.
- Variáveis estão documentadas em `.env.example`; `.env` não é versionado. Ruff e hooks locais verificam o código; veja [Qualidade](qualidade-de-codigo.md).

## Organização De Dados

- `agent-data/` contém fontes operacionais versionadas, como os CSVs.
- `kb/` contém fontes de conhecimento para retrieval, como o manual de políticas.
- `challenge-document/` contém o enunciado do desafio e é ignorado pelo Git.
- Índices e históricos ficam no PostgreSQL, com volume persistente no Compose. Relatórios gerados ficam em `benchmark-results/`, ignorado pelo Git; resumos publicados ficam em `docs/`.

## Estratégia De Git

- O repositório usa um Git Flow adaptado, com branches `feature/` e `fix/`.
- Commits não devem ser feitos diretamente em `master` ou `develop`.
- A integração ocorre por pull request com rebase e histórico linear.
- O GitHub permite apenas merge por rebase em `master` e `develop`.

## Pontos Pendentes

- Processo efetivo de acompanhamento humano das reclamações e confirmação das ambiguidades comerciais identificadas no manual.
- Guardrails de fundamentação além do prompt; acesso a pedidos e conversas já é validado pelo backend.
- Revisão dos gabaritos factuais e planos de tools, repetição controlada dos experimentos e cenários de demonstração publicados.
