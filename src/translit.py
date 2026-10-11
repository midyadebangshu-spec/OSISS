"""Romanized Bengali/Hindi query support.

Users often type Indic questions in Latin letters ("goti kake bole?"), which neither BM25 nor the
embedder maps reliably to the native-script books. `romanized_variants` detects such queries and
transliterates them to Bengali and Devanagari with AI4Bharat's IndicXlit (CTranslate2 build).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional

from config import settings
from ranking import QUERY_STOPWORDS

# The model's Bengali output sometimes uses Assamese letters; map them to their Bengali forms.
ASSAMESE_TO_BENGALI = str.maketrans({"ৰ": "র", "ৱ": "ব"})

# Words that are not English but are very common in romanized Bengali/Hindi questions.
INDIC_MARKERS: FrozenSet[str] = frozenset(
    """
    kake bole bolo bolun kise kisko kisse kehte kahte kehlate kahlate kehlata kahlata kya kyun kyu kaise
    kitna kitni koto keno kemon kothay konti kobe hoy hain hai holo kaun kon ebong evam aur mein
    ki ke ka ko se
    """.split()
)

TOKEN_PATTERN = re.compile(r"[a-z]+")
# Single letters ("a", "I", variable names) say nothing about the language.
MIN_WORD_LENGTH = 2
LANGUAGE_TAGS = {"bn": "__bn__", "hi": "__hi__"}


@dataclass(frozen=True)
class RomanVariant:
    """A query rewritten in one script."""

    language: str  # "bn" or "hi"
    text: str  # best spelling of every word: used for embeddings, reranking and QA
    keyword_text: str  # all candidate spellings: used for the BM25 query


@lru_cache(maxsize=1)
def get_translator():
    """Load the CTranslate2 transliterator once; None when disabled, missing or not installed."""
    if not settings.translit_enabled:
        return None
    if not os.path.isdir(settings.translit_model_path):
        print(f"[OSISS] Transliteration model not found at '{settings.translit_model_path}'; romanized queries off.")
        return None
    try:
        import ctranslate2  # pylint: disable=import-outside-toplevel

        return ctranslate2.Translator(settings.translit_model_path, device="cpu")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Transliteration unavailable: {exc}")
        return None


@lru_cache(maxsize=1)
def get_english_vocabulary() -> FrozenSet[str]:
    """English words known to the corpus (empty when the vocabulary file has not been built)."""
    path = settings.english_vocab_path
    if not os.path.isfile(path):
        return frozenset()
    with open(path, encoding="utf-8") as handle:
        return frozenset(line.strip() for line in handle if line.strip())


def classify_query(query: str, vocabulary: FrozenSet[str]) -> str:
    """Classify a query as "english", "romanized" or "ambiguous".

    - "romanized": Latin script with Indic marker words ("kake", "kise", "bole"...).
    - "ambiguous": Latin script, no marker words, but some word is unknown to the English vocabulary
      (a rare English term, a lone romanized word, or a mix like "what is gati"); the caller decides
      by checking how well the query as typed matches the database.
    - "english": native script, no words, or every word is a known English word.
    """
    if any("\u0900" <= char <= "\u0dff" for char in query):
        return "english"
    tokens = [token for token in TOKEN_PATTERN.findall(query.lower()) if len(token) >= MIN_WORD_LENGTH]
    if not tokens:
        return "english"
    if any(token in INDIC_MARKERS for token in tokens):
        return "romanized"
    if vocabulary and any(token not in vocabulary for token in tokens):
        return "ambiguous"
    return "english"


def query_mode(query: str) -> str:
    """`classify_query` with the loaded vocabulary; "english" when transliteration is unavailable."""
    if get_translator() is None:
        return "english"
    return classify_query(query, get_english_vocabulary())


def transliterate_words(translator, words: List[str], language: str, topk: int) -> List[List[str]]:
    """Candidate native-script spellings (best first) for each Latin-script word."""
    if not words:
        return []
    tag = LANGUAGE_TAGS[language]
    results = translator.translate_batch(
        [[tag] + list(word) for word in words],
        beam_size=max(4, topk),
        num_hypotheses=topk,
    )
    candidates = []
    for result in results:
        spellings: List[str] = []
        for hypothesis in result.hypotheses:
            spelling = "".join(hypothesis).translate(ASSAMESE_TO_BENGALI)
            if spelling and spelling not in spellings:
                spellings.append(spelling)
        candidates.append(spellings)
    return candidates


def romanized_variants(query: str, partial: bool = False) -> List[RomanVariant]:
    """Bengali and Hindi rewrites of a romanized query.

    `partial` rewrites only the words that are marker words or unknown to the English vocabulary and
    leaves known English words as typed ("what is gati" -> "what is গতি"); otherwise every word is rewritten.
    Empty when transliteration is unavailable or the query has no Latin words.
    """
    translator = get_translator()
    if translator is None:
        return []

    vocabulary = get_english_vocabulary()
    parts = re.findall(r"[A-Za-z]+|[^A-Za-z\s]+", query)
    positions = [
        index
        for index, part in enumerate(parts)
        if part.isascii()
        and part[0].isalpha()
        and len(part) >= MIN_WORD_LENGTH
        and (not partial or not vocabulary or part.lower() in INDIC_MARKERS or part.lower() not in vocabulary)
    ]
    if not positions:
        return []
    words = [parts[index].lower() for index in positions]
    topk = max(1, settings.translit_topk)

    variants: List[RomanVariant] = []
    for language in ("bn", "hi"):
        candidates = transliterate_words(translator, words, language, topk)
        best, keyword = list(parts), list(parts)
        for index, spellings in zip(positions, candidates):
            if spellings:
                best[index] = spellings[0]
                # Question words ("কাকে বলে") get stripped from the BM25 query later; keep their variants out of it.
                keyword[index] = spellings[0] if spellings[0].casefold() in QUERY_STOPWORDS else " ".join(spellings)
        variants.append(RomanVariant(language, " ".join(best), " ".join(keyword)))
    return variants


def english_vocabulary_words(texts: Optional[List[str]] = None) -> List[str]:
    """Distinct lowercase words of at least 2 letters seen at least twice (drops OCR noise)."""
    counts: Dict[str, int] = {}
    for text in texts or []:
        for word in TOKEN_PATTERN.findall(text.lower()):
            counts[word] = counts.get(word, 0) + 1
    return sorted(word for word, count in counts.items() if count >= 2 and len(word) >= 2)
