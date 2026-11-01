"""Cobertura estrutural da fonte real, sem banco nem chamadas de embeddings."""

import pytest

from app.rag.errors import PolicyIndexingError
from app.rag.indexing import DEFAULT_MANUAL_PATH
from app.rag.parser import (
    ParsedManual,
    Section,
    build_nodes,
    parse_manual,
    structured_blocks,
    token_count,
)


@pytest.fixture(scope="module")
def manual():
    return parse_manual(DEFAULT_MANUAL_PATH.read_bytes())


def test_real_manual_keeps_policy_units_and_hierarchy(manual):
    nodes = build_nodes(manual, "manual.pdf", "version", 1000)
    assert manual.version == "2.1"
    assert len(manual.sections) == 28
    assert len(nodes) == 25
    by_section = {n.metadata["section"]: n for n in nodes}
    regret = by_section["4.1"]
    assert "Política de Trocas e Devoluções" in regret.text
    for condition in ["7 (sete) dias", "embalagem original", "10 dias úteis", "por conta da loja"]:
        assert condition in regret.text
    assert "Página 4" not in regret.text
    assert "Manual de Políticas e Procedimentos" not in regret.text


@pytest.mark.parametrize("number,pages", [("5.2", (4, 5)), ("7.2", (6, 7)), ("9", (7, 8))])
def test_sections_cross_page_boundaries(manual, number, pages):
    section = next(s for s in manual.sections if s.number == number)
    assert (section.page_start, section.page_end) == pages
    if number == "9":
        assert "consentimento explícito" in section.text
        assert "exclusão de seus dados" in section.text
    if number == "7.2":
        assert "1. Saudação:" in section.text
        assert "5. Fechamento:" in section.text
        assert "|" not in section.text


def test_tables_preserve_column_associations_and_wrapped_cells(manual):
    sections = {s.number: s for s in manual.sections}
    assert (
        "| PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela |" in sections["3"].text
    )
    assert "| SEDEX (Correios) | 2 a 5 dias úteis | Sim | Incluído |" in sections["5.2"].text
    assert "| Sábado | 09:00 às 13:00 |" in sections["2"].text
    assert "cotação individual" in sections["5.2"].text


def test_ambiguities_and_conflicting_contacts_are_not_corrected(manual):
    sections = {s.number: s for s in manual.sections}
    assert "exceto abaixo de R$ 50,00" in sections["3.1"].text
    assert "acima de R$ 500,00" in sections["5.1"].text
    assert "abaixo de R$ 500,00" in sections["5.1"].text
    assert "3341-4444" in sections["1.2"].text
    assert "3321-4500" in sections["7"].text


def test_large_section_splits_only_between_complete_blocks():
    blocks = [f"• Regra {i}: condição especial sem exceção. " * 15 for i in range(4)]
    section = Section("1", "Política", 1, 2, hierarchy=["1 — Política"], blocks=blocks)
    nodes = build_nodes(ParsedManual("1.0", [section]), "manual.pdf", "version", 256)
    assert len(nodes) > 1
    assert all(token_count(n.text) <= 256 for n in nodes)
    for block in blocks:
        assert sum(block in n.text for n in nodes) == 1


def test_indivisible_oversized_block_is_rejected_without_truncation():
    section = Section("1", "Política", 1, 1, hierarchy=["Política"], blocks=["condição " * 2000])
    with pytest.raises(PolicyIndexingError, match="divisão estrutural"):
        build_nodes(ParsedManual("1.0", [section]), "manual.pdf", "version", 256)


def test_bad_pdf_is_rejected():
    with pytest.raises(PolicyIndexingError):
        parse_manual(b"invalid PDF")


def test_inconsistent_table_is_not_silently_flattened():
    with pytest.raises(PolicyIndexingError, match="colunas"):
        structured_blocks(["Forma    Prazo    Seguro", "PAC    12 dias"])
