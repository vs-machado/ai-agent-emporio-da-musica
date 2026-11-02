# Consulta de CEP e atendimento de frete

O protótipo consulta o ViaCEP gratuitamente, sem chave adicional. A consulta é feita
somente durante o atendimento; construir as tools não acessa a internet.

## Consulta de CEP

`lookup_postal_code` recebe um CEP com oito dígitos ou no formato `00000-000`.
Consulta o ViaCEP e retorna endereço, município, UF, código IBGE e referência
da fonte. O serviço valida o formato antes de fazer a chamada e diferencia
CEP inexistente (`not_found`) de indisponibilidade (`unavailable`).

```python
tool = next(t for t in tools if t.name == "lookup_postal_code")
result = tool.invoke({"postal_code": "01001-000"})
```

O CEP do manual é fictício e não é substituído automaticamente por outro.
O endereço retornado não comprova cobertura do motoboy nem preço de entrega.

`calculate_shipping` também aceita `destination_postal_code`. Quando informado,
valida o destino e devolve o endereço junto ao resultado. Campo Grande/MS é
identificado como local; outro estado é externo. Para outras cidades de MS, o
cliente ainda precisa confirmar se está na região de entrega. Sem CEP, permanece
disponível o campo `destination_region`, confirmado pelo cliente.

```python
tool = next(t for t in tools if t.name == "calculate_shipping")
result = tool.invoke(
    {
        "items": [{"product_id": 83}],
        "destination_postal_code": "79002-000",
    }
)
```

## Frete no escopo do desafio

A tool consulta produtos, quantidades e subtotal no banco, além do destino opcional.
Mantém o nome `calculate_shipping` por compatibilidade, mas não calcula nem escolhe
uma tarifa: `shipping_brl` permanece `null`. Taxas, limites de gratuidade, modalidades
e prazos comerciais foram retirados do código.

### Região metropolitana de Campo Grande

Retorna `requires_policy` e orienta o agente a consultar `search_policy`. As condições
apresentadas vêm do manual vigente, com suas referências e exceções, não de constantes
da aplicação ou respostas antigas no histórico. Sem política suficiente, verifica-se
o valor com um funcionário.

### Fora da região metropolitana

O padrão de resposta é:

> Vou verificar com um funcionário quanto fica o frete para o seu destino e te informar depois.

Não exige peso, dimensões ou CEP antes dessa orientação quando a cidade já deixa claro
que o destino é externo. Não oferece modalidades, valores ou prazos de entrega fixos.
Não promete prazo de retorno nem afirma contato já realizado ou agendamento automático.

A tool retorna `requires_quote` e `staff_quote_required=true`, com `staff_contacted=false`
e `follow_up_scheduled=false`. Isso representa a orientação de atendimento; não há
integração de notificação ou acompanhamento humano automático implementada.

## Pendências

- Pesquisa e validação de peso/dimensões do produto e da embalagem.
- Eventual tabela de frete por região do país, a ser avaliada.
- Processo efetivo de contato com funcionário e retorno ao cliente.

A busca DuckDuckGo não está disponível no agente e sua dependência foi removida.

## Testes

```bash
uv run pytest tests/test_external_tools.py tests/test_tools_contracts.py
```

Testes usam respostas HTTP simuladas. Com `TEST_DATABASE_URL`, verificam a identificação
do destino, a ausência de tarifas fixas e a orientação de atendimento externo, em
PostgreSQL dedicado. Os testes do agente também verificam o recebimento de políticas
com valores e prazos diferentes sem alterar o código da tool.
