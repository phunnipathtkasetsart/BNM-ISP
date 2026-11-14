"""The order of the Shorts feed: clips that fit the viewer come first.

Three things make a short fit someone:

* Their courses. The words of each course's name and topic titles are
  compared with the short's tags (+2 for each tag the course names itself,
  +1 for each tag a related subject brings in, from SUBJECTS below).
* Their likes. Each tag on the short adds how many of the viewer's liked
  shorts carry it. A liked short is not boosted by its own like, so it is
  the other shorts like it that rise.
* Their programme. A short tagged with the viewer's programme (#ske or
  #cpe, their account's department) gets PROGRAMME_POINTS.

The course and like scores are each capped at MAX_SCORE, and the weight is
1 + 2 x (course + like + programme): 1 for no match, 17 at most. When no
short in the feed fits the viewer at all, the feed stays newest first.

The order is a weighted random draw without replacement: each place goes
to one of the remaining shorts with a chance in proportion to its weight.
Every short still appears once per round; heavy ones just tend to come
early. The draw is seeded, so the same round seed always gives the same
order and the feed can be paged through without repeats or gaps.
"""

import random
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from django.db.models import Q

from accounts.models import User as Account
from course.models import Course, Topic

from .forms import tag_slug
from .models import Short, ShortLike

MAX_SCORE = 3
OWN_WORD_POINTS = 2
RELATED_POINTS = 1
PROGRAMME_POINTS = 2

# Words that say nothing about the subject of a course.
STOPWORDS = frozenset(
    "a an and basic basics course for fundamental fundamentals i ii iii "
    "in intro introduction iv lab laboratory of on principles section "
    "seminar special the to topics with".split()
)


@dataclass(frozen=True)
class Subject:
    """Course words that name a subject, and the tags that go with it."""

    words: frozenset[str]
    tags: frozenset[str]


def _subject(words: str, tags: str) -> Subject:
    return Subject(frozenset(words.split()), frozenset(tags.split()))


# A course name rarely says which tags its students want: "Individual
# Software Process" never says "python". Add a line here to cover a new
# kind of course; words and tags are written the way tag_slug() writes
# them (lower case, hyphens between words).
SUBJECTS = (
    _subject(
        "math maths mathematics calculus algebra geometry trigonometry",
        "math mathematics calculus algebra geometry trigonometry "
        "statistics probability",
    ),
    _subject(
        "statistics probability",
        "statistics probability data math",
    ),
    _subject(
        "software programming program coding computer isp ske cpe "
        "python java",
        "program programming code coding python java javascript git "
        "testing debugging",
    ),
    _subject(
        "algorithm algorithms data-structures structures",
        "algorithm algorithms data-structures recursion sorting big-o "
        "complexity",
    ),
    _subject(
        "database databases sql",
        "database databases sql postgresql data",
    ),
    _subject(
        "web internet",
        "web html css javascript django http",
    ),
    _subject(
        "network networks networking",
        "network networking tcp ip internet",
    ),
    _subject(
        "ai machine-learning intelligence learning",
        "ai machine-learning python data neural-network",
    ),
    _subject(
        "security cybersecurity",
        "security cybersecurity cryptography",
    ),
    _subject(
        "physics mechanics",
        "physics mechanics calculus",
    ),
)


@dataclass(frozen=True)
class Interests:
    """What one viewer's courses and likes say they want to see."""

    course_words: frozenset[str] = frozenset()
    related_tags: frozenset[str] = frozenset()
    liked_tags: Mapping[str, int] = field(default_factory=dict)
    liked_shorts: frozenset[int] = frozenset()
    programme_tags: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        """No courses, likes or programme: nothing to rank by."""
        return not (
            self.course_words
            or self.related_tags
            or self.liked_tags
            or self.programme_tags
        )


# --- what the viewer's courses are about -----------------------------------


def course_texts(user: Account) -> list[str]:
    """Names and topic titles of the user's live courses.

    Students' enrolled courses, TAs' assisted ones and lecturers' own. The
    Department oversees every course, which would make every short
    "relevant", so its feed is ranked by likes alone.
    """
    if user.is_superuser:
        return []
    courses = Course.objects.filter(
        Q(enrollments__student=user) | Q(ta_links__user=user) | Q(owner=user),
        archived_at__isnull=True,
    ).distinct()
    names = list(courses.values_list("name", flat=True))
    topics = Topic.objects.filter(course__in=courses).values_list(
        "title", flat=True
    )
    return names + list(topics)


def words_of(texts: Iterable[str]) -> frozenset[str]:
    """Tag-style words and two-word phrases, without filler words.

    "Data Structures and Algorithms" -> data, structures, algorithms and
    data-structures. A filler word ends a phrase, so "structures-algorithms"
    is not made. Phrases let a course match tags like #data-structures.
    """
    found: set[str] = set()
    for text in texts:
        run: list[str] = []
        for word in tag_slug(text).split("-") + [""]:  # "" ends the last run
            if word and word not in STOPWORDS:
                run.append(word)
                continue
            found.update(run)
            found.update(f"{a}-{b}" for a, b in zip(run, run[1:]))
            run = []
    return frozenset(found)


def related_tags(words: frozenset[str]) -> frozenset[str]:
    """Tags of every subject the words mention, minus the words themselves."""
    tags: set[str] = set()
    for subject in SUBJECTS:
        if subject.words & words:
            tags.update(subject.tags)
    return frozenset(tags - words)


# --- what the viewer has liked ---------------------------------------------


def liked(user: Account) -> tuple[Counter[str], frozenset[int]]:
    """How many liked shorts carry each tag, and which shorts were liked."""
    rows = ShortLike.objects.filter(
        user=user, short__archived_at__isnull=True
    ).values_list("short_id", "short__tags__slug")
    counts: Counter[str] = Counter()
    shorts: set[int] = set()
    for short_id, slug in rows:
        shorts.add(short_id)
        if slug:
            counts[slug] += 1
    return counts, frozenset(shorts)


def programme_tags(user: Account) -> frozenset[str]:
    """Tags naming the user's programme: "ske" and its full name's slug."""
    code = user.department or ""
    label = dict(Account.Department.choices).get(code, "")
    return frozenset(tag for tag in (code, tag_slug(label)) if tag)


def interests_for(user: Account) -> Interests:
    """Everything the ranking needs to know about one viewer."""
    words = words_of(course_texts(user))
    liked_tags, liked_shorts = liked(user)
    return Interests(
        course_words=words,
        related_tags=related_tags(words),
        liked_tags=liked_tags,
        liked_shorts=liked_shorts,
        programme_tags=programme_tags(user),
    )


# --- scoring and ordering --------------------------------------------------


def course_score(tags: Iterable[str], interests: Interests) -> int:
    """+2 per tag a course names, +1 per related tag; at most MAX_SCORE."""
    points = 0
    for tag in tags:
        if tag in interests.course_words:
            points += OWN_WORD_POINTS
        elif tag in interests.related_tags:
            points += RELATED_POINTS
    return min(points, MAX_SCORE)


def like_score(
    short_id: int, tags: Iterable[str], interests: Interests
) -> int:
    """Liked shorts sharing each tag; 0 for a short already liked."""
    if short_id in interests.liked_shorts:
        return 0
    points = sum(interests.liked_tags.get(tag, 0) for tag in tags)
    return min(points, MAX_SCORE)


def programme_score(tags: Iterable[str], interests: Interests) -> int:
    """PROGRAMME_POINTS if the short is tagged with the viewer's programme."""
    if interests.programme_tags.isdisjoint(tags):
        return 0
    return PROGRAMME_POINTS


def weight(short_id: int, tags: Iterable[str], interests: Interests) -> int:
    """1 for a short that fits nothing, up to 17 for one that fits all."""
    tags = list(tags)
    score = (
        course_score(tags, interests)
        + like_score(short_id, tags, interests)
        + programme_score(tags, interests)
    )
    return 1 + 2 * score


def weighted_order(weights: Mapping[int, int], seed: int) -> list[int]:
    """The keys in a seeded, weighted random order.

    Each short draws key = u ** (1 / weight) for a uniform u, and the
    largest key goes first (Efraimidis and Spirakis, 2006). That is the
    same as drawing one short at a time, in proportion to weight, from
    those not yet drawn.
    """
    # Not for security: the order only has to look shuffled.
    draw = random.Random(seed)  # nosec B311
    keys = {pk: draw.random() ** (1 / w) for pk, w in weights.items()}
    return sorted(weights, key=lambda pk: (-keys[pk], pk))


def tags_by_short() -> dict[int, list[str]]:
    """Every live short's tag slugs, newest short first."""
    rows = (
        Short.objects.filter(archived_at__isnull=True)
        .order_by("-created_at", "-pk")
        .values_list("pk", "tags__slug")
    )
    tags: dict[int, list[str]] = {}
    for pk, slug in rows:
        tags.setdefault(pk, [])
        if slug:
            tags[pk].append(slug)
    return tags


def feed_order(user: Account, seed: int) -> list[int]:
    """Ids of all live shorts in the order this round shows them.

    When nothing in the feed fits the viewer (every weight is 1), there
    is nothing to rank by and the feed stays newest first.
    """
    shorts = tags_by_short()
    interests = interests_for(user)
    weights = {pk: weight(pk, tags, interests) for pk, tags in shorts.items()}
    if interests.is_empty or set(weights.values()) <= {1}:
        return list(shorts)
    return weighted_order(weights, seed)
