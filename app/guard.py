"""Keeps the AI calls on-task: only contact searches reach the query model, and
nothing the model writes freely is shown back to the user."""

from __future__ import annotations

import re
import unicodedata

from .schemas import MAX_QUESTION_CHARS, CardExtraction, ContactIn, QuerySpec

OFF_TOPIC_MESSAGE = (
    "I can only search your saved business cards. "
    "Try something like \"emails of CEOs in Guangzhou\" or \"furniture suppliers\"."
)
NOT_A_CARD_MESSAGE = (
    "This doesn't look like a business card. Please photograph a business card, "
    "or fill in the details by hand."
)

# Characters and phrases that never appear in a genuine "find my contacts" question
# but are typical of prompt injection or of using the app as a general chatbot.
_BAD_CHARS = re.compile(r"[{}<>`\\|]")
_BLOCKED = re.compile(
    r"""
    https?://
    | \b(ignore|disregard|forget|override)\b .{0,30} \b(instruction|instructions|prompt|rules|above|previous|prior|earlier)\b
    | \bsystem\s*prompt\b | \byour\s+(instructions|prompt|rules|guidelines)\b
    | \byou\s+are\s+(now\s+)?(a|an|my)\b | \bact\s+as\b | \bpretend\b | \brole[\s-]?play\b
    | \bjailbreak\b | \bdeveloper\s+mode\b
    | \bwrite\s+(me\s+)?(a|an|some|the|my)\b
    | \b(poem|essay|story|stories|song|lyrics|joke|jokes|recipe|homework)\b
    | \b(translate|summari[sz]e|paraphrase|rewrite|proofread)\b
    | \b(python|javascript|typescript|html|regex)\b
    | 忽略.{0,10}(指令|提示|规则) | 系统提示 | 扮演 | 写一(首|篇|个)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_HAS_WORD = re.compile(r"[^\W\d_]", re.UNICODE)


def normalise_question(question: str) -> str:
    """NFKC-normalise (folds full-width/look-alike forms) and collapse whitespace."""
    q = unicodedata.normalize("NFKC", question or "")
    return " ".join(q.split())


def check_question(question: str) -> str | None:
    """Cheap local screen run before any AI call. Returns an error message, or None if OK."""
    raw = question or ""
    if any(unicodedata.category(ch) == "Cc" for ch in raw):
        return "Please ask a single-line question about your contacts."
    q = normalise_question(raw)
    if not q or not _HAS_WORD.search(q):
        return OFF_TOPIC_MESSAGE
    if len(q) > MAX_QUESTION_CHARS:
        return f"Please keep questions under {MAX_QUESTION_CHARS} characters."
    if _BAD_CHARS.search(q) or _BLOCKED.search(q):
        return OFF_TOPIC_MESSAGE
    return None


# ---- query spec ---------------------------------------------------------
_LIST_FIELDS = ("industries", "keywords", "title_keywords", "exclude_title_keywords", "names",
                "companies", "cities", "provinces", "countries", "tags")
MAX_TERMS = 30
MAX_TERM_CHARS = 60
MAX_TERM_WORDS = 6  # names and company names fit; sentences don't
SHOWN_TERM_CHARS = 24


def clip_spec(spec: QuerySpec) -> QuerySpec:
    """Bound every model-produced search term; drop anything sentence-like."""
    updates = {}
    for name in _LIST_FIELDS:
        terms: dict[str, str] = {}
        for t in getattr(spec, name):
            t = " ".join(str(t).split())[:MAX_TERM_CHARS]
            if t and len(t.split()) <= MAX_TERM_WORDS and t.lower() not in terms:
                terms[t.lower()] = t
        updates[name] = list(terms.values())[:MAX_TERMS]
    return spec.model_copy(update=updates)


def _short(term: str) -> str:
    return term if len(term) <= SHOWN_TERM_CHARS else term[: SHOWN_TERM_CHARS - 1].rstrip() + "…"


def _few(terms: list[str], n: int = 4) -> str:
    shown = ", ".join(_short(t) for t in terms[:n])
    return shown + ("…" if len(terms) > n else "")


def describe_spec(spec: QuerySpec) -> str:
    """A server-built summary of the filter, so no free-form model text reaches the user."""
    parts: list[str] = []
    topic = []
    if spec.industries:
        topic.append(f"industry {_few(spec.industries)}")
    if spec.keywords:
        topic.append(f"mentions {_few(spec.keywords)}")
    if topic:
        parts.append(" or ".join(topic))
    if spec.title_keywords:
        parts.append(f"title {_few(spec.title_keywords)}")
    if spec.exclude_title_keywords:
        parts.append(f"not {_few(spec.exclude_title_keywords)}")
    if spec.names:
        parts.append(f"named {_few(spec.names)}")
    if spec.companies:
        parts.append(f"at {_few(spec.companies)}")
    places = spec.cities + spec.provinces + spec.countries
    if places:
        parts.append(f"in {_few(places)}")
    if spec.tags:
        parts.append(f"tagged {_few(spec.tags)}")
    if spec.require_fields:
        parts.append(f"with {', '.join(dict.fromkeys(spec.require_fields))}")
    return ("Contacts: " + " · ".join(parts)) if parts else "All contacts"


# ---- card extraction ----------------------------------------------------
def clean_extraction(extraction: CardExtraction) -> dict:
    """Clip extracted fields to the same limits as a saved contact."""
    data = extraction.model_dump(exclude={"is_business_card"})
    clipped = ContactIn(**data).model_dump(exclude={"industry", "description", "notes", "tags",
                                                     "front_image", "back_image"})
    return clipped
