# Decisões Técnicas

Este documento registra as decisões de arquitetura para o protótipo do assistente de atendimento da Empório da Música. As decisões podem evoluir, mas alterações relevantes devem ser justificadas no README final.

## Objetivo

Construir um agente de atendimento em português que responda clientes com base nas políticas da loja e em dados operacionais de catálogo, promoções e pedidos. Informações dinâmicas devem ser obtidas por tools, nunca inventadas pelo modelo.

## Linguagem E Comunicação

- Python é a linguagem principal do projeto.
- README, documentação, prompt do agente, tools descritas ao modelo e respostas ao cliente serão escritos em português.
- Identificadores de código, módulos, variáveis, tabelas e variáveis de ambiente seguirão convenções em inglês.

## Backend E Interface

- FastAPI será o backend HTTP e concentrará a lógica de aplicação.
- A API permanecerá desacoplada da interface, com endpoint de chat reutilizável.
- Gradio será montado na mesma aplicação FastAPI como interface de demonstração do chat.
- A documentação interativa do FastAPI em `/docs` também será disponibilizada.
- Uma interface React com autenticação é uma possível evolução, apenas após o MVP estar completo. Ela consumirá a mesma API do FastAPI.

O Gradio foi escolhido por permitir uma interface de chat funcional em Python, com pouca implementação de frontend, facilitando a demonstração e a avaliação do agente. Ele é uma interface de protótipo; as regras de negócio e a persistência permanecem no backend para permitir sua substituição futura.

## Agente E Orquestração

- LangChain será usado para o agente de tool calling: schemas, ciclo modelo-tool-modelo e execução das tools.
- O histórico de mensagens será persistido em PostgreSQL usando a integração SQL do LangChain, identificada por um ID de conversa. A integração com a versão escolhida do agente deverá ser validada, incluindo o armazenamento consistente de chamadas e resultados de tools.
- O histórico visual do Gradio não substitui essa persistência. A retomada de uma conversa na interface dependerá de como o ID de conversa for associado à sessão; autenticação e controle de acesso serão definidos posteriormente.
- Não será criado um grafo de negócio customizado nem configurado o checkpointer do LangGraph no MVP. Os casos atuais podem ser atendidos com histórico, tools e regras determinísticas; fluxos explícitos em grafo serão reconsiderados se surgirem etapas obrigatórias, espera ou retomada após intervenção humana.
- Se uma avaliação futura concluir que o LangGraph é essencial para determinadas funções do bot, ele será implementado nos fluxos que necessitarem dele.
- O backend deverá expor uma abstração de atendimento, como `ChatService`, separando a API e o Gradio da implementação do agente. Tools, regras de negócio, retrieval e persistência terão interfaces desacopladas da orquestração, permitindo introduzir LangGraph futuramente com mudanças concentradas nessa camada.
- A versão escolhida do LangChain pode usar LangGraph internamente; isso não implica construir um grafo customizado nem configurar seu checkpointer.
- O agente usará tools estruturadas, não um agente SQL genérico nem um fluxo ReAct baseado em texto livre.

## Modelos E Fallback

- LiteLLM será a camada de acesso a modelos e de roteamento entre provedores.
- Modelo primário: Gemini 2.5 Flash-Lite.
- Modelo de fallback: DeepSeek V4.1 Flash, acessado via OpenRouter.
- `OPENROUTER_API_KEY` será opcional. Ausente ou vazia, o futuro agente usará apenas o Gemini, sem configurar uma rota de fallback DeepSeek. Essa chave não é necessária para indexação nem retrieval das políticas; a integração de chat via LiteLLM ainda será implementada.
- O fallback ocorrerá somente em falhas técnicas recuperáveis, como timeout, rate limit e erros 5xx do provedor.
- O fallback não será acionado para substituir uma resposta válida nem no meio de uma chamada de tool sem preservar o estado do turno.

### Justificativa Dos Modelos

O Gemini 2.5 Flash-Lite foi escolhido como modelo primário por combinar baixo custo, latência adequada a uma conversa e suporte a chamadas estruturadas de tools. A escolha também permite usar o ecossistema Gemini para geração de respostas e embeddings, reduzindo a quantidade de integrações necessárias.

O DeepSeek V4.1 Flash, via OpenRouter, foi escolhido como fallback por oferecer suporte a tool calling e preço competitivo. O uso de um segundo provedor de inferência ajuda a manter o atendimento disponível quando houver timeout, limitação de taxa ou indisponibilidade no provedor primário. O LiteLLM centraliza a configuração e o roteamento entre os modelos.

Os dois modelos devem ser avaliados nos cenários do projeto, especialmente quanto à qualidade das respostas em português e à seleção e execução correta das tools. Preços, disponibilidade e comportamento de tool calling podem mudar e devem ser verificados antes da entrega.

O fallback de chat não substitui o serviço de embeddings. Quando o Gemini Embedding estiver indisponível, a recuperação de políticas usará o fallback textual descrito abaixo.

## Dados Operacionais

- PostgreSQL será usado como banco de dados principal.
- SQLAlchemy será a camada de acesso aos dados.
- Alembic versionará o schema por migrations.
- Os CSVs em `agent-data/` serão carregados em tabelas de negócio, incluindo produtos, clientes, pedidos, itens de pedido e promoções.
- O banco também armazenará reclamações registradas e o histórico de mensagens do LangChain.
- PostgreSQL foi escolhido em vez de SQLite para representar um cenário mais próximo de produção, com concorrência e persistência adequada para múltiplas conversas.
- A carga dos CSVs preservará os arquivos originais e validará tipos e relacionamentos antes de gravar os dados.

## Tools Do MVP

- `search_products`: pesquisa e filtra produtos, consultando preços, status e estoque; também apoia a busca de alternativas.
- `get_product_details`: consulta detalhes, preço e disponibilidade de um produto.
- `get_active_promotions`: consulta promoções registradas, permitindo verificar a situação de uma campanha solicitada; informa limitações dos dados disponíveis.
- `get_order_status`: consulta pedido e rastreamento usando número do pedido e e-mail cadastrado como verificação inicial do protótipo.
- `search_policy`: recupera trechos do manual para responder dúvidas institucionais e sobre políticas.
- `register_complaint`: registra reclamações em uma tabela `complaints`, com status inicial `open`, para acompanhamento pela equipe.
- `calculate_payment`: consulta produtos e promoções para calcular condições de pagamento de um carrinho, sem receber preços informados pelo modelo.
- `calculate_shipping`: consulta o carrinho para estimar o frete local ou informar a necessidade de cotação externa.

Número do pedido e e-mail não comprovam controle da conta e não substituem autenticação. O acesso a dados sensíveis e a identificação real do cliente serão tratados na discussão de guardrails.

### Limitações E Complementos

- Pagamento e frete terão tools próprias, apoiadas em serviços determinísticos separados do LangChain. Valores monetários usam `Decimal`, com arredondamento para centavos. Nenhuma dessas tools efetua pagamento, reserva estoque ou cria pedidos.
- A simulação de frete considera o subtotal com promoções antes do desconto por forma de pagamento. Essa convenção do protótipo deve ser confirmada pela equipe antes de uso comercial.
- O manual não define o frete para exatamente R$ 500,00 e não esclarece a exceção abaixo de R$ 50,00 no parcelamento em até 3x. Esses limites devem retornar necessidade de confirmação humana, sem assumir uma condição comercial.
- Promoções não serão acumuladas. Os CSVs atuais têm no máximo uma promoção ativa por produto, e as consultas pressupõem esse cenário. Validação e resolução de conflitos entre múltiplas promoções ativas não são tratadas nesta etapa; não há regra de escolha pelo maior desconto. A vigência por datas também não é avaliada, pois a fonte contém apenas `is_active`.
- O CSV não tem coluna de marca. O filtro por marca será uma busca no nome do produto, com essa limitação explícita. Alternativas são sugestões da mesma categoria, sem afirmar equivalência técnica ou sucessão.
- O CSV de promoções contém `is_active`, mas não datas de vigência. A consulta deve informar apenas o estado registrado, sem inventar quando uma promoção terminou.
- O CSV de pedidos contém data estimada de entrega, não necessariamente a data real de recebimento. Não se deve usar a estimativa para concluir se o prazo de arrependimento ainda está aberto; o agente deve solicitar ou confirmar a informação necessária.
- O cadastro da reclamação representa um encaminhamento apenas se houver processo efetivo de acompanhamento pela equipe; sem isso, a interface deve informar a limitação.
- A tool de reclamações retornará protocolo e status `open`, sem afirmar envio à equipe nem garantir o prazo de 24 horas úteis enquanto o fluxo humano não estiver integrado. Repetições com a mesma chave de requisição não devem criar reclamações duplicadas.
- A verificação inicial de pedido exige ID e e-mail correspondentes e retorna apenas os dados necessários para atendimento, sem listar clientes. Isso não implementa autenticação real.
- `search_policy` usa uma interface de retrieval injetável, implementada com LlamaIndex/pgvector e fallback textual. A ausência do serviço ou de um índice ativo retorna indisponibilidade, sem respostas fabricadas nem uso dos CSVs como base de políticas.
- Perguntas sobre acessórios e assuntos fora do escopo podem ser tratadas pelo agente com base nas políticas, sem uma tool específica.

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

### Chunking E Recuperação De Contexto

A abordagem inicial combinará três mecanismos:

1. **Structure-aware chunking:** dividir o manual respeitando títulos, seções, subseções, parágrafos, listas e tabelas. A subseção será a unidade padrão; seções sem subseções formarão unidades próprias. Listas e tabelas deverão preservar a associação entre regras, condições e exceções. Seções que atravessam páginas serão reconstruídas, removendo cabeçalhos e rodapés repetidos. Divisões internas adicionais serão feitas apenas quando o tamanho da unidade exigir, respeitando esses limites estruturais.
2. **Recuperação de múltiplos chunks:** buscar vários trechos relevantes para permitir respostas apoiadas em mais de uma seção. A configuração inicial será top-k de 5 candidatos e até 5 evidências finais, após expansão e deduplicação, respeitando um orçamento total de contexto de aproximadamente 2.000 tokens. Não haverá corte intermediário fixo para 3 evidências nem reranking na primeira versão; será preservada a ordem de relevância da busca, usando a posição do candidato mais bem classificado quando houver agrupamento pela expansão. A seleção deverá preservar unidades coerentes, sem truncar condições ou exceções apenas para preencher o orçamento. Esses parâmetros serão ajustados por avaliação; aumentar o top-k, por si só, não garante recuperar todas as regras necessárias.
3. **Expansão estrutural automática:** quando necessário, complementar o trecho recuperado com sua subseção completa ou contexto hierárquico relevante, usando relações derivadas da estrutura do documento. A expansão deverá preservar referências, deduplicar conteúdo e respeitar o orçamento total de contexto.

Os trechos terão metadados de origem, versão do documento, identificação e título da seção, hierarquia e páginas de origem. O texto usado para embeddings incluirá os títulos hierárquicos para contextualizar cada unidade. As evidências enviadas ao modelo preservarão o conteúdo da fonte e sua localização, sem substituir as políticas por resumos gerados.

A escolha aproveita a estrutura explícita e as subseções curtas do manual. Sentence window retrieval não será a estratégia inicial: frases vizinhas nem sempre preservam tabelas e listas, e não alcançam necessariamente exceções em seções distantes. A expansão estrutural também não garante cobrir dependências entre seções distintas. Por exemplo, uma resposta sobre desconto PIX em produto promocional precisa considerar tanto a seção 3 quanto a seção 6.2.

### Indexação, Versionamento E Contratos

- A indexação identificará o arquivo por hash e registrará a versão declarada no manual e a configuração de processamento: parser, estratégia de chunking, modelo e dimensão dos embeddings. Reexecutar a mesma fonte com a mesma configuração não deverá duplicar os dados. Mudanças na fonte ou na configuração exigirão uma nova versão do índice.
- Uma nova versão só substituirá a versão ativa após concluir a extração, a validação dos trechos e a geração e persistência dos embeddings com sucesso. Falhas durante a atualização preservarão a versão ativa anterior. A consulta usará uma única versão ativa, tanto na busca vetorial quanto no fallback textual, evitando misturar políticas ou vetores incompatíveis.
- Cada evidência preserva página inicial e final, além da seção, título, versão e origem. `PolicyPassage` usa `page_start` e `page_end` em lugar de `page`. O limite antigo de 2.000 caracteres por passagem foi removido; o retriever limita o conjunto por tokens para preservar unidades completas sem truncamento silencioso.
- `PolicySearch.limit` representará o máximo de evidências finais, com padrão 5 e máximo 5. O top-k de candidatos será uma configuração interna do retriever, inicialmente 5, independente desse limite de saída.
- O orçamento aproximado de 2.000 tokens se aplicará ao conjunto de evidências, incluindo títulos e referências. Se uma expansão não couber, será reduzido primeiro o contexto estrutural adicional; se necessário, serão omitidas unidades completas, preservando a ordem de relevância. Uma unidade que exceda sozinha o orçamento deverá ser dividida em blocos coerentes na indexação, sem cortar condições ou exceções arbitrariamente durante a consulta.

A implementação inicial usa pypdf em modo layout para extrair o PDF e reconstruir as tabelas, e nodes LlamaIndex para representar as unidades recuperáveis. O limite inicial de chunk é 1.000 tokens, medidos com `cl100k_base`; essa contagem é uma aproximação operacional, não a tokenização exata do Gemini ou DeepSeek. Um bloco indivisível que exceda esse limite impede a indexação até revisão de sua divisão, em vez de sofrer truncamento automático. As 28 seções do manual atual resultam em 25 chunks; três seções são apenas títulos ancestrais sem texto próprio.

Os embeddings terão inicialmente 768 dimensões, com tarefas `RETRIEVAL_DOCUMENT` e `RETRIEVAL_QUERY` e distância cosseno. Essa dimensão está fixada no schema vetorial; alterá-la exigirá migration e reindexação. O PGVectorStore fará a leitura vetorial, com criação de tabelas desabilitada: Alembic gerencia o schema, e a escrita dos nodes serializados, seções e ativação ocorre na mesma transação SQLAlchemy. Versões anteriores serão mantidas e filtradas das consultas; a política de expurgo poderá ser definida posteriormente.

No Compose, a indexação será executada automaticamente pelo serviço de execução única `index-policies`, após as migrations e em paralelo à ingestão dos CSVs. A API aguardará a conclusão bem-sucedida de ambas as etapas. Essa decisão substitui a indexação exclusivamente manual; o comando `python -m app.rag` continuará disponível para execução isolada. O lifespan da API não fará a indexação. A primeira carga ou uma nova versão exigirá `GOOGLE_API_KEY` válida e acesso ao provedor; sem mudanças na fonte/configuração, o índice existente será reutilizado sem novas chamadas de embeddings. Falhas da etapa impedirão a inicialização da API nessa sequência do Compose.

### Avaliação E Alternativas De Retrieval

O benchmark avaliará a cobertura das evidências necessárias, incluindo condições e exceções, a precisão das referências e a qualidade das respostas, além de latência e volume de contexto. Encontrar apenas um trecho semelhante à pergunta não será suficiente. Haverá casos que exigem múltiplas seções, como PIX e cumulatividade de promoções, e casos que exigem reconstrução entre páginas, como entregas para outras cidades e privacidade. Ambiguidades e divergências da fonte deverão ser preservadas e sinalizadas, sem inventar uma regra para resolvê-las.

Se o desempenho inicial não for satisfatório, serão consideradas as seguintes alternativas, conforme as falhas observadas:

- **Busca híbrida:** combinar resultados vetoriais e lexicais na mesma consulta para melhorar a recuperação de termos específicos, valores e nomes. Sua adoção dependerá de ganho demonstrado sobre a busca vetorial com fallback textual.
- **Decomposição da pergunta:** realizar buscas complementares para aspectos diferentes de uma pergunta, ajudando a recuperar regras distribuídas entre seções. O ganho de cobertura deverá compensar o processamento e a latência adicionais.
- **Reranking:** reordenar candidatos recuperados antes de selecionar as evidências finais. Essa etapa pode melhorar a precisão, mas não recupera evidências ausentes do conjunto de candidatos.
- **Parent-child retrieval:** indexar unidades menores para busca e retornar a subseção ou seção correspondente como contexto. Será considerado se a granularidade inicial prejudicar a recuperação; a expansão deverá evitar incluir regras de outros cenários sem necessidade e não resolve, sozinha, dependências entre pais diferentes.
- **Relações explícitas entre políticas:** vincular trechos complementares para expansão entre seções, inclusive distantes. Essas relações podem vir de referências presentes no texto, de curadoria ou de inferência automatizada com validação. Poderão ser armazenadas no PostgreSQL, sem exigir banco de grafos ou GraphRAG. Relações semânticas manuais exigem manutenção a cada atualização e serão consideradas como apoio para dependências críticas, sem tornar a cobertura geral dependente do mapeamento prévio de todas as relações.

### Fallback Da Busca De Políticas

Se a geração de embeddings ou a consulta vetorial falhar, a tool de políticas recorrerá à busca textual nos trechos e metadados armazenados no PostgreSQL. O número e o tamanho dos trechos enviados ao modelo permanecerão limitados, e ambos os caminhos serão avaliados com perguntas representativas.

Como a base de conhecimento atual contém apenas um documento, o manual de políticas em PDF, o eventual custo adicional de processamento ou de contexto nos casos excepcionais de indisponibilidade da API de embeddings é aceitável para preservar o atendimento. Os CSVs são dados operacionais carregados em tabelas do PostgreSQL, não documentos dessa base de conhecimento. A busca textual não exige chamadas adicionais a um provedor de embeddings nem a manutenção de um segundo índice vetorial; ela não implica necessariamente maior consumo de tokens, pois isso depende dos trechos retornados.

Esse mecanismo é um fallback, não uma busca híbrida executada em todas as consultas. Caso o PostgreSQL também esteja indisponível, nenhuma das duas buscas nele funcionará; o agente deverá informar que não conseguiu consultar as políticas, sem inventar a resposta.

## Infraestrutura E Dependências

- `uv` será usado para criar ambientes e instalar dependências.
- `pyproject.toml` será a fonte de configuração de dependências e tools do projeto.
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

- Regras de guardrails além do prompt, incluindo proteção de acesso a pedidos e prevenção de respostas comerciais sem dados de tools.
- Contrato final da API de chat, incluindo o formato do ID de conversa e a autenticação do cliente.
- Processo efetivo de acompanhamento humano das reclamações e confirmação das ambiguidades comerciais identificadas no manual.
- Implementação do chunking e retrieval definidos e ajuste dos parâmetros com base no benchmark.
- Cenários de demonstração e testes automatizados.
