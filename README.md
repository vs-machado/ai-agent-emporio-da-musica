# HarmonIA — assistente da Empório da Música

Protótipo de agente de atendimento em **Python**, desenvolvido para o desafio
técnico de AI Engineer da Artefact.

O objetivo é reduzir a sobrecarga da equipe da Empório da Música, loja fictícia de
instrumentos musicais de Campo Grande/MS, respondendo dúvidas recorrentes com base
nos dados operacionais e nas políticas da loja. A HarmonIA combina consultas
estruturadas e recuperação de conhecimento para oferecer respostas contextualizadas,
mantendo explícitas as limitações do atendimento automatizado.

**[Acessar demonstração](https://emporio-da-musica.150.136.10.184.nip.io)**

> Para acessar uma conta de demonstração já cadastrada a partir da lista de
> clientes, use o e-mail do cliente e a senha padrão **`senha12345`**.

<img width="2514" height="1306" alt="emporio_musica_1" src="https://github.com/user-attachments/assets/15af1638-247c-426c-8631-2a0c15ac836c" />
<img width="2514" height="1306" alt="emporio_musica_2" src="https://github.com/user-attachments/assets/6e2a0712-02a0-40f0-9029-ec5be9c5a222" />

## Navegação rápida

| Assunto | Links |
|---|---|
| Execução | [Configurar ambiente](#executar-com-docker) · [Construir e iniciar](docs/docker.md#construir-e-iniciar) · [Acessar](docs/docker.md#acessar) · [Operação Docker](docs/docker.md) |
| Arquitetura | [Visão geral](#arquitetura-e-decisões-técnicas) · [Decisões técnicas](docs/decisoes-tecnicas.md) · [Agente](docs/agente.md) |
| Dados e ferramentas | [Ingestão](docs/ingestao-de-dados.md) · [RAG](docs/rag.md) · [Tools](docs/tools.md) · [Consultas externas](docs/consultas-externas.md) |
| Interface e acesso | [Autenticação e contas demo](docs/autenticacao.md) · [API](docs/api-chat.md) · [Interface](docs/interface-chat.md) |
| Conversas | [Cinco exemplos reais de interação](#exemplos-de-interação) |
| Qualidade e avaliação | [Verificações locais](docs/qualidade-de-codigo.md#verificações-locais) · [Benchmarks](docs/benchmarks.md) · [Resultados e custos](docs/decisao-rag-e-custos.md) |
| Desenvolvimento | [Limitações e evolução](#avaliação-e-limitações) · [Uso de IA](#uso-de-ia-no-desenvolvimento) |

## Funcionalidades

- Consulta de catálogo, preços, estoque e promoções.
- Simulações de pagamento e consulta de condições de entrega.
- Respostas sobre horários, garantia, trocas, devoluções e políticas da loja.
- Consulta dos pedidos vinculados à conta autenticada.
- Registro de reclamações com protocolo, sem encaminhamento humano automático.
- Histórico de conversas por conta, com retomada na interface.
- Tratamento cordial de perguntas fora do escopo da loja.

O atendimento usa a persona HarmonIA, em português brasileiro, com tom acolhedor,
informal e profissional. As ferramentas não executam compras, pagamentos,
reservas, cancelamentos ou reembolsos.

## Executar com Docker

### Pré-requisitos

- Git, Docker e **Docker Compose v2** (`docker compose`).
- Acesso à internet e chaves com cota/créditos disponíveis para **OpenRouter**
  (chat) e **Google Gemini API** (embeddings).
- Portas locais `8000` e `5432` disponíveis, ou outras configuradas no `.env`.

Não é necessário instalar Python ou PostgreSQL no host para executar com Docker.

### Clonar e preencher o ambiente

```bash
git clone https://github.com/vs-machado/ai-agent-emporio-da-musica.git
cd ai-agent-emporio-da-musica
git switch develop
cp .env.example .env
```

Edite o `.env`. Para a configuração padrão, preencha:

| Variável | Configuração |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Credenciais do banco; o exemplo inclui valores locais |
| `AUTH_JWT_SECRET` | Segredo aleatório próprio com pelo menos 32 caracteres |
| `DEMO_PASSWORD` | Senha inicial das contas demo, de 10 a 128 caracteres |
| `OPENROUTER_API_KEY` | Chave para o modelo de chat DeepSeek via OpenRouter |
| `GOOGLE_API_KEY` | Chave para indexar o manual e gerar embeddings das consultas |

Para gerar o segredo JWT usando apenas Docker:

```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copie o resultado para `AUTH_JWT_SECRET`. As chaves e o `.env` não devem ser
publicados. O exemplo completo está em [`.env.example`](.env.example).

O chat usa `CHAT_PRIMARY_MODEL=openrouter/deepseek/deepseek-v4.1-flash` e os
embeddings usam `RAG_EMBEDDING_MODEL=gemini-embedding-001`, com 768 dimensões.
`CHAT_FALLBACK_MODEL` fica vazio por padrão. A chave de chat não substitui a chave
de embeddings: a primeira indexação precisa de acesso válido à API Google.

Após preencher o ambiente, siga os guias de **[construção e inicialização](docs/docker.md#construir-e-iniciar)**
e **[acesso à aplicação](docs/docker.md#acessar)**. O Compose executa migrations,
ingestão, indexação e seed automaticamente. Para publicação HTTPS, consulte a
[configuração do proxy e dos cookies](docs/autenticacao.md#https-atrás-do-coolify-ou-de-outro-proxy).

## Arquitetura e decisões técnicas

```mermaid
flowchart TD
    UI["FastAPI / Gradio"] --> Auth["Autenticação"]
    Auth --> Service["ChatService"]
    Service --> Agent["Agente LangChain"]
    Agent <--> Model["LiteLLM → DeepSeek V4.1 Flash / OpenRouter"]
    Agent <--> Tools["Tools estruturadas"]
    Tools --> Data["Dados e cálculos determinísticos"]
    Data --> DB[("PostgreSQL")]
    Tools --> RAG["Políticas / LlamaIndex"]
    RAG --> Vectors["pgvector / PostgreSQL"]
    RAG --> Embeddings["Gemini Embedding"]
    Tools --> External["Relógio, feriados e ViaCEP"]
    Service --> History["Histórico por conta"]
    History --> DB
```

| Escolha | Papel na solução |
|---|---|
| **LangChain + tool calling e RAG** | Orquestra consultas por ferramentas com schemas definidos e acessa as políticas por RAG com chunking estrutural (structure-aware), busca vetorial e fallback textual, mantendo SQL, autorização e cálculos fora da LLM |
| **DeepSeek via OpenRouter / LiteLLM** | Melhor comportamento observado no catálogo e nas ferramentas durante o desenvolvimento, com custo competitivo e acesso desacoplado do provedor |
| **FastAPI + Gradio** | API e interface de chat em Python, compartilhando o mesmo serviço de atendimento, sem frontend separado |
| **PostgreSQL, SQLAlchemy e Alembic** | Persistência transacional de dados, contas e conversas, com schema versionado |
| **LlamaIndex + pgvector** | Implementa o RAG com chunks por seção/subseção, busca vetorial por similaridade de cosseno e fallback para busca textual PostgreSQL em caso de falha vetorial, preservando evidências e metadados |

### Framework e abordagem do agente

O **LangChain** foi escolhido pela facilidade de implementar tool calling, pela
integração com diferentes modelos e pela familiaridade prévia com o ecossistema
LangChain/LangGraph. Isso permitiu concentrar o desenvolvimento nas ferramentas e
nas regras do atendimento, em vez de implementar manualmente o ciclo de chamadas
ao modelo.

Não foi necessário construir um grafo customizado com **LangGraph**: o fluxo de
atendimento podia ser resolvido pelo agente com ferramentas, sem etapas que
exigissem uma orquestração explícita em grafo. O LangChain utiliza LangGraph
internamente, mas o projeto não define um grafo próprio.

### API e interface

O **FastAPI** foi escolhido para desenvolver a API em Python, com validação de
entradas e documentação automática, facilitando a implementação e a exploração
dos endpoints.

O **Gradio** permitiu entregar um chat funcional sem construir e manter um frontend
separado. Essa escolha priorizou o desenvolvimento e a avaliação do agente, que
são o foco do desafio.

### Persistência

O **PostgreSQL com pgvector** foi escolhido para concentrar dados operacionais,
histórico de conversas e embeddings em um único banco, evitando um serviço
adicional para a busca vetorial.

Embora SQLite pudesse simplificar o protótipo, PostgreSQL aproxima a solução de
um ambiente de produção. A contrapartida é exigir um serviço de banco, incluído
no Docker Compose para simplificar a execução.

### Modelo e acesso aos provedores

O **DeepSeek V4.1 Flash via OpenRouter** foi escolhido pelo comportamento observado
nos testes de atendimento e tool calling, após dificuldades com Gemini Flash-Lite,
e pelo custo competitivo. A decisão se baseia nos cenários do projeto, sem
representar uma comparação universal entre os modelos.

O **LiteLLM** facilita a troca de modelos e provedores por uma interface comum e
permite configurar fallback, reduzindo o acoplamento da aplicação a um único
provedor.

### RAG e recuperação das políticas

O **LlamaIndex** foi escolhido pela familiaridade prévia com a biblioteca e por
oferecer componentes para representar os trechos do manual, preservar seus
metadados e integrar a recuperação ao pgvector. A biblioteca permite separar o
processamento e a busca de conhecimento da orquestração do agente, mantendo o RAG
acessível pela ferramenta `search_policy`.

O **chunking estrutural** foi definido a partir da organização do manual em seções
e subseções. Essa abordagem preserva regras, listas e tabelas em unidades
completas, reduzindo a fragmentação de informações necessárias para interpretar
uma política. Os trechos mantêm referências de origem, seção e página para
inspeção e avaliação.

A **busca vetorial com fallback textual** foi mantida após os experimentos com
diferentes configurações de recuperação. A busca vetorial permite recuperar
trechos por proximidade semântica; quando ocorre falha na geração de embeddings
ou na consulta vetorial, a busca textual do PostgreSQL oferece um caminho
alternativo sobre o índice ativo. As comparações com `top_k=10` e BM25 + RRF não
demonstraram ganho consistente que justificasse substituir a configuração atual.

Somente `kb/políticas_da_loja.pdf` é indexado; os CSVs são consultados como dados
relacionais. O manual atual produz 25 chunks, com embeddings Gemini de 768
dimensões, similaridade de cosseno, `top_k=5` e orçamento de aproximadamente 2.000
tokens de contexto. O fallback textual não é uma fusão híbrida dos resultados.

Como o material contém apenas um PDF curto, uma implementação sem biblioteca de
RAG também seria viável. A escolha do LlamaIndex priorizou uma estrutura
extensível para indexação e recuperação, aceitando maior complexidade e mais
dependências no protótipo. Detalhes em [RAG](docs/rag.md) e
[decisão de RAG e custos](docs/decisao-rag-e-custos.md).

### Dados, prompt e histórico

**Dados e ingestão.** Os seis CSVs de `agent-data/` são validados antes de uma
carga transacional com upsert: tipos, valores monetários, status, JSON e
relacionamentos. Os arquivos originais são preservados. Preços históricos e datas
de recebimento ausentes não são inferidos. Consulte [ingestão](docs/ingestao-de-dados.md).

**Prompt.** O [system prompt](app/agents/prompt.py) define persona, tom, fontes
obrigatórias e tratamento de ambiguidades e perguntas fora do escopo. O agente
deve consultar dados antes de afirmar preço, estoque ou status, usar políticas
para regras da loja e não prometer operações inexistentes. As evidências apoiam
a resposta sem expor referências documentais ao cliente. Autorizações e cálculos
continuam validados pelo backend, independentemente do prompt.

**Histórico.** Conversas e turnos completos, incluindo chamadas e resultados de
ferramentas, ficam no PostgreSQL e podem ser retomados por conta. IDs de solicitação
evitam reexecutar turnos já concluídos, e a titularidade de conversas e pedidos é
verificada no backend. Detalhes em [agente](docs/agente.md),
[autenticação](docs/autenticacao.md) e [decisões técnicas](docs/decisoes-tecnicas.md).

## Avaliação e limitações

O projeto usa pytest para validações, contratos e integração; Ruff para lint e
formatação; e Ragas para avaliar respostas reais em 38 casos. Os benchmarks usam
MiMo-V2.6-Flash como juiz e consideram RAG Triad, uso de ferramentas, tokens e
latência. Na linha de base vetorial, a relevância do contexto foi **89,6%**, a
fundamentação **82,2%** e a relevância da resposta **71,4%**.

Essas médias são de uma execução e não garantem correção em todo atendimento.
Consulte a metodologia, os resultados e suas ressalvas:

- [Benchmarks: dataset, métricas e execução](docs/benchmarks.md).
- [Resultados da linha de base vetorial](docs/benchmark-baseline-rag-vetorial.md).
- [Resultados da busca híbrida BM25 + RRF](docs/resultados-busca-hibrida.md).
- [Comparação vetorial com top-k 5 e 10](docs/benchmark-comparacao-top-k.md).
- [Decisão de RAG e estimativas de custo](docs/decisao-rag-e-custos.md).
- [Verificações locais: lint, formatação e testes](docs/qualidade-de-codigo.md#verificações-locais).

Limitações e próximos passos priorizados:

- **Erros da LLM:** ainda pode haver interpretação equivocada, informação sem
  suporte suficiente ou promessa incompatível com as capacidades do sistema.
- **Busca e comunicação de disponibilidade:** melhorar as consultas por modelos
  específicos e distinguir produto inexistente, sem estoque e descontinuado.
  Nas conversas registradas, buscas restritas a disponíveis levaram o agente a
  afirmar que modelos descontinuados não constavam no catálogo. O atendimento
  deve esclarecer que não estão à venda, mesmo quando houver unidades registradas
  em estoque, sem confundir quantidade física com disponibilidade comercial.
- **Revisão dos benchmarks:** `factual_correctness` registrou **31,9% em 20 casos**
  na linha de base. É necessário revisar referências, critérios e respostas caso
  a caso e repetir a avaliação. Problemas metodológicos são uma hipótese, não
  uma explicação comprovada nem motivo para descartar erros reais.
- **Redundância de provedores:** o mecanismo de fallback de chat existe, mas está
  desativado por padrão. Falta selecionar e validar um modelo alternativo e
  ampliar a redundância dos serviços de IA, incluindo embeddings.
- **Atendimento humano:** integrar encaminhamento, acompanhamento e confirmação
  de solicitações. Hoje, registrar uma reclamação gera protocolo no banco, sem
  notificar a equipe ou garantir retorno.
- **Ferramenta de OCR:** implementar uma tool para extrair texto de imagens e
  documentos digitalizados, permitindo usar essas informações no atendimento.
- **Frete para outras regiões:** adicionar ferramenta de pesquisa na internet
  para obter dimensões dos produtos e evoluir o cálculo de frete. Atualmente,
  ViaCEP informa endereço, não tarifa; destinos externos dependem de confirmação
  com um funcionário. Pesquisa de dimensões e cálculo externo ainda não existem.

## Uso de IA no desenvolvimento

Foi utilizado **OpenCode com GPT-6.1 Sol** como apoio ao desenvolvimento. As
tecnologias foram definidas com auxílio da IA, que também analisou a estrutura
dos documentos para apoiar as decisões sobre ingestão e RAG. A técnica escolhida
considerou tanto a organização do manual de políticas quanto os resultados dos
benchmarks executados.

Os prompts foram elaborados inicialmente com IA e refinados manualmente de forma
iterativa. Conversas armazenadas no banco foram fornecidas como insumo para a IA
sugerir alterações, com revisão e direcionamento do desenvolvedor.

O GPT-6.1 Sol é o modelo do assistente de código; o modelo de atendimento da
aplicação é o **DeepSeek V4.1 Flash** (`openrouter/deepseek/deepseek-v4.1-flash`).
O **Gemini Embedding** (`gemini-embedding-001`) é usado nos embeddings e o
**MiMo-V2.6-Flash** (`xiaomi/mimo-v2.6-flash`) nas avaliações registradas.

## Exemplos de interação

Cinco conversas realizadas na aplicação em 03/11/2026, transcritas do banco com
as mensagens originais do cliente e da HarmonIA. Ao todo, são 33 turnos de atendimento.

| Conversa | Cenários demonstrados |
|---|---|
| [1. Ukulele para iniciante](docs/conversas/01-ukulele-pix-frete.md) | Recomendação, catálogo, desconto PIX e frete local pago |
| [2. Guitarra e formas de pagamento](docs/conversas/02-guitarra-cep-pagamento.md) | Catálogo, promoções, consulta de CEP, frete grátis e parcelamento |
| [3. Pedido atrasado e reclamação](docs/conversas/03-pedido-atrasado-reclamacao.md) | Horário atual, consulta do pedido autenticado, rastreamento e registro de protocolo |
| [4. Violão Taylor e devolução](docs/conversas/04-taylor-devolucao-entrega.md) | Refinamento da busca, detalhes do produto, parcelamento, políticas de devolução e entrega para outra região |
| [5. Escopo e produtos descontinuados](docs/conversas/05-escopo-produtos-descontinuados.md) | Acessórios não comercializados, pergunta fora do escopo, alternativas e esclarecimento sobre descontinuação |
