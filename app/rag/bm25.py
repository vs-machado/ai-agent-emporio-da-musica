"""BM25 Okapi sobre tokens normalizados, com IDF positivo (variante Lucene)."""

from collections import Counter
from math import log1p

K1 = 1.2
B = 0.75


def bm25_scores(documents: list[list[str]], query: list[str]) -> list[float]:
    """Pontua todo o corpus antes do corte top-k; termos repetidos na consulta contam uma vez.

    Documentos vazios participam das estatísticas, mas recebem zero. Não faz
    tokenização: corpus e consulta devem usar a mesma normalização linguística.
    """
    if not documents:
        return []
    counts = [Counter(tokens) for tokens in documents]
    average_length = sum(map(len, documents)) / len(documents)
    if not average_length:
        return [0.0] * len(documents)
    terms = set(query)
    frequencies = Counter(term for count in counts for term in terms if term in count)
    scores = []
    for tokens, count in zip(documents, counts, strict=True):
        normalization = K1 * (1 - B + B * len(tokens) / average_length)
        score = 0.0
        for term in sorted(terms & count.keys()):
            idf = log1p((len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            frequency = count[term]
            score += idf * frequency * (K1 + 1) / (frequency + normalization)
        scores.append(score)
    return scores
