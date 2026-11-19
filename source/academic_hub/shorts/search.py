"""Searching shorts from the pop-down bar above the feed.

Two kinds of search, told apart by the first character:

* ``#recursion`` is a tag search. It is exact: only shorts carrying that
  very tag (its label or slug, ignoring case) are found, so ``#rec`` does
  not find ``#recursion``. Several tags (``#ske #big-o``) find shorts
  carrying all of them. Newest first.
* Anything else searches titles, closest match first: the whole title,
  then titles starting with the words, then a word in the title starting
  with them, then the words anywhere, then every word as the start of some
  title word, and last near misses such as typos.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from django.db.models import Q, QuerySet

from .models import Short

# Longest query looked at; anything after this is ignored.
MAX_QUERY = 100
# Most results sent back; the page says when there were more.
MAX_RESULTS = 120
# How alike a title (or part of one) must be to count as a near miss.
FUZZY_MIN = 0.75

Kind = Literal["empty", "tag", "title"]
# (tier, closeness): higher is closer on both, tier first.
Rank = tuple[int, float]

EXACT, STARTS, WORD_STARTS, CONTAINS, ALL_WORDS, NEAR = 6, 5, 4, 3, 2, 1


@dataclass(frozen=True)
class Query:
    """What was typed, read as a tag search or a title search."""

    kind: Kind
    text: str = ""
    tags: tuple[str, ...] = ()


def normalise(text: str) -> str:
    """Lower case, one space between words, full-width letters folded."""
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def words_of(text: str) -> list[str]:
    """The words of already-normalised text."""
    return re.findall(r"\w+", text)


def parse(raw: str) -> Query:
    """Read the search box: ``#tag ...`` or title words."""
    text = normalise(raw[:MAX_QUERY])
    if not text:
        return Query("empty")
    if text.startswith("#"):
        names = (part.lstrip("#") for part in text.split())
        return Query("tag", tags=tuple(dict.fromkeys(n for n in names if n)))
    return Query("title", text=text)


def live_shorts() -> "QuerySet[Short]":
    """Every short that is not archived, newest first, with its tags."""
    return (
        Short.objects.filter(archived_at__isnull=True)
        .prefetch_related("tags")
        .order_by("-created_at", "-pk")
    )


def tagged(tags: tuple[str, ...]) -> list[Short]:
    """Shorts carrying every one of these exact tags, newest first."""
    if not tags:
        return []
    rows = live_shorts()
    for name in tags:
        rows = rows.filter(
            Q(tags__slug__iexact=name) | Q(tags__label__iexact=name)
        )
    return list(rows.distinct())


def _similarity(query: str, title: str) -> float:
    """How alike the query is to the title, or to its closest stretch."""
    best = SequenceMatcher(None, query, title).ratio()
    title_words = title.split()
    width = len(query.split())
    for start in range(max(0, len(title_words) - width + 1)):
        part = " ".join(title_words[start : start + width])
        best = max(best, SequenceMatcher(None, query, part).ratio())
    return best


def title_rank(query: str, title: str) -> Rank | None:
    """How close a title is to a (normalised) query; None if not at all."""
    name = normalise(title)
    if not query or not name:
        return None
    query_words = words_of(query)
    title_words = words_of(name)
    # Closest kind of match first; the first that holds sets the tier.
    checks: tuple[tuple[int, Callable[[], bool]], ...] = (
        (EXACT, lambda: name == query),
        (STARTS, lambda: name.startswith(query)),
        (
            WORD_STARTS,
            lambda: re.search(r"(?<!\w)" + re.escape(query), name) is not None,
        ),
        (CONTAINS, lambda: query in name),
        (
            ALL_WORDS,
            lambda: bool(query_words)
            and all(
                any(word.startswith(q) for word in title_words)
                for q in query_words
            ),
        ),
    )
    for tier, matches in checks:
        if matches():
            # Within a tier, the more of the title matched, the closer.
            return (tier, len(query) / len(name))
    near = _similarity(query, name)
    return (NEAR, near) if near >= FUZZY_MIN else None


def by_title(text: str) -> list[Short]:
    """Shorts whose titles match, closest first; newest first on a tie."""
    ranked = []
    for short in live_shorts():
        rank = title_rank(text, short.title)
        if rank is not None:
            ranked.append((rank, short))
    # sort() keeps equal ranks in their newest-first order.
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [short for _rank, short in ranked]


def search(query: Query) -> list[Short]:
    """Every short the query finds, in the order to show them."""
    if query.kind == "tag":
        return tagged(query.tags)
    if query.kind == "title":
        return by_title(query.text)
    return []
