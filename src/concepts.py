"""Concept graph across the textbooks: which concepts the books define, where they appear and how they relate.

Model-free and service-free (the runner is `build_concepts.py`, storage and lookups are in `concept_store.py`), so
everything here is unit-tested on plain strings. All of it is extractive: concepts are terms the books define or
emphasise, links are sentences in which two concepts appear together.

* **Candidates**: definition sentences ("X is ...", "... is called X", "...কে X বলে", "...को X कहते हैं"), bold short
  lines (headings and key terms, from the page-asset cache) and bilingual glosses ("दर्पण का द्वारक (aperture)").
* **Merging**: only by spelling (case, plural, hyphens) and through glosses: the books' own "Indic term (English
  term)" pairs tie Bengali / Hindi names to the English one. Embeddings were measured too unreliable for this
  (true translations 0.39-0.89 cosine, unrelated terms up to 0.75 with BGE-M3).
* **Mentions**: alias matching over sentence tokens; Bengali / Hindi aliases match with case endings (সমাজ ->
  সমাজের), English ones with plural endings.
* **Links**: sentence and chunk co-occurrence scored with normalized PMI, with evidence sentences per book, a
  relation type when the wording shows one ("is a type of", "consists of", "used for", "unlike", "causes") and an
  order hint (which concept most books reach first).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from layout import DEFINITION_PATTERNS, clean_term, language_of

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!।॥])\s+")
WORD_RE = re.compile(r"[^\s.,;:!?()\[\]{}\"'“”‘’।॥|/\\<>=+*&^%$#@~`…–—]+")
INDIC_RE = re.compile(r"[ऀ-ॿঀ-৿]")
LETTER_RE = re.compile(r"[A-Za-zऀ-ॿঀ-৿]")
GLOSS_RE = re.compile(
    r"((?:[ঀ-৿ऀ-ॿ‌‍]+[\s\-]?){1,5})\s*\(\s*([A-Za-z][A-Za-z\- ]{2,40}?)\s*\)"
)
NUMBERING_RE = re.compile(r"^\s*(?:\(?[0-9০-৯०-९]+(?:\.[0-9০-৯०-९]+)*[.)]?|[ivx]+[.)]|[a-z][.)])\s+", re.IGNORECASE)

# Words that are never a concept on their own, and may not start or end one.
STOPWORDS = frozenset(
    """a an the of and or in on to is are was were be been for with as by at from that this these those it its
    which who whom how why when where what do does did can could will would shall should may might must here there
    then than such so if else also very more most less least much many some any all each every other another both
    either neither not no yes one two three first second third last next new old same different following above
    below example examples figure fig table activity activities exercise exercises question questions answer answers
    note notes summary chapter chapters section page unit lesson introduction conclusion review problem problems
    solution solutions step steps case cases result results type types kind kinds part parts way ways thing things
    use uses used using called known given shown let we you they he she i us our your their his her them me my
    keyword keywords objective objectives learning outcomes points point key terms term definition meaning
    since because while although though whereas unless until after before during once thus hence therefore however
    কী কি কে কাকে বলে হয় হল হলো এবং ও কোন কোনো কেন কীভাবে কিভাবে করে এর একটি এক এই সেই যে যা তা তার এটা সেটা
    এগুলো এখন প্রশ্ন উত্তর চিত্র অনুশীলন উদ্দেশ্য উদ্দেশ সারণি কারণ অর্থ ধরা তাই যদি তবে কিন্তু অথবা
    সেগুলি সেগুলো এগুলি তাদের তারা এরা ওরা ইহা উহা যাহা তাহা তিনি আমরা আমাদের
    क्या है हैं किसे कहते को की का के में से और एक कैसे क्यों कौन यह वह ये वे इस उस यदि तब अतः फिर केवल जब लेकिन
    तथा एवं अथवा प्रश्न उत्तर चित्र क्रियाकलाप सारणी उदाहरण इसलिए इसे उसे जो कि भी ही हम हमें हमारे हमारा आप उन्हें
    openstax ncert""".split()
)
# Bengali / Hindi postpositions and particles that often stick to the left of a glossed term.
INDIC_PARTICLES = frozenset(
    """को का के की में से पर और तथा एवं है हैं था थे या ने द्वारा लिए वाले वाली जो कि भी ही इस उस यह वह
    কে এর র থেকে দিয়ে এবং ও হয় হল হলো যে যা এই সেই এক একটি মধ্যে জন্য দ্বারা কোনো বা হচ্ছে যথাক্রমে অর্থাৎ
    অথবা কিংবা এটি এটা সেটি সেটা এগুলি এগুলো""".split()
)
GENERIC_EN = frozenset(
    """time number value values data system systems process method methods form forms set order end line lines
    area level amount size change changes state states program programs model models rate
    work energy object objects series topic topics reader statement origin magnitude property properties structure
    group groups item items list information material materials body bodies factor factors effect effects feature
    features concept concepts principle principles description story policy fire advantage advantages disadvantage
    disadvantages drawback drawbacks benefit benefits limitation limitations category categories region regions
    definitions comparison length width height reverse main purpose goal role context approach technique techniques
    aspect aspects basis nature condition conditions situation range quantity quality character content means sense
    cost costs reason reasons idea ideas fact facts detail details purpose purposes requirement requirements
    book books student students attempt attempts person people side sides round green positive negative direct
    natural physical general special simple common normal total""".split()
)
# Single English adjectives / participles ("balanced", "reliable") are rarely concepts on their own.
ADJECTIVE_RE = re.compile(r"^[a-z]+(?:ed|ly|able|ible|ful|ous|less)$")
ADJECTIVE_EXCEPTIONS = frozenset("variable variables vegetable vegetables cable cables timetable red seed bed".split())

INDIC_SUFFIX_MAX = 3  # characters of a case ending allowed after a Bengali / Hindi alias


# ---------------------------------------------------------------------------------------------- text helpers


def normalize(text: str) -> str:
    """Canonical spelling of a term: NFC, lower case, numbering / quotes / outer punctuation stripped."""
    text = unicodedata.normalize("NFC", text or "").replace("­", "")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    # Leading bullets of any kind (fonts use private-use glyphs too): everything before the first letter / digit.
    # Running headers alternate the two: "10 • Liquids and Solids".
    for _ in range(3):
        text = NUMBERING_RE.sub("", re.sub(r"^[^\wऀ-৿]+", "", text))
        text = re.sub(r"^[A-Za-z]\s+(?=[•·▪■◆|])", "", text)  # appendix headers: "E • Water Properties"
    text = re.sub(r"[\"“”‘’'`*_]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:!?-–—()[]").lower()
    words = text.split()
    while words and words[0] in {"a", "an", "the"}:
        words = words[1:]
    return " ".join(words)


def words_of(text: str) -> List[str]:
    return WORD_RE.findall(unicodedata.normalize("NFC", text or "").lower())


def sentences(text: str) -> List[str]:
    return [sentence.strip() for sentence in SENTENCE_SPLIT_RE.split(" ".join((text or "").split())) if sentence.strip()]


def is_indic(text: str) -> bool:
    return bool(INDIC_RE.search(text))


def singular(word: str) -> str:
    """English plural -> singular for matching (queues -> queue, bodies -> body, classes -> class)."""
    if len(word) <= 3 or is_indic(word) or not word.isascii():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def term_key(term: str) -> str:
    """Merge key: normalized, last English word singular, hyphens as spaces."""
    words = normalize(term).replace("-", " ").split()
    if words:
        words[-1] = singular(words[-1])
    return " ".join(words)


def acceptable(term: str) -> bool:
    """Plausible concept name: 1-4 words of letters, not a stopword, not starting / ending with a function word."""
    words = term.split()
    if not 1 <= len(words) <= 4 or not 3 <= len(term) <= 48:
        return False
    letters = len(LETTER_RE.findall(term))
    if letters < 0.7 * len(term.replace(" ", "")) or letters < 3:
        return False
    if words[0] in STOPWORDS or words[-1] in STOPWORDS or words[-1] in INDIC_PARTICLES:
        return False
    if len(set(words)) < len(words):
        return False  # "functions functions"
    if re.search(r"[\[\]{}()=<>|~^_•·▪■◆]", term) or (len(words) > 1 and len(words[-1]) == 1):
        return False  # code / formula fragments ("element data[k", "binary tree t")
    if len(words) == 1 and (
        words[0] in GENERIC_EN
        or (words[0].isascii() and len(words[0]) < 4)
        or (ADJECTIVE_RE.match(words[0]) and words[0] not in ADJECTIVE_EXCEPTIONS)
    ):
        return False
    return not any(char.isdigit() for char in term)


# ---------------------------------------------------------------------------------------------- candidates


@dataclass
class Candidate:
    term: str
    source: str  # "definition" | "bold" | "gloss"
    book_id: int
    page: int
    chunk_id: Optional[int] = None
    sentence: Optional[str] = None
    gloss: Optional[str] = None  # English gloss of an Indic term


def definition_candidates(sentence: str) -> List[str]:
    """Terms a sentence defines (layout.DEFINITION_PATTERNS, language from the script)."""
    if len(sentence.split()) > 60:
        return []
    found = []
    for pattern in DEFINITION_PATTERNS.get(language_of(sentence), []):
        match = pattern.search(sentence)
        if match:
            term = normalize(clean_term(match.group("term").strip(" -–:")))
            if acceptable(term):
                found.append(term)
            break
    return found


# Endings of inflected words (case markers, verb forms) that do not start a glossed term.
INFLECTED_RE = re.compile(r"(?:কে|ের|েরা|টি|গুলো|গুলি|দের|তে|ই|না|ने|ना|ता|ती|ते|कर|का|की|के|ों)$")


def trim_indic_term(phrase: str) -> str:
    """'दोष को दूर-दृष्टिता' -> 'दूर-दृष्टिता': the glossed term is the last word, or the last two when the earlier one
    is not a particle or an inflected word ('दर्पण का द्वारक' -> 'द्वारक', 'प्रकाश संश्लेषण' stays)."""
    words = phrase.split()
    if not words:
        return ""
    kept = [words[-1]]
    if len(words) >= 2:
        before = words[-2]
        if before not in INDIC_PARTICLES and before not in STOPWORDS and not INFLECTED_RE.search(before):
            kept.insert(0, before)
    return " ".join(kept)


def gloss_pairs(text: str) -> List[Tuple[str, str]]:
    """(Indic term, English term) pairs printed as 'Indic term (English term)'."""
    pairs = []
    for match in GLOSS_RE.finditer(text or ""):
        indic = normalize(trim_indic_term(match.group(1).strip(" -")))
        english = normalize(match.group(2))
        if acceptable(indic) and acceptable(english) and len(english.split()) <= 4:
            pairs.append((indic, english))
    return pairs


def bold_candidate(line_text: str) -> Optional[str]:
    """A bold heading / key-term line ("4.2 Linked Lists", "Photosynthesis:") as a term, if it is one."""
    text = normalize(re.sub(r"[:：]\s*$", "", line_text))
    return text if acceptable(text) and len(text.split()) <= 4 else None


# ---------------------------------------------------------------------------------------------- concepts


@dataclass
class Concept:
    key: str
    surfaces: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    source_books: Dict[str, Set[int]] = field(default_factory=lambda: defaultdict(set))
    aliases: Set[str] = field(default_factory=set)  # normalized spellings to match in text
    english: Optional[str] = None

    @property
    def label(self) -> str:
        english = [surface for surface, _ in self.surfaces.most_common() if not is_indic(surface)]
        return english[0] if english else self.surfaces.most_common(1)[0][0]


def build_concepts(candidates: Sequence[Candidate]) -> Dict[str, Concept]:
    """Group candidates by spelling, then merge Indic names into their English gloss's concept."""
    concepts: Dict[str, Concept] = {}
    glosses: Dict[str, Counter] = defaultdict(Counter)
    for candidate in candidates:
        key = term_key(candidate.term)
        if not key:
            continue
        concept = concepts.setdefault(key, Concept(key))
        concept.surfaces[candidate.term] += 1
        concept.sources[candidate.source] += 1
        concept.source_books[candidate.source].add(candidate.book_id)
        concept.aliases.add(normalize(candidate.term))
        if candidate.gloss:
            english_key = term_key(candidate.gloss)
            glosses[key][english_key] += 1
            gloss_concept = concepts.setdefault(english_key, Concept(english_key))
            gloss_concept.surfaces[candidate.gloss] += 1
            gloss_concept.sources["gloss"] += 1
            gloss_concept.aliases.add(normalize(candidate.gloss))
    # An Indic name joins the English concept it is most often glossed with.
    for indic_key, counts in glosses.items():
        english_key = counts.most_common(1)[0][0]
        if indic_key == english_key or indic_key not in concepts or english_key not in concepts:
            continue
        indic, english = concepts.pop(indic_key), concepts[english_key]
        english.surfaces.update(indic.surfaces)
        english.sources.update(indic.sources)
        for source, books in indic.source_books.items():
            english.source_books[source] |= books
        english.aliases |= indic.aliases
    return concepts


# ---------------------------------------------------------------------------------------------- matching


class AliasMatcher:
    """Finds concept aliases in tokenized text (longest match first; case endings / plurals tolerated)."""

    def __init__(self, aliases: Dict[str, str]):
        """`aliases`: normalized alias -> concept key."""
        self.by_first: Dict[str, List[Tuple[Tuple[str, ...], str]]] = defaultdict(list)
        for alias, key in aliases.items():
            tokens = tuple(words_of(alias.replace("-", " ")))
            if tokens:
                self.by_first[self._base(tokens[0])].append((tokens, key))
        for options in self.by_first.values():
            options.sort(key=lambda option: -len(option[0]))

    @staticmethod
    def _base(token: str) -> str:
        return singular(token)

    @staticmethod
    def _same(alias_token: str, token: str, last: bool) -> bool:
        if alias_token == token or singular(token) == alias_token or singular(token) == singular(alias_token):
            return True
        if last and is_indic(alias_token):
            return token.startswith(alias_token) and len(token) - len(alias_token) <= INDIC_SUFFIX_MAX
        return False

    def _candidates(self, token: str) -> List[Tuple[Tuple[str, ...], str]]:
        options = list(self.by_first.get(self._base(token), ()))
        if is_indic(token):
            for cut in range(1, INDIC_SUFFIX_MAX + 1):
                if len(token) - cut >= 2:
                    options.extend(self.by_first.get(token[:-cut], ()))
        return options

    def find(self, text: str) -> List[Tuple[str, int]]:
        """(concept key, token position) for every match in `text`."""
        tokens = words_of(text.replace("-", " "))
        found: List[Tuple[str, int]] = []
        position = 0
        while position < len(tokens):
            best = None
            for alias_tokens, key in self._candidates(tokens[position]):
                size = len(alias_tokens)
                window = tokens[position : position + size]
                if len(window) == size and all(
                    self._same(a, t, index == size - 1) for index, (a, t) in enumerate(zip(alias_tokens, window))
                ):
                    if best is None or size > best[0]:
                        best = (size, key)
            if best:
                found.append((best[1], position))
                position += best[0]
            else:
                position += 1
        return found


# ---------------------------------------------------------------------------------------------- relations


RELATION_PATTERNS: List[Tuple[str, re.Pattern, bool]] = [
    # (relation, pattern on the text between the two concepts, True when it reads first -> second)
    ("is_a", re.compile(r"^\s*(?:is|are|was|were)\s+(?:also\s+)?(?:a|an|one)?\s*(?:special\s+|particular\s+)?(?:type|kind|form|example|class|category|variety)s?\s+of\s*(?:the\s+)?$", re.I), True),
    ("is_a", re.compile(r"^\s*,?\s*(?:a|an)\s+(?:type|kind|form)\s+of\s*$", re.I), True),
    ("is_a", re.compile(r"^\s*(?:such as|including|like|e\.g\.,?)\s*$", re.I), False),
    ("has_part", re.compile(r"^\s*(?:consists?|consisting)\s+of\s*(?:a|an|the|two|three|many)?\s*$|^\s*(?:is|are)\s+(?:made|composed)\s+(?:up\s+)?of\s*(?:a|an|the)?\s*$|^\s*(?:contains?|includes?|has|have)\s+(?:a|an|the|two|three|many)?\s*$", re.I), True),
    ("has_part", re.compile(r"^\s*(?:is|are)\s+(?:a\s+)?parts?\s+of\s*(?:the|a|an)?\s*$", re.I), False),
    ("used_for", re.compile(r"^\s*(?:is|are)\s+(?:mainly\s+|commonly\s+|often\s+)?used\s+(?:to|for|in)\s*(?:the|a|an)?\s*$", re.I), True),
    ("causes", re.compile(r"^\s*(?:causes?|produces?|leads?\s+to|results?\s+in|gives?\s+rise\s+to)\s*(?:the|a|an)?\s*$", re.I), True),
    ("contrast", re.compile(r"^\s*(?:and|vs\.?|versus|or)\s*(?:a|an|the)?\s*$", re.I), True),
    ("is_a", re.compile(r"^\s*(?:এক\s+প্রকার|এক\s+ধরনের|একটি)\s*$"), True),
    ("is_a", re.compile(r"^\s*,?\s*(?:एक\s+प्रकार\s+का|एक\s+प्रकार\s+की)\s*$"), True),
]
CONTRAST_CONTEXT_RE = re.compile(r"\b(?:difference|differences|differ|differentiate|distinguish|unlike|whereas|compared|contrast)\b|পার্থক্য|अंतर|अन्तर", re.I)


KEY_TERMS_RE = re.compile(r"key\s*terms|keywords|key\s*words|मुख्य\s*(?:शब्द|बिन्दु|बिंदु)|মূল\s*শব্দ|প্রধান\s*শব্দ", re.I)


def is_prose(sentence: str) -> bool:
    """Real sentences, not figure labels, tables or code read as text ("17 29 18 21 PTR NEXT Head ...")."""
    tokens = sentence.split()
    if len(tokens) < 4:
        return False
    numeric = sum(1 for token in tokens if any(char.isdigit() for char in token))
    if numeric / len(tokens) > 0.25:
        return False
    # Key-term lists ("Section Key Terms angular acceleration ... torque", "मुख्य बिन्दु (Keywords) ...") link every
    # term to every other; so do comma lists of bare terms.
    if KEY_TERMS_RE.search(sentence):
        return False
    parts = [part for part in sentence.split(",") if part.strip()]
    return not (len(parts) >= 6 and sum(len(part.split()) for part in parts) / len(parts) <= 3)


def classify_relation(sentence: str, first: str, second: str) -> Optional[Tuple[str, bool]]:
    """(relation, first_is_source) from the wording between two aliases in a sentence, or None."""
    lower = unicodedata.normalize("NFC", sentence).lower()
    a, b = lower.find(first), lower.find(second)
    if a < 0 or b < 0 or a == b:
        return None
    forward = a < b
    start, end = (a + len(first), b) if forward else (b + len(second), a)
    # Skip the rest of the matched word (plural / case ending: "data structure|s such as").
    while start < end and not lower[start].isspace():
        start += 1
    middle = lower[start:end]
    if len(middle.split()) > 8:
        return None
    for relation, pattern, first_to_second in RELATION_PATTERNS:
        if pattern.search(middle):
            if relation == "contrast" and not CONTRAST_CONTEXT_RE.search(lower):
                continue
            source_is_left = first_to_second
            return relation, source_is_left == forward
    return None


# ---------------------------------------------------------------------------------------------- graph


@dataclass
class Mention:
    key: str
    book_id: int
    page: int
    chunk_id: int
    sentence: str
    is_definition: bool


@dataclass
class Link:
    a: str
    b: str
    weight: float
    npmi: float
    sentence_count: int
    chunk_count: int
    books: Set[int]
    evidence: List[Dict]
    relation: Optional[str] = None
    relation_source: Optional[str] = None
    order: int = 0  # > 0: most books reach `a` before `b`


@dataclass
class Chunk:
    id: int
    book_id: int
    page: int
    text: str


def find_mentions(
    chunks: Sequence[Chunk], matcher: AliasMatcher, definitions: Dict[Tuple[int, str], Set[str]]
) -> Tuple[List[Mention], Dict[int, List[Tuple[str, ...]]]]:
    """Mentions, plus per chunk the tuple of concept keys found in each sentence (for co-occurrence)."""
    mentions: List[Mention] = []
    per_chunk: Dict[int, List[Tuple[str, ...]]] = {}
    for chunk in chunks:
        chunk_sentences = []
        seen_in_chunk: Set[str] = set()
        for sentence in sentences(chunk.text):
            keys = tuple(dict.fromkeys(key for key, _ in matcher.find(sentence)))
            chunk_sentences.append((keys, sentence))
            defined = definitions.get((chunk.id, sentence), set())
            for key in keys:
                if key in defined or key not in seen_in_chunk:
                    mentions.append(Mention(key, chunk.book_id, chunk.page, chunk.id, sentence[:500], key in defined))
                    seen_in_chunk.add(key)
        per_chunk[chunk.id] = [keys for keys, _ in chunk_sentences]
    return mentions, per_chunk


def build_links(
    chunks: Sequence[Chunk],
    matcher: AliasMatcher,
    aliases_of: Dict[str, Set[str]],
    min_chunks: int = 2,
    max_neighbours: int = 25,
    evidence_per_book: int = 2,
) -> List[Link]:
    """Concept pairs that appear together, scored with NPMI over chunks and backed by evidence sentences."""
    chunk_concepts: Dict[int, Set[str]] = {}
    pair_chunks: Counter = Counter()
    pair_sentences: Counter = Counter()
    pair_books: Dict[Tuple[str, str], Set[int]] = defaultdict(set)
    pair_evidence: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    first_page: Dict[Tuple[int, str], int] = {}

    for chunk in chunks:
        found: Set[str] = set()
        for sentence in sentences(chunk.text):
            keys = sorted(set(key for key, _ in matcher.find(sentence)))
            found.update(keys)
            if not is_prose(sentence):
                continue
            for index, a in enumerate(keys):
                for b in keys[index + 1 :]:
                    pair = (a, b)
                    pair_sentences[pair] += 1
                    pair_books[pair].add(chunk.book_id)
                    if len(sentence.split()) >= 4:
                        pair_evidence[pair].append(
                            {"book_id": chunk.book_id, "page": chunk.page, "chunk_id": chunk.id, "sentence": sentence[:600]}
                        )
        chunk_concepts[chunk.id] = found
        for key in found:
            slot = (chunk.book_id, key)
            first_page[slot] = min(first_page.get(slot, chunk.page), chunk.page)
        ordered = sorted(found)
        for index, a in enumerate(ordered):
            for b in ordered[index + 1 :]:
                pair_chunks[(a, b)] += 1
                pair_books[(a, b)].add(chunk.book_id)

    total = max(1, len(chunks))
    frequency: Counter = Counter(key for keys in chunk_concepts.values() for key in keys)
    links: List[Link] = []
    for (a, b), together in pair_chunks.items():
        # A link needs at least two sentences that name both (the evidence the explorer shows): links resting on
        # a single sentence were mostly coincidences in a hand-checked sample (7 of 10).
        if pair_sentences[(a, b)] < 2 or not pair_evidence[(a, b)]:
            continue
        p_ab = together / total
        p_a, p_b = frequency[a] / total, frequency[b] / total
        if p_ab >= 1:
            continue
        npmi = math.log(p_ab / (p_a * p_b)) / -math.log(p_ab)
        if npmi <= 0.05:
            continue
        sentence_count = pair_sentences[(a, b)]
        books = pair_books[(a, b)]
        weight = npmi * math.log1p(together + 2 * sentence_count) * (1 + 0.25 * (len(books) - 1))
        link = Link(a, b, round(weight, 4), round(npmi, 4), sentence_count, together, books, [])
        link.evidence = _pick_evidence(pair_evidence[(a, b)], evidence_per_book)
        link.relation, link.relation_source = _relation_for(link, pair_evidence[(a, b)], aliases_of)
        link.order = _order_hint(a, b, first_page)
        links.append(link)
    return _keep_strongest(links, max_neighbours)


def _pick_evidence(candidates: List[Dict], per_book: int) -> List[Dict]:
    """Up to `per_book` sentences per book, preferring mid-length ones (short = fragments, long = lists)."""
    chosen: List[Dict] = []
    counts: Counter = Counter()
    for item in sorted(candidates, key=lambda item: abs(len(item["sentence"].split()) - 22)):
        if counts[item["book_id"]] < per_book and not any(item["sentence"] == other["sentence"] for other in chosen):
            chosen.append(item)
            counts[item["book_id"]] += 1
    return chosen[:8]


def _relation_for(link: Link, evidence: List[Dict], aliases_of: Dict[str, Set[str]]) -> Tuple[Optional[str], Optional[str]]:
    votes: Counter = Counter()
    for item in evidence:
        for alias_a in sorted(aliases_of.get(link.a, ()), key=len, reverse=True):
            for alias_b in sorted(aliases_of.get(link.b, ()), key=len, reverse=True):
                result = classify_relation(item["sentence"], alias_a, alias_b)
                if result:
                    relation, a_is_source = result
                    votes[(relation, link.a if a_is_source else link.b)] += 1
                    break
            else:
                continue
            break
    if not votes:
        return None, None
    (relation, source), _ = votes.most_common(1)[0]
    return relation, source


def _order_hint(a: str, b: str, first_page: Dict[Tuple[int, str], int]) -> int:
    books = {book for book, key in first_page if key == a} & {book for book, key in first_page if key == b}
    score = 0
    for book in books:
        page_a, page_b = first_page[(book, a)], first_page[(book, b)]
        score += (page_a < page_b) - (page_a > page_b)
    return score


def _keep_strongest(links: List[Link], max_neighbours: int) -> List[Link]:
    """Keep a link when it is among the `max_neighbours` strongest of either end."""
    by_concept: Dict[str, List[Link]] = defaultdict(list)
    for link in links:
        by_concept[link.a].append(link)
        by_concept[link.b].append(link)
    keep: Set[int] = set()
    for items in by_concept.values():
        for link in sorted(items, key=lambda item: -item.weight)[:max_neighbours]:
            keep.add(id(link))
    return [link for link in links if id(link) in keep]


# ---------------------------------------------------------------------------------------------- whole build


@dataclass
class Graph:
    concepts: Dict[str, Concept]
    mentions: List[Mention]
    links: List[Link]


def extract_candidates(chunks: Sequence[Chunk], bold_lines: Iterable[Tuple[int, int, str]]) -> Tuple[List[Candidate], Dict[Tuple[int, str], Set[str]]]:
    """Candidates from chunk text and bold lines ((book_id, page, text)); also which sentence defines what."""
    candidates: List[Candidate] = []
    definitions: Dict[Tuple[int, str], Set[str]] = defaultdict(set)
    for chunk in chunks:
        for sentence in sentences(chunk.text):
            for term in definition_candidates(sentence):
                candidates.append(Candidate(term, "definition", chunk.book_id, chunk.page, chunk.id, sentence))
                definitions[(chunk.id, sentence)].add(term_key(term))
        for indic, english in gloss_pairs(chunk.text):
            candidates.append(Candidate(indic, "gloss", chunk.book_id, chunk.page, chunk.id, gloss=english))
    for book_id, page, text in bold_lines:
        term = bold_candidate(text)
        if term:
            candidates.append(Candidate(term, "bold", book_id, page))
    return candidates, definitions


def supported(concept: Concept) -> bool:
    """Enough evidence that a term is a concept. A single English word ("control", "safe", "variety") needs more:
    defined twice, glossed, or bold in two books; most generic links in a hand-checked sample hung on such words."""
    single_english = len(concept.key.split()) == 1 and not is_indic(concept.key)
    if single_english:
        return (
            concept.sources["definition"] >= 2
            or (concept.sources["definition"] >= 1 and concept.sources["bold"] >= 1)
            or bool(concept.sources["gloss"])
            or len(concept.source_books.get("bold", ())) >= 2
        )
    return bool(concept.sources["definition"] or concept.sources["gloss"] or concept.sources["bold"] >= 2)


def drop_boilerplate(chunks: Sequence[Chunk], min_repeats: int = 3) -> List[Chunk]:
    """Remove sentences repeated across the corpus (licence lines, credits, running heads, logos)."""
    counts: Counter = Counter()
    for chunk in chunks:
        counts.update({sentence.lower() for sentence in sentences(chunk.text)})
    repeated = {sentence for sentence, count in counts.items() if count >= min_repeats and len(sentence.split()) >= 3}
    if not repeated:
        return list(chunks)
    return [
        Chunk(chunk.id, chunk.book_id, chunk.page, " ".join(s for s in sentences(chunk.text) if s.lower() not in repeated))
        for chunk in chunks
    ]


def build_graph(
    chunks: Sequence[Chunk],
    bold_lines: Iterable[Tuple[int, int, str]] = (),
    min_chunks: int = 2,
    max_chunk_share: float = 0.12,
    log=print,
) -> Graph:
    """Concepts, their mentions and links for a corpus. `chunks` should exclude exercise chunks."""
    chunks = drop_boilerplate(chunks)
    candidates, definitions = extract_candidates(chunks, bold_lines)
    concepts = build_concepts(candidates)
    log(f"[OSISS] {len(candidates)} candidate terms -> {len(concepts)} distinct concepts before filtering.")

    # Remap definition keys after gloss merging.
    alias_to_key = {alias: key for key, concept in concepts.items() for alias in concept.aliases}
    matcher = AliasMatcher(alias_to_key)
    remapped = {slot: {alias_to_key.get(key, key) for key in keys} for slot, keys in definitions.items()}

    mentions, _ = find_mentions(chunks, matcher, remapped)
    chunk_count: Dict[str, Set[int]] = defaultdict(set)
    for mention in mentions:
        chunk_count[mention.key].add(mention.chunk_id)
    limit = max(10, int(max_chunk_share * len(chunks)))
    keep = {
        key
        for key, concept in concepts.items()
        if len(chunk_count.get(key, ())) >= min_chunks
        and len(chunk_count[key]) <= limit  # too common to be a useful concept ("water", "energy")
        and supported(concept)
    }
    concepts = {key: concept for key, concept in concepts.items() if key in keep}
    mentions = [mention for mention in mentions if mention.key in keep]
    log(f"[OSISS] {len(concepts)} concepts kept, {len(mentions)} mentions.")

    matcher = AliasMatcher({alias: key for key, concept in concepts.items() for alias in concept.aliases})
    aliases_of = {key: concept.aliases for key, concept in concepts.items()}
    links = build_links(chunks, matcher, aliases_of, min_chunks=min_chunks)
    log(f"[OSISS] {len(links)} links.")
    return Graph(concepts, mentions, links)
