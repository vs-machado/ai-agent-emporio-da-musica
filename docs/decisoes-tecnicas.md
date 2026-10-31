# Decisões Técnicas

Este documento registra as decisões de arquitetura para o protótipo do assistente de atendimento da Empório da Música. As decisões podem evoluir, mas alterações relevantes devem ser justificadas no README final.

## Objetivo

Construir um agente de atendimento em português que responda clientes com base nas políticas da loja e em dados operacionais de catálogo, promoções e pedidos. Informações dinâmicas devem ser obtidas por ferramentas, nunca inventadas pelo modelo.

## Linguagem E Comunicação

- Python é a linguagem principal do projeto.
- README, documentação, prompt do agente, ferramentas descritas ao modelo e respostas ao cliente serão escritos em português.
- Identificadores de código, módulos, variáveis, tabelas e variáveis de ambiente seguirão convenções em inglês.

## Backend E Interface

- FastAPI será o backend HTTP e concentrará a lógica de aplicação.
- A API permanecerá desacoplada da interface, com endpoint de chat reutilizável.
- Gradio será montado na mesma aplicação FastAPI como interface de demonstração do chat.
- A documentação interativa do FastAPI em `/docs` também será disponibilizada.
- Uma interface React com autenticação é uma possível evolução, apenas após o MVP estar completo. Ela consumirá a mesma API do FastAPI.

O Gradio foi escolhido por permitir uma interface de chat funcional em Python, com pouca implementação de frontend, facilitando a demonstração e a avaliação do agente. Ele é uma interface de protótipo; as regras de negócio e a persistência permanecem no backend para permitir sua substituição futura.

## Agente E Orquestração

- LangChain será usado para o agente de tool calling: schemas, ciclo modelo-ferramenta-modelo e execução das ferramentas.
- O histórico de mensagens será persistido em PostgreSQL usando a integração SQL do LangChain, identificada por um ID de conversa. A integração com a versão escolhida do agente deverá ser validada, incluindo o armazenamento consistente de chamadas e resultados de ferramentas.
- O histórico visual do Gradio não substitui essa persistência. A retomada de uma conversa na interface dependerá de como o ID de conversa for associado à sessão; autenticação e controle de acesso serão definidos posteriormente.
- Não será criado um grafo de negócio customizado nem configurado o checkpointer do LangGraph no MVP. Os casos atuais podem ser atendidos com histórico, ferramentas e regras determinísticas; fluxos explícitos em grafo serão reconsiderados se surgirem etapas obrigatórias, espera ou retomada após intervenção humana.
- Se uma avaliação futura concluir que o LangGraph é essencial para determinadas funções do bot, ele será implementado nos fluxos que necessitarem dele.
- O backend deverá expor uma abstração de atendimento, como `ChatService`, separando a API e o Gradio da implementação do agente. Tools, regras de negócio, retrieval e persistência terão interfaces desacopladas da orquestração, permitindo introduzir LangGraph futuramente com mudanças concentradas nessa camada.
- A versão escolhida do LangChain pode usar LangGraph internamente; isso não implica construir um grafo customizado nem configurar seu checkpointer.
- O agente usará ferramentas estruturadas, não um agente SQL genérico nem um fluxo ReAct baseado em texto livre.

## Modelos E Fallback

- LiteLLM será a camada de acesso a modelos e de roteamento entre provedores.
- Modelo primário: Gemini 2.5 Flash-Lite.
- Modelo de fallback: DeepSeek V4.1 Flash, acessado via OpenRouter.
- O fallback ocorrerá somente em falhas técnicas recuperáveis, como timeout, rate limit e erros 5xx do provedor.
- O fallback não será acionado para substituir uma resposta válida nem no meio de uma chamada de ferramenta sem preservar o estado do turno.

### Justificativa Dos Modelos

O Gemini 2.5 Flash-Lite foi escolhido como modelo primário por combinar baixo custo, latência adequada a uma conversa e suporte a chamadas estruturadas de ferramentas. A escolha também permite usar o ecossistema Gemini para geração de respostas e embeddings, reduzindo a quantidade de integrações necessárias.

O DeepSeek V4.1 Flash, via OpenRouter, foi escolhido como fallback por oferecer suporte a tool calling e preço competitivo. O uso de um segundo provedor de inferência ajuda a manter o atendimento disponível quando houver timeout, limitação de taxa ou indisponibilidade no provedor primário. O LiteLLM centraliza a configuração e o roteamento entre os modelos.

Os dois modelos devem ser avaliados nos cenários do projeto, especialmente quanto à qualidade das respostas em português e à seleção e execução correta das ferramentas. Preços, disponibilidade e comportamento de tool calling podem mudar e devem ser verificados antes da entrega.

O fallback de chat não substitui o serviço de embeddings. Quando o Gemini Embedding estiver indisponível, a recuperação de políticas usará o fallback textual descrito abaixo.

## Dados Operacionais

- PostgreSQL será usado como banco de dados principal.
- SQLAlchemy será a camada de acesso aos dados.
- Alembic versionará o schema por migrations.
- Os CSVs em `agent-data/` serão carregados em tabelas de negócio, incluindo produtos, clientes, pedidos, itens de pedido e promoções.
- O banco também armazenará reclamações registradas e o histórico de mensagens do LangChain.
- PostgreSQL foi escolhido em vez de SQLite para representar um cenário mais próximo de produção, com concorrência e persistência adequada para múltiplas conversas.
- A carga dos CSVs preservará os arquivos originais e validará tipos e relacionamentos antes de gravar os dados.

## Ferramentas Do MVP

- `search_products`: pesquisa e filtra produtos, consultando preços, status e estoque; também apoia a busca de alternativas.
- `get_product_details`: consulta detalhes, preço e disponibilidade de um produto.
- `get_active_promotions`: consulta promoções registradas, permitindo verificar a situação de uma campanha solicitada; informa limitações dos dados disponíveis.
- `get_order_status`: consulta pedido e rastreamento usando número do pedido e e-mail cadastrado como verificação inicial do protótipo.
- `search_policy`: recupera trechos do manual para responder dúvidas institucionais e sobre políticas.
- `register_complaint`: registra reclamações em uma tabela `complaints`, com status inicial `open`, para acompanhamento pela equipe.

Número do pedido e e-mail não comprovam controle da conta e não substituem autenticação. O acesso a dados sensíveis e a identificação real do cliente serão tratados na discussão de guardrails.

### Limitações E Complementos

- Pagamento e frete exigem cálculos determinísticos para desconto PIX, parcelamento e frete local. Ainda será decidido se serão tools próprias ou funções usadas pelas tools comerciais.
- O CSV de promoções contém `is_active`, mas não datas de vigência. A consulta deve informar apenas o estado registrado, sem inventar quando uma promoção terminou.
- O CSV de pedidos contém data estimada de entrega, não necessariamente a data real de recebimento. Não se deve usar a estimativa para concluir se o prazo de arrependimento ainda está aberto; o agente deve solicitar ou confirmar a informação necessária.
- O cadastro da reclamação representa um encaminhamento apenas se houver processo efetivo de acompanhamento pela equipe; sem isso, a interface deve informar a limitação.
- Perguntas sobre acessórios e assuntos fora do escopo podem ser tratadas pelo agente com base nas políticas, sem uma ferramenta específica.

## RAG Das Políticas

- O manual de políticas é a fonte de verdade e fica em `kb/políticas_da_loja.pdf`.
- LlamaIndex será usado para ingestão e retrieval.
- A extensão pgvector do PostgreSQL armazenará os embeddings; não haverá banco vetorial separado.
- O modelo de embeddings será `gemini-embedding-001`.
- O mesmo modelo de embedding deve ser usado na indexação do manual e na vetorização de cada pergunta de busca.
- O modelo de chat pode alternar entre Gemini e DeepSeek sem exigir reindexação, porque os trechos recuperados são enviados como texto ao modelo gerador.

### Justificativa Do LlamaIndex

O LlamaIndex foi mantido para organizar a ingestão e a recuperação do manual, separar a busca de conhecimento da orquestração do agente e permitir que os trechos recuperados tenham metadados, como seção e página. Essa estrutura também facilita a evolução da base caso outros documentos sejam adicionados ou as políticas sejam atualizadas.

Para o escopo atual, com apenas um PDF curto, uma solução mais simples também seria viável: extrair o texto e disponibilizar o manual ao modelo ou recuperar seções por busca textual, sem embeddings nem banco vetorial. Portanto, a escolha do LlamaIndex com pgvector não é uma necessidade imposta pelo volume de documentos; ela prioriza demonstrar RAG e preparar a solução para uma base de conhecimento maior, ao custo de mais dependências e configuração.

A primeira versão usará busca vetorial com fallback textual, com quantidade e tamanho dos trechos limitados. A recuperação será avaliada no benchmark com perguntas que exijam tanto correspondência semântica quanto regras e exceções em seções diferentes. Posteriormente, o benchmark será usado para comparar essa abordagem com busca híbrida e avaliar se combinar resultados vetoriais e textuais traz benefício relevante.

### Fallback Da Busca De Políticas

Se a geração de embeddings ou a consulta vetorial falhar, a ferramenta de políticas recorrerá à busca textual nos trechos e metadados armazenados no PostgreSQL. O número e o tamanho dos trechos enviados ao modelo permanecerão limitados, e ambos os caminhos serão avaliados com perguntas representativas.

Como a base de conhecimento atual contém apenas um documento, o manual de políticas em PDF, o eventual custo adicional de processamento ou de contexto nos casos excepcionais de indisponibilidade da API de embeddings é aceitável para preservar o atendimento. Os CSVs são dados operacionais carregados em tabelas do PostgreSQL, não documentos dessa base de conhecimento. A busca textual não exige chamadas adicionais a um provedor de embeddings nem a manutenção de um segundo índice vetorial; ela não implica necessariamente maior consumo de tokens, pois isso depende dos trechos retornados.

Esse mecanismo é um fallback, não uma busca híbrida executada em todas as consultas. Caso o PostgreSQL também esteja indisponível, nenhuma das duas buscas nele funcionará; o agente deverá informar que não conseguiu consultar as políticas, sem inventar a resposta.

## Infraestrutura E Dependências

- `uv` será usado para criar ambientes e instalar dependências.
- `pyproject.toml` será a fonte de configuração de dependências e ferramentas do projeto.
- Docker e Docker Compose subirão a API e o PostgreSQL para execução reproduzível.
- Segredos e URLs de conexão serão configurados por variáveis de ambiente e documentados em `.env.example`; arquivos `.env` não serão versionados.

## Organização De Dados

- `agent-data/` contém fontes operacionais versionadas, como os CSVs.
- `kb/` contém fontes de conhecimento para retrieval, como o manual de políticas.
- `challenge-document/` contém o enunciado do desafio e é ignorado pelo Git.
- Dados gerados, como índices, artefatos de ingestão e arquivos locais, deverão ficar em `storage/` e ser ignorados pelo Git.

## Estratégia De Git

- O repositório usa um Git Flow adaptado, com branches `feature/` e `fix/`.
- Commits não devem ser feitos diretamente em `master` ou `develop`.
- A integração ocorre por pull request com rebase e histórico linear.
- O GitHub permite apenas merge por rebase em `master` e `develop`.

## Pontos Pendentes

- Regras de guardrails além do prompt, incluindo proteção de acesso a pedidos e prevenção de respostas comerciais sem dados de ferramentas.
- Contrato final da API de chat, incluindo o formato do ID de conversa e a autenticação do cliente.
- Schema de reclamações, processo de acompanhamento e interface dos cálculos determinísticos.
- Estratégia de chunking e metadados do manual de políticas.
- Cenários de demonstração e testes automatizados.
