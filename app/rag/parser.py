"""Extração por estrutura do manual, preservando tabelas e páginas."""

import re
from dataclasses import dataclass, field
from io import BytesIO

import tiktoken
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.rag.errors import PolicyIndexingError

PARSER_VERSION = "structure-aware-v1"
TOKEN_ENCODING = "cl100k_base"
HEADING = re.compile(r"^(\d+(?:\.\d+)*)(?:\.)?\s+(.+)$")
FOOTER = re.compile(r"^Página\s+\d+$")
HEADER = "Empório da Música Manual de Políticas e Procedimentos"


def token_count(text: str) -> int:
    return len(tiktoken.get_encoding(TOKEN_ENCODING).encode(text, disallowed_special=()))


@dataclass
class Section:
    number: str
    title: str
    page_start: int
    page_end: int
    lines: list[str] = field(default_factory=list)
    hierarchy: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.blocks)

    @property
    def parent(self) -> str | None:
        return self.number.rsplit(".", 1)[0] if "." in self.number else None


@dataclass
class ParsedManual:
    version: str
    sections: list[Section]


def structured_blocks(lines: list[str]) -> list[str]:
    """Reconstrói parágrafos, itens e tabelas a partir do layout extraído.

    Args:
        lines: Linhas com alinhamento horizontal preservado pelo pypdf.

    Returns:
        Blocos completos; tabelas usam Markdown e preservam suas colunas.

    Raises:
        PolicyIndexingError: Uma linha de tabela não mantém as colunas esperadas.
    """
    blocks: list[str] = []
    paragraph: list[str] = []
    table: list[list[str]] = []

    def flush_paragraph():
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    def flush_table():
        if table:
            rows = [table[0], ["---"] * len(table[0]), *table[1:]]
            blocks.append("\n".join("| " + " | ".join(row) + " |" for row in rows))
            table.clear()

    for raw in lines:
        stripped = raw.strip()
        columns = re.split(r" {2,}", stripped)
        if re.match(r"^(?:•|\d+\.)\s", stripped):
            columns = [stripped]
        if len(columns) > 1:
            flush_paragraph()
            if table and len(columns) != len(table[0]):
                raise PolicyIndexingError("Tabela com colunas inconsistentes; revise a extração.")
            table.append(columns)
        elif table and len(raw) - len(raw.lstrip()) >= 12:
            table[-1][-1] += " " + stripped
        else:
            flush_table()
            if re.match(r"^(?:•|\d+\.)\s|^[\wÀ-ÿ /]+:", stripped):
                flush_paragraph()
            paragraph.append(re.sub(r"\s+", " ", stripped))
    flush_table()
    flush_paragraph()
    return blocks


def parse_manual(content: bytes) -> ParsedManual:
    """Extrai unidades numeradas sem usar a página como limite de chunk.

    Args:
        content: PDF original; não requer OCR para o manual atual.

    Returns:
        Versão declarada e seções com hierarquia, blocos e intervalo de páginas.

    Raises:
        PolicyIndexingError: PDF ilegível, sem versão ou com estrutura inconsistente.
    """
    try:
        reader = PdfReader(BytesIO(content))
        pages = [
            page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False)
            for page in reader.pages
        ]
    except (PdfReadError, ValueError, OSError) as exc:
        raise PolicyIndexingError("Não foi possível extrair o PDF de políticas.") from exc
    version = re.search(r"Versão\s+(\d+(?:\.\d+)+)", "\n".join(pages))
    if not version:
        raise PolicyIndexingError("O manual não informa uma versão reconhecível.")
    sections: list[Section] = []
    current = None
    for page_number, page in enumerate(pages, 1):
        for raw in page.replace("\u200b", "").splitlines():
            stripped = raw.strip()
            if not stripped or stripped == HEADER or FOOTER.fullmatch(stripped):
                continue
            if stripped.startswith(("Última atualização:", "Empório da Música — Sua música")):
                continue
            heading = HEADING.fullmatch(raw)
            if heading:
                current = Section(heading[1], heading[2], page_number, page_number)
                sections.append(current)
            elif current:
                current.lines.append(raw)
                current.page_end = page_number
    if not sections:
        raise PolicyIndexingError("Nenhuma seção numerada encontrada no PDF.")
    by_number = {}
    for section in sections:
        if section.number in by_number or (section.parent and section.parent not in by_number):
            raise PolicyIndexingError("Numeração de seções duplicada ou sem seção ancestral.")
        label = f"{section.number} — {section.title}"
        section.hierarchy = (
            [*by_number[section.parent].hierarchy, label] if section.parent else [label]
        )
        section.blocks = structured_blocks(section.lines)
        by_number[section.number] = section
    return ParsedManual(version[1], sections)


def build_nodes(
    manual: ParsedManual, source: str, index_id: str, chunk_tokens: int
) -> list[TextNode]:
    """Cria nodes LlamaIndex com títulos e unidades estruturais sem overlap arbitrário.

    Args:
        manual: Seções extraídas e validadas.
        source: Nome do PDF para referências.
        index_id: Identificador da versão do índice.
        chunk_tokens: Máximo de tokens de texto por node, incluindo a hierarquia.

    Returns:
        Nodes com referência à seção para expansão; seções vazias servem só à hierarquia.

    Raises:
        PolicyIndexingError: Bloco indivisível excede o limite ou não há texto indexável.
    """
    nodes = []
    for section in manual.sections:
        title = " > ".join(section.hierarchy)
        groups: list[str] = []
        body = ""
        for block in section.blocks:
            if token_count(f"{title}\n\n{block}") > chunk_tokens:
                raise PolicyIndexingError(
                    f"Bloco da seção {section.number} excede o limite; "
                    "revise sua divisão estrutural."
                )
            joined = f"{body}\n\n{block}" if body else block
            if body and token_count(f"{title}\n\n{joined}") > chunk_tokens:
                groups.append(body)
                body = block
            else:
                body = joined
        if body:
            groups.append(body)
        for position, group in enumerate(groups):
            nodes.append(
                TextNode(
                    id_=f"{index_id}:{section.number}:{position}",
                    text=f"{title}\n\n{group}",
                    metadata={
                        "index_id": index_id,
                        "source": source,
                        "document_version": manual.version,
                        "section": section.number,
                        "title": section.title,
                        "hierarchy": section.hierarchy,
                        "page_start": section.page_start,
                        "page_end": section.page_end,
                        "body": group,
                    },
                    excluded_embed_metadata_keys=[
                        "index_id",
                        "source",
                        "document_version",
                        "section",
                        "title",
                        "hierarchy",
                        "page_start",
                        "page_end",
                        "body",
                    ],
                    excluded_llm_metadata_keys=["body"],
                    relationships={
                        NodeRelationship.PARENT: RelatedNodeInfo(
                            node_id=f"{index_id}:{section.number}"
                        )
                    },
                )
            )
    if not nodes:
        raise PolicyIndexingError("O manual não contém texto indexável.")
    return nodes
