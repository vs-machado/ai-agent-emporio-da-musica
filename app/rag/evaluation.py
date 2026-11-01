"""Benchmark inicial de cobertura de evidências, sem gerar respostas por LLM."""

from time import perf_counter

from app.rag.parser import token_count
from app.rag.retrieval import LlamaIndexPolicyRetriever

CASES = (
    ("horario", "Qual é o horário de atendimento aos sábados?", {"2"}),
    ("pix_promocao", "Produto em promoção tem mais 5% de desconto no PIX?", {"3", "6.2"}),
    ("parcelamento", "Quais são as condições para parcelar no cartão de crédito?", {"3", "3.1"}),
    ("arrependimento", "Comprei online e quero devolver. Quem paga o frete?", {"4.1"}),
    (
        "preferencia",
        "Posso trocar um instrumento por outro modelo? Há itens não elegíveis?",
        {"4.3", "4.4"},
    ),
    ("frete_local", "Qual o frete para um pedido de exatamente R$ 500 em Campo Grande?", {"5.1"}),
    ("frete_especial", "Como funciona o envio de um piano para outra cidade?", {"5.2"}),
    ("garantia", "Qual é a garantia legal e quais danos não são cobertos?", {"8.1", "8.3"}),
    ("privacidade", "Para quais finalidades vocês usam meus dados pessoais?", {"9"}),
    ("reclamacao", "Como faço uma reclamação e qual o prazo de retorno?", {"7.3"}),
)


def evaluate(retriever: LlamaIndexPolicyRetriever) -> dict:
    """Mede cobertura das seções esperadas e informa o caminho realmente utilizado.

    Args:
        retriever: Retriever conectado a um índice ativo.

    Returns:
        Resultados por pergunta, recall médio de seções e proporção de casos completos.
        O benchmark não avalia geração de respostas nem prova correção das políticas.
    """
    results = []
    for name, query, expected in CASES:
        started = perf_counter()
        result = retriever.retrieve(query)
        elapsed = perf_counter() - started
        retrieved = {p.section for p in result.passages}
        found = expected & retrieved
        results.append(
            {
                "case": name,
                "query": query,
                "mode": result.mode,
                "expected_sections": sorted(expected),
                "retrieved_sections": sorted(retrieved),
                "missing_sections": sorted(expected - retrieved),
                "section_recall": len(found) / len(expected),
                "complete": expected <= retrieved,
                "candidate_count": result.candidate_count,
                "context_tokens": sum(token_count(p.model_dump_json()) for p in result.passages),
                "latency_seconds": round(elapsed, 3),
            }
        )
    return {
        "case_count": len(results),
        "mean_section_recall": sum(r["section_recall"] for r in results) / len(results),
        "complete_case_rate": sum(r["complete"] for r in results) / len(results),
        "results": results,
    }
