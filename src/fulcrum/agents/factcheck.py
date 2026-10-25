"""Deterministic Fact-Checker (AI_PROMPT_DESIGN §5). No LLM, no network.

Every piece of agent copy (explanation, persona variants, translations, recaps) passes through here.
A text fails if it contains:

1. **an unknown number**: a numeric token (`\\d+(?:[.,]\\d+)?`, which also covers `67'` and `71%`) that is not
   the value of a fact, a number inside a string fact or label (e.g. `"2-1"`, `"67'"`, "last 5 min"), the
   current score or the moment's minute. A comparison is *numeric* after normalising decimal commas, so
   `0,34` (translations) and `0.34` match, and `0.30` matches a fact stored as `0.3`. Sign is ignored
   (a `-22` delta may be written as "dropped 22 points"). Thousands groups ("11,240") are one number.
   Scorelines get no special allowance: "3-2" fails unless 3 and 2 are fact-pack numbers. This is stricter
   than §5.1, whose 0–3 scoreline allowance would let a wrong score through.
2. **an unknown fact key** in `Explanation.key_fact_keys`.
3. **an unknown name**: a real club or competition from `denylist.py`, or a team/player from the fictional
   league who is not in this match (all names in our league are unique, see tests/contracts). Case is
   ignored, except for club nicknames that are ordinary words ("wolves").
4. **banned language**: gambling words in English plus the copy's own language.
5. **a length violation**: headline > 90, body > 300, commentary_line > 160 chars or > 25 words.
6. **a misread number** (English copy): a pass count ("3-pass move", "1 completed pass") that is not the value
   of a pass fact; "a 0-1 lead" (a lead is written winner-first, so the first number must be larger) or
   "a 2-1 deficit"; and, on a single goal's copy, the new score called unchanged ("the score stayed 3-0").
7. **a data key in the copy**: a snake_case token such as `control_index_home` (say it in words).

`check_insight_copy` additionally verifies the Persona Editor returned exactly the four variants required.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from fulcrum.contracts import Explanation, Fact, InsightVariant, Persona

from .denylist import BANNED_TERMS, CASE_SENSITIVE_CLUB_WORDS, COMPETITION_NAMES, REAL_CLUB_NAMES
from .schemas import MomentContext

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Thousands groups: "11,240" in any language; "11.240" only outside English (de/es/pt/fr style).
THOUSANDS_COMMA_RE = re.compile(r"(?<![\d.,])[1-9]\d{0,2}(?:,\d{3})+(?![\d,]|\.\d)")
THOUSANDS_DOT_RE = re.compile(r"(?<![\d.,])[1-9]\d{0,2}(?:\.\d{3})+(?![\d.]|,\d)")

# Rule 6 (English). A scoreline is "3-0" / "3–0"; a pass count is "3-pass" / "3 passes" / "1 completed pass".
SCORELINE = r"(\d+)\s*[-–]\s*(\d+)"
PASS_COUNT_RE = re.compile(r"(?<![\w.,])(\d+)(?:-|\s+)(?:completed\s+|successful\s+)?pass(?:es)?\b", re.IGNORECASE)
LEAD_RE = re.compile(rf"\b{SCORELINE}\s+(lead|advantage|deficit)\b", re.IGNORECASE)
UNCHANGED = r"(?:stayed|stays|remained|remains|still|unchanged|held)"
# "stayed 3-0", "still at 3-0", "the score remained 3-0"; after the score only a verb ("3-0 stayed"), since
# "makes it 3-0 and they still need…" is fine.
UNCHANGED_RE = re.compile(
    rf"\b{UNCHANGED}\b(?:\W+\w+){{0,2}}?\W+{SCORELINE}|{SCORELINE}\W+(?:stayed|remained|was unchanged)\b",
    re.IGNORECASE,
)

# Rule 7: a snake_case data key ("control_index_home", "chaos_index") never belongs in copy for viewers.
FACT_KEY_RE = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w-])")

MAX_HEADLINE = 90
MAX_BODY = 300
MAX_COMMENTARY = 160
MAX_COMMENTARY_WORDS = 25
MAX_WHY = 400


def _word_pattern(terms: Iterable[str], *, ignore_case: bool, hyphen_is_word: bool = False) -> re.Pattern[str] | None:
    """One alternation regex matching any term as a whole word.

    With `hyphen_is_word`, a hyphen counts as part of a word, so "Bell" does not match inside
    "Nakamura-Bell" (needed for roster surnames); otherwise "Arsenal-style" matches "Arsenal".
    Inside multi-word terms any run of spaces, NBSPs or hyphens matches ("Premier-League").
    """
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return None
    body = "|".join(r"[\s -]+".join(re.escape(w) for w in re.split(r"[\s-]+", t)) for t in terms)
    edge = r"[\w-]" if hyphen_is_word else r"\w"
    return re.compile(rf"(?<!{edge})(?:{body})(?!{edge})", re.IGNORECASE if ignore_case else 0)


_COMPETITIONS_RE = _word_pattern(COMPETITION_NAMES, ignore_case=True)
_CLUBS_RE = _word_pattern([c for c in REAL_CLUB_NAMES if c not in CASE_SENSITIVE_CLUB_WORDS], ignore_case=True)
_CLUB_WORDS_RE = _word_pattern(CASE_SENSITIVE_CLUB_WORDS, ignore_case=False)
_BANNED_RE: dict[str, re.Pattern[str] | None] = {
    lang: _word_pattern(terms, ignore_case=True) for lang, terms in BANNED_TERMS.items()
}


def to_decimal(token: str) -> Decimal | None:
    """Parse a numeric token, accepting a decimal comma. Returns None for anything unparsable."""
    try:
        return abs(Decimal(token.replace(",", ".")))
    except InvalidOperation:
        return None


def numbers_in(text: str, language: str = "en") -> list[str]:
    """Numeric tokens in `text`. Thousands groups ("11,240"; "11.240" outside English) become one integer."""
    grouped: list[str] = []

    def take(m: re.Match[str]) -> str:
        grouped.append(re.sub(r"[.,]", "", m.group(0)))
        return " "

    text = THOUSANDS_COMMA_RE.sub(take, text)
    if base_language(language) != "en":
        text = THOUSANDS_DOT_RE.sub(take, text)
    return grouped + NUMBER_RE.findall(text)


def base_language(language: str | None) -> str:
    """'es-ES' → 'es'; None → 'en'."""
    return (language or "en").split("-")[0].split("_")[0].lower()


# --------------------------------------------------------------------------- #
# Issues and results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Issue:
    """One fact-check failure. `str(issue)` is the line fed back to the agent in the repair prompt."""

    code: str  # unknown_number | misread_number | unknown_fact_key | denylisted_name | foreign_name | banned_term | …
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
    pass_counts: frozenset[Decimal] = frozenset()  # values of pass facts: the only numbers "N passes" may use
    new_score: str | None = None  # a single goal's score_after (normalised "3-0"): never "stayed" at it
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
        pass_counts: set[Decimal] = set()
        befores: list[str] = []
        afters: list[str] = []

        def add_tokens(text: str) -> None:
            numbers.update(d for t in numbers_in(text) if (d := to_decimal(t)) is not None)

        for fact in facts:
            keys.add(fact.key)
            numeric = isinstance(fact.value, int | float) and not isinstance(fact.value, bool)
            if numeric and "pass" in fact.label.lower():
                pass_counts.add(abs(Decimal(str(fact.value))))
            if isinstance(fact.value, str) and "pass" in fact.value.lower():
                pass_counts.update(d for t in numbers_in(fact.value) if (d := to_decimal(t)) is not None)
            if fact.key == "score_before":
                befores.append(str(fact.value))
            elif fact.key == "score_after":
                afters.append(str(fact.value))
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
        # Only one goal's facts (a recap may rightly say a score "stayed 3-0 until the end").
        one_goal = len(befores) == 1 and len(afters) == 1 and befores != afters
        return cls(
            numbers=frozenset(numbers),
            keys=frozenset(keys),
            foreign_names=names,
            pass_counts=frozenset(pass_counts),
            new_score=_scoreline(afters[0]) if one_goal else None,
            _foreign_re=_word_pattern(names, ignore_case=True, hyphen_is_word=True),
        )

    # ----------------------------------------------------------------------------------------- #

    def check_text(self, text: str, where: str, *, language: str = "en") -> list[Issue]:
        """Rules 1, 3 and 4 on one string."""
        issues: list[Issue] = []
        for token in numbers_in(text, language):
            value = to_decimal(token)
            if value is None or value not in self.numbers:
                issues.append(Issue("unknown_number", where, f"number '{token}' is not in FACTS"))
        for m in FACT_KEY_RE.finditer(text):  # "control_index_home at 16" leaked into live copy
            issues.append(Issue("fact_key_in_text", where, f"'{m.group(0)}' is a data key: say it in words"))
        for pattern, code, what in (
            (_COMPETITIONS_RE, "denylisted_name", "real competition"),
            (_CLUBS_RE, "denylisted_name", "real club"),
            (_CLUB_WORDS_RE, "denylisted_name", "real club"),
            (self._foreign_re, "foreign_name", "name not in this match's ROSTER"),
        ):
            if pattern is not None:
                for m in pattern.finditer(text):
                    issues.append(Issue(code, where, f"'{m.group(0)}' is a {what}"))
        if base_language(language) == "en":
            issues += self._misread(text, where)
        for lang in dict.fromkeys(("en", base_language(language))):
            pattern = _BANNED_RE.get(lang)
            if pattern is not None:
                for m in pattern.finditer(text):
                    issues.append(Issue("banned_term", where, f"banned betting/market term '{m.group(0)}'"))
        return issues

    def _misread(self, text: str, where: str) -> list[Issue]:
        """Rule 6: numbers that are in FACTS but used as the wrong thing (English copy only)."""
        issues: list[Issue] = []
        for m in PASS_COUNT_RE.finditer(text):
            if (d := to_decimal(m.group(1))) is not None and d not in self.pass_counts:
                detail = f"'{m.group(0)}': {m.group(1)} is not a pass count in FACTS"
                issues.append(Issue("misread_number", where, detail))
        for m in LEAD_RE.finditer(text):
            a, b, word = int(m.group(1)), int(m.group(2)), m.group(3).lower()
            if (word == "deficit" and a > b) or (word != "deficit" and a <= b):
                detail = f"'{m.group(0)}': write a {word} with the leading side's goals first"
                issues.append(Issue("misread_number", where, detail))
        if self.new_score:
            for m in UNCHANGED_RE.finditer(text):
                nums = [g for g in m.groups() if g is not None]
                if f"{nums[0]}-{nums[1]}" == self.new_score:
                    detail = f"'{m.group(0)}': this goal made it {self.new_score}; it did not stay"
                    issues.append(Issue("misread_number", where, detail))
        return issues


def fix_lead_order(text: str) -> str:
    """English copy: "a 0-1 lead" -> "a 1-0 lead", "a 2-1 deficit" -> "a 1-2 deficit" (same numbers, right way round).

    The fact pack gives scores home-first ("Score (Harlow Cross first) 0-1"), so the model often writes an away
    lead as "a 0-1 lead" and, told to quote numbers exactly, keeps doing it in the repair. The convention is
    unambiguous, so it is fixed here instead of costing a repair or a fallback. A "lead" at level scores is not
    fixable and is still flagged by rule 6.
    """

    def swap(m: re.Match[str]) -> str:
        a, b, word = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        wrong = (word == "deficit" and a > b) or (word != "deficit" and a < b)
        if not wrong:
            return m.group(0)
        sep, tail = m.string[m.end(1) : m.start(2)], m.string[m.end(2) : m.end(0)]
        return f"{m.group(2)}{sep}{m.group(1)}{tail}"

    return LEAD_RE.sub(swap, text)


def fix_copy(
    explanation: Explanation | None, variants: Sequence[InsightVariant]
) -> tuple[Explanation | None, list[InsightVariant]]:
    """`fix_lead_order` on every English text field of an explanation and its variants."""
    if explanation is not None:
        explanation = explanation.model_copy(
            update={
                "headline": fix_lead_order(explanation.headline),
                "why_it_matters": fix_lead_order(explanation.why_it_matters),
            }
        )
    fixed = [
        v.model_copy(update={k: fix_lead_order(getattr(v, k)) for k in ("headline", "body", "commentary_line")})
        if base_language(v.language) == "en"
        else v
        for v in variants
    ]
    return explanation, fixed


def _scoreline(text: str) -> str | None:
    """'3 – 0' → '3-0'; None if `text` has no scoreline."""
    m = re.search(SCORELINE, text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def context_sheet(ctx: MomentContext) -> FactSheet:
    """The (cached) fact sheet for one workflow run's moment."""
    if "sheet" not in ctx._cache:
        ctx._cache["sheet"] = FactSheet.build(
            ctx.moment.facts, score=ctx.score, clock_ms=ctx.moment.clock_ms, foreign_names=ctx.foreign_names
        )
    return ctx._cache["sheet"]


def _length(text: str, limit: int, where: str) -> list[Issue]:
    if len(text) > limit:
        return [Issue("too_long", where, f"{len(text)} chars, max {limit}")]
    return []


# Sentence-final characters (incl. closing quotes/brackets). The services enforce the contract's max lengths
# while the model writes, so an over-long body comes back cut off mid-word; that must be caught and repaired.
SENTENCE_END = (".", "!", "?", "…", '"', "'", "”", "’", ")", "»")


def cut_off(text: str, where: str) -> list[Issue]:
    """Flag copy that does not end a sentence: it was cut off at a max length while being generated."""
    if text.strip() and not text.rstrip().endswith(SENTENCE_END):
        return [Issue("cut_off", where, "ends mid-sentence (cut off at the length limit); write it shorter")]
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
    issues += cut_off(expl.why_it_matters, "explanation.why_it_matters")
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
    issues += cut_off(v.body, f"{where}.body")
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
