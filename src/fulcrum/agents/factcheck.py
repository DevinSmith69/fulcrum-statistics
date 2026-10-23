"""Deterministic Fact-Checker (AI_PROMPT_DESIGN §5). No LLM, no network.

Every piece of agent copy (explanation, persona variants, translations, recaps) passes through here.
A text fails if it contains:

1. **an unknown number**: a numeric token (`\\d+(?:[.,]\\d+)?`, which also covers `67'` and `71%`) that is not
   the value of a fact, a number inside a string fact or label (e.g. `"2-1"`, `"67'"`, "last 5 min"), the
   current score or the moment's minute. A comparison is *numeric* after normalising decimal commas, so
   `0,34` (translations) and `0.34` match, and `0.30` matches a fact stored as `0.3`. Sign is ignored
   (a `-22` delta may be written as "dropped 22 points"). Small scorelines (`1-0`, `2–1`, digits 0–3) are
   allowed as phrases, per §5.1.
2. **an unknown fact key** in `Explanation.key_fact_keys`.
3. **an unknown name**: a real club or competition from `denylist.py`, or a team/player from the fictional
   league who is not in this match (all names in our league are unique, see tests/contracts).
4. **banned language**: gambling words in English plus the copy's own language.
5. **a length violation**: headline > 90, body > 300, commentary_line > 160 chars or > 25 words.

`check_insight_copy` additionally verifies the Persona Editor returned exactly the four variants required.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from fulcrum.contracts import Explanation, Fact, InsightVariant, Persona

from .denylist import BANNED_TERMS, COMPETITION_NAMES, REAL_CLUB_NAMES

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# A scoreline phrase like "1-0", "2–1" or "3:3" (digits 0–3 only, not part of a longer number).
SCORE_PHRASE_RE = re.compile(r"(?<![\d.,])[0-3]\s?[-–—:]\s?[0-3](?!\d|[.,]\d|\s?[-–—:]\s?\d)")

MAX_HEADLINE = 90
MAX_BODY = 300
MAX_COMMENTARY = 160
MAX_COMMENTARY_WORDS = 25
MAX_WHY = 400


def _word_pattern(terms: Iterable[str], *, ignore_case: bool, hyphen_is_word: bool = False) -> re.Pattern[str] | None:
    """One alternation regex matching any term as a whole word.

    With `hyphen_is_word`, a hyphen counts as part of a word, so "Bell" does not match inside
    "Nakamura-Bell" (needed for roster surnames); otherwise "Arsenal-style" matches "Arsenal".
    """
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return None
    body = "|".join(re.escape(t) for t in terms)
    edge = r"[\w-]" if hyphen_is_word else r"\w"
    return re.compile(rf"(?<!{edge})(?:{body})(?!{edge})", re.IGNORECASE if ignore_case else 0)


_COMPETITIONS_RE = _word_pattern(COMPETITION_NAMES, ignore_case=True)
_CLUBS_RE = _word_pattern(REAL_CLUB_NAMES, ignore_case=False)
_BANNED_RE: dict[str, re.Pattern[str] | None] = {
    lang: _word_pattern(terms, ignore_case=True) for lang, terms in BANNED_TERMS.items()
}


def to_decimal(token: str) -> Decimal | None:
    """Parse a numeric token, accepting a decimal comma. Returns None for anything unparsable."""
    try:
        return abs(Decimal(token.replace(",", ".")))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[str]:
    """All numeric tokens in `text`, as written."""
    return NUMBER_RE.findall(text)


def base_language(language: str | None) -> str:
    """'es-ES' → 'es'; None → 'en'."""
    return (language or "en").split("-")[0].split("_")[0].lower()


# --------------------------------------------------------------------------- #
# Issues and results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Issue:
    """One fact-check failure. `str(issue)` is the line fed back to the agent in the repair prompt."""

    code: str  # unknown_number | unknown_fact_key | denylisted_name | foreign_name | banned_term | too_long | variants
    where: str
    detail: str

    def __str__(self) -> str:
        return f"{self.where}: {self.detail}"


@dataclass
class CheckResult:
    """Outcome of checking a set of copy."""

    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def explanation_failed(self) -> bool:
        """True if any issue is in the Analyst's explanation (so the Analyst must redo it)."""
        return any(i.where.startswith("explanation") for i in self.issues)

    @property
    def messages(self) -> list[str]:
        return [str(i) for i in self.issues]


# --------------------------------------------------------------------------- #
# The fact sheet: what a given piece of copy is allowed to say
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FactSheet:
    """Allowed numbers and fact keys, plus forbidden names, for one moment (or a recap's moments)."""

    numbers: frozenset[Decimal]
    keys: frozenset[str]
    foreign_names: tuple[str, ...] = ()
    _foreign_re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def build(
        cls,
        facts: Iterable[Fact],
        *,
        score: str | None = None,
        clock_ms: int | None = None,
        foreign_names: Sequence[str] = (),
        extra_numbers: Iterable[str] = (),
    ) -> FactSheet:
        """Collect every number the copy may use from `facts`, the score and the moment minute."""
        numbers: set[Decimal] = set()
        keys: set[str] = set()

        def add_tokens(text: str) -> None:
            numbers.update(d for t in numbers_in(text) if (d := to_decimal(t)) is not None)

        for fact in facts:
            keys.add(fact.key)
            add_tokens(fact.label)
            if isinstance(fact.value, str):
                add_tokens(fact.value)
            elif not isinstance(fact.value, bool):
                numbers.add(abs(Decimal(str(fact.value))))
        if score:
            add_tokens(score)
        if clock_ms is not None:
            # engines differ on "minute being played" vs "completed minutes": accept both
            numbers.update({Decimal(clock_ms // 60_000), Decimal(clock_ms // 60_000 + 1)})
        for token in extra_numbers:
            add_tokens(token)
        names = tuple(foreign_names)
        return cls(
            numbers=frozenset(numbers),
            keys=frozenset(keys),
            foreign_names=names,
            _foreign_re=_word_pattern(names, ignore_case=False, hyphen_is_word=True),
        )

    # ----------------------------------------------------------------------------------------- #

    def check_text(self, text: str, where: str, *, language: str = "en") -> list[Issue]:
        """Rules 1, 3 and 4 on one string."""
        issues: list[Issue] = []
        stripped = SCORE_PHRASE_RE.sub(" ", text)
        for token in numbers_in(stripped):
            value = to_decimal(token)
            if value is None or value not in self.numbers:
                issues.append(Issue("unknown_number", where, f"number '{token}' is not in FACTS"))
        for pattern, code, what in (
            (_COMPETITIONS_RE, "denylisted_name", "real competition"),
            (_CLUBS_RE, "denylisted_name", "real club"),
            (self._foreign_re, "foreign_name", "name not in this match's ROSTER"),
        ):
            if pattern is not None:
                for m in pattern.finditer(text):
                    issues.append(Issue(code, where, f"'{m.group(0)}' is a {what}"))
        for lang in dict.fromkeys(("en", base_language(language))):
            pattern = _BANNED_RE.get(lang)
            if pattern is not None:
                for m in pattern.finditer(text):
                    issues.append(Issue("banned_term", where, f"banned betting/market term '{m.group(0)}'"))
        return issues


def _length(text: str, limit: int, where: str) -> list[Issue]:
    if len(text) > limit:
        return [Issue("too_long", where, f"{len(text)} chars, max {limit}")]
    return []


def variant_label(v: InsightVariant) -> str:
    """Stable label used in issue locations, e.g. `variants[club_fan:NBA]`."""
    suffix = v.perspective_team_id or v.perspective_player_id
    return f"variants[{v.persona}{':' + suffix if suffix else ''}]"


def check_explanation(expl: Explanation, sheet: FactSheet) -> list[Issue]:
    """Rules 1–5 on the Moment Analyst's explanation."""
    issues = sheet.check_text(expl.headline, "explanation.headline")
    issues += sheet.check_text(expl.why_it_matters, "explanation.why_it_matters")
    issues += _length(expl.headline, MAX_HEADLINE, "explanation.headline")
    issues += _length(expl.why_it_matters, MAX_WHY, "explanation.why_it_matters")
    for key in expl.key_fact_keys:
        if key not in sheet.keys:
            issues.append(Issue("unknown_fact_key", "explanation.key_fact_keys", f"'{key}' is not a FACT key"))
    return issues


def check_variant(v: InsightVariant, sheet: FactSheet, where: str | None = None) -> list[Issue]:
    """Rules 1, 3, 4, 5 on one persona variant (any language)."""
    where = where or variant_label(v)
    issues: list[Issue] = []
    for attr, limit in (("headline", MAX_HEADLINE), ("body", MAX_BODY), ("commentary_line", MAX_COMMENTARY)):
        text = getattr(v, attr)
        issues += sheet.check_text(text, f"{where}.{attr}", language=v.language)
        issues += _length(text, limit, f"{where}.{attr}")
    words = len(v.commentary_line.split())
    if words > MAX_COMMENTARY_WORDS:
        issues.append(Issue("too_long", f"{where}.commentary_line", f"{words} words, max {MAX_COMMENTARY_WORDS}"))
    return issues


def required_variants(home_id: str, away_id: str) -> list[tuple[str, str | None]]:
    """The (persona, perspective_team_id) pairs the Persona Editor must return, in canonical order."""
    return [
        (Persona.ANALYST.value, None),
        (Persona.CASUAL.value, None),
        (Persona.CLUB_FAN.value, home_id),
        (Persona.CLUB_FAN.value, away_id),
    ]


def check_variant_set(
    variants: Sequence[InsightVariant], home_id: str, away_id: str, *, language: str = "en"
) -> list[Issue]:
    """The editor must return exactly analyst, casual, club_fan(home), club_fan(away) in `language`."""
    issues: list[Issue] = []
    got = [(str(v.persona), v.perspective_team_id) for v in variants]
    for pair in required_variants(home_id, away_id):
        n = got.count(pair)
        label = pair[0] + (f" for {pair[1]}" if pair[1] else "")
        if n == 0:
            issues.append(Issue("variants", "variants", f"missing the {label} variant"))
        elif n > 1:
            issues.append(Issue("variants", "variants", f"{n} {label} variants, expected exactly 1"))
    for pair in got:
        if pair not in required_variants(home_id, away_id):
            issues.append(Issue("variants", "variants", f"unexpected variant {pair[0]} / {pair[1]}"))
    for v in variants:
        if v.language != language:
            issues.append(Issue("variants", variant_label(v), f"language '{v.language}', expected '{language}'"))
    return issues


def check_insight_copy(
    explanation: Explanation | None,
    variants: Sequence[InsightVariant],
    sheet: FactSheet,
    *,
    home_id: str,
    away_id: str,
    language: str = "en",
) -> CheckResult:
    """Full check of one moment's copy: explanation (if given), the variant set, and every variant."""
    result = CheckResult()
    if explanation is not None:
        result.issues += check_explanation(explanation, sheet)
    result.issues += check_variant_set(variants, home_id, away_id, language=language)
    for v in variants:
        result.issues += check_variant(v, sheet)
    return result


def check_variants(variants: Sequence[InsightVariant], sheet: FactSheet) -> CheckResult:
    """Check translated (or otherwise standalone) variants — rules 1, 3, 4, 5."""
    result = CheckResult()
    for v in variants:
        result.issues += check_variant(v, sheet)
    return result
