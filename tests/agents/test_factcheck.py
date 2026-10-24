import pytest

from fulcrum.agents.factcheck import (
    FactSheet,
    base_language,
    check_explanation,
    check_insight_copy,
    check_variant,
    check_variant_set,
    numbers_in,
    to_decimal,
)
from fulcrum.contracts import Explanation, InsightVariant, MomentType
from tests.agents.factories import AWAY, HOME, F, make_context, make_moment


@pytest.fixture
def sheet() -> FactSheet:
    ctx = make_context(make_moment(MomentType.GOAL))
    return FactSheet.build(
        ctx.moment.facts, score=ctx.score, clock_ms=ctx.moment.clock_ms, foreign_names=ctx.foreign_names
    )


def codes(issues) -> list[str]:
    return [i.code for i in issues]


def variant(persona="analyst", team=None, language="en", headline="H", body="B.", line="L") -> InsightVariant:
    return InsightVariant(
        persona=persona, perspective_team_id=team, language=language, headline=headline, body=body, commentary_line=line
    )


# --------------------------------------------------------------------------- numbers


def test_number_tokens_cover_minutes_and_percent():
    assert numbers_in("Brennick scores on 67' and lifts them to 71%, 0.34 xG, 0,34 xG") == [
        "67",
        "71",
        "0.34",
        "0,34",
    ]


def test_to_decimal_accepts_decimal_comma_and_ignores_sign():
    assert to_decimal("0,34") == to_decimal("0.34")
    assert to_decimal("0.30") == to_decimal("0.3")


@pytest.mark.parametrize(
    "text",
    [
        "Brennick scores with a 0.34 xG chance",
        "win probability jumps from 48% to 71%",
        "a swing of 23 points",
        "struck at 98 km/h from 14.2 m",
        "on 67' it is 1-0",
        "the assist rated 7.8 for difficulty",
        "Brennick (67') makes it 1-0",
        "they now lead 1–0",
    ],
)
def test_numbers_from_facts_pass(sheet, text):
    assert sheet.check_text(text, "t") == []


@pytest.mark.parametrize(
    "text, bad",
    [
        ("a 0.52 xG chance", "0.52"),
        ("win probability now 72%", "72"),
        ("his 12th goal of the season", "12"),
        ("struck at 99 km/h", "99"),
        ("shirt number 9 strikes", "9"),
    ],
)
def test_invented_numbers_fail(sheet, text, bad):
    issues = sheet.check_text(text, "body")
    assert codes(issues) == ["unknown_number"]
    assert f"'{bad}'" in str(issues[0]) and str(issues[0]).startswith("body:")


@pytest.mark.parametrize(
    "text",
    [
        "Brennick marca con un disparo de 0,34 xG",  # es
        "la probabilité passe de 48 % à 71 %",  # fr, spaced percent
        "chute a 98 km/h de 14,2 m",  # pt
        "Schussgeschwindigkeit 98 km/h, Distanz 14,2 m",  # de
    ],
)
def test_translation_decimal_commas_pass(sheet, text):
    assert sheet.check_text(text, "t", language=text[:2]) == []


def test_translation_with_altered_decimal_fails(sheet):
    issues = sheet.check_text("un disparo de 0,43 xG", "t", language="es")
    assert codes(issues) == ["unknown_number"]


def test_trailing_zero_matches_numerically():
    s = FactSheet.build([F("xg", 0.3, "xG"), F("progressive_m", 27.0, "m")])
    assert s.check_text("0.30 xG and 27 m gained, 27.0 m", "t") == []


def test_negative_delta_quoted_as_magnitude():
    s = FactSheet.build([F("wp_home_delta", -12, "%")])
    assert s.check_text("their chances drop 12 points", "t") == []
    assert s.check_text("their chances drop -12 points", "t") == []


def test_numbers_inside_string_facts_and_labels_are_allowed():
    s = FactSheet.build([F("milestone", "50th completed pass"), F("shots_last_5", 3, label="Shots in last 5 min")])
    assert s.check_text("his 50th completed pass; 3 shots in the last 5 minutes", "t") == []


def test_score_and_minute_from_context_are_allowed():
    s = FactSheet.build([], score="2-1", clock_ms=66 * 60_000 + 10_000)
    assert s.check_text("2-1 up in the 67th minute", "t") == []
    assert s.check_text("2 goals to 1 on 66'", "t") == []
    assert codes(s.check_text("4 goals", "t")) == ["unknown_number"]


def test_scorelines_need_fact_pack_digits():
    s = FactSheet.build([], score="1-0")
    assert s.check_text("from 0-0 to 1–0", "t") == []
    assert [i.code for i in s.check_text("Northbridge lead 3-2", "t")] == ["unknown_number", "unknown_number"]
    assert "unknown_number" in codes(s.check_text("in a 4-3-3", "t"))
    assert codes(s.check_text("a 71-29 split", "t")) == ["unknown_number", "unknown_number"]


def test_thousands_separators():
    s = FactSheet.build([F("distance_m", 11240, "m"), F("value", 1)])
    assert s.check_text("he has covered 11,240 m", "t") == []
    assert s.check_text("11.240 m recorridos", "t", language="es") == []
    assert "unknown_number" in codes(s.check_text("1.000 Pässe", "t", language="de"))
    assert s.check_text("0,34 xG", "t", language="es") != []  # still a decimal, and not a fact here


def test_numbers_in_labels_are_allowed(sheet):
    # the goal facts include the label "Assist pass difficulty (0-10)", so 10 is a fact-pack number
    assert sheet.check_text("7.8 out of 10", "t") == []


def test_player_ids_are_not_numbers_we_allow(sheet):
    assert "unknown_number" in codes(sheet.check_text("NBA-17 comes on", "t"))


# --------------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "text, hit",
    [
        ("Brennick scores like a Premier League striker", "Premier League"),
        ("a finish worthy of the premier league", "premier league"),
        ("Arsenal-style passing", "Arsenal"),
        ("shades of Manchester United", "Manchester United"),
        ("Spurs would have loved that", "Spurs"),
        ("the Gunners of Northbridge", "Gunners"),
        ("like an EPL winger", "EPL"),
        ("ARSENAL take note", "ARSENAL"),
        ("like arsenal did", "arsenal"),
        ("a Premier-League style finish", "Premier-League"),
        ("a Premier League finish", "Premier League"),
        ("Aston  Villa would", "Aston  Villa"),
    ],
)
def test_denylisted_real_names_fail(sheet, text, hit):
    issues = sheet.check_text(text, "t")
    assert "denylisted_name" in codes(issues)
    assert any(hit in str(i) for i in issues)


def test_denylist_in_translation(sheet):
    assert "denylisted_name" in codes(sheet.check_text("como un delantero del Liverpool", "t", language="es"))


@pytest.mark.parametrize(
    "text", ["Northbridge Athletic lead", "Riverside Rovers respond", "Marsh-style United fans", "the wolves circle"]
)
def test_fictional_and_generic_names_pass(sheet, text):
    assert sheet.check_text(text, "t") == []


def test_league_names_outside_this_match_fail(sheet):
    issues = sheet.check_text("Ullmann would have scored that for Harlow Cross", "t")
    assert codes(issues) == ["foreign_name", "foreign_name"]


@pytest.mark.parametrize("text", ["harlow are next", "a Marsh Dock United fan", "ULLMANN scores", "Harlow Cross"])
def test_foreign_names_any_case_and_distinctive_first_word(sheet, text):
    assert "foreign_name" in codes(sheet.check_text(text, "t"))


def test_own_team_names_any_case_pass(sheet):
    assert sheet.check_text("riverside rovers respond; NORTHBRIDGE lead", "t") == []


def test_roster_names_in_match_pass_including_hyphenated(sheet):
    assert sheet.check_text("Fenwick-Shaw finds Nakamura-Bell's weak side; Brennick finishes", "t") == []


def test_foreign_name_not_matched_inside_hyphenated_name():
    s = FactSheet.build([], foreign_names=["Bell", "Shaw"])
    assert s.check_text("Nakamura-Bell saves from Fenwick-Shaw", "t") == []
    assert [i.code for i in s.check_text("Bell saves", "t")] == ["foreign_name"]


# --------------------------------------------------------------------------- banned language


@pytest.mark.parametrize(
    "text, language",
    [
        ("the odds have shifted", "en"),
        ("you would not bet against them now", "en"),
        ("the betting public", "en"),
        ("the market has moved", "en"),
        ("bookmakers are nervous", "en"),
        ("las apuestas cambian", "es"),
        ("les paris sportifs s'affolent", "fr"),
        ("as apostas sobem", "pt"),
        ("die Wetten laufen", "de"),
        ("the odds have shifted", "es"),  # English betting words are banned in every language
    ],
)
def test_banned_terms_fail(sheet, text, language):
    assert "banned_term" in codes(sheet.check_text(text, "t", language=language))


@pytest.mark.parametrize(
    "text, language",
    [
        ("a better ball between the lines", "en"),
        ("a marked man", "en"),
        ("pricey? no: priceless", "en"),
        ("Seine Quote liegt bei 71 %", "de"),
        ("sa cote de popularité", "fr"),
    ],
)
def test_banned_terms_need_whole_words(sheet, text, language):
    assert sheet.check_text(text, "t", language=language) == []


def test_base_language():
    assert base_language("es-ES") == "es" and base_language(None) == "en" and base_language("pt_BR") == "pt"


# --------------------------------------------------------------------------- structure & length


def test_unknown_fact_key_fails(sheet):
    expl = Explanation(
        headline="Brennick strikes", why_it_matters="It matters.", tactical_tag="turning_point", key_fact_keys=["x"]
    )
    issues = check_explanation(expl, sheet)
    assert codes(issues) == ["unknown_fact_key"] and "'x'" in str(issues[0])


def test_commentary_word_limit(sheet):
    v = variant(line=" ".join(["go"] * 26))
    assert codes(check_variant(v, sheet)) == ["too_long"]


def test_length_limits_on_constructed_models(sheet):
    v = InsightVariant.model_construct(
        persona="casual", language="en", perspective_team_id=None, headline="x" * 91, body="ok.", commentary_line="ok"
    )
    assert codes(check_variant(v, sheet)) == ["too_long"]


def test_variant_set_requires_exactly_four():
    good = [variant("analyst"), variant("casual"), variant("club_fan", HOME), variant("club_fan", AWAY)]
    assert check_variant_set(good, HOME, AWAY) == []
    missing = check_variant_set(good[:3], HOME, AWAY)
    assert [str(i) for i in missing] == ["variants: missing the club_fan for RVR variant"]
    dup = check_variant_set([*good, variant("casual")], HOME, AWAY)
    assert codes(dup) == ["variants"]
    wrong_team = check_variant_set([*good[:3], variant("club_fan", "HLC")], HOME, AWAY)
    assert len(wrong_team) == 2  # missing RVR + unexpected HLC
    wrong_lang = check_variant_set([*good[:3], variant("club_fan", AWAY, language="es")], HOME, AWAY)
    assert codes(wrong_lang) == ["variants"]


def test_check_insight_copy_flags_explanation_vs_variants(sheet):
    expl = Explanation(
        headline="Brennick hits 0.99 xG", why_it_matters="ok.", tactical_tag="turning_point", key_fact_keys=["xg"]
    )
    vs = [variant("analyst"), variant("casual"), variant("club_fan", HOME), variant("club_fan", AWAY, body="72%.")]
    res = check_insight_copy(expl, vs, sheet, home_id=HOME, away_id=AWAY)
    assert not res.passed and res.explanation_failed
    assert res.messages == [
        "explanation.headline: number '0.99' is not in FACTS",
        "variants[club_fan:RVR].body: number '72' is not in FACTS",
    ]
    res2 = check_insight_copy(None, vs, sheet, home_id=HOME, away_id=AWAY)
    assert not res2.explanation_failed and len(res2.issues) == 1


def test_copy_cut_off_mid_sentence_is_flagged(sheet):
    # Foundry enforces the 300-char body limit while the model writes, so over-long copy arrives cut mid-word
    cut = variant(body="Northbridge drop back, which leaves space and turns the pressure balance sharply away, a")
    assert codes(check_variant(cut, sheet)) == ["cut_off"]
    for ending in ("ends here.", "what a goal!", "really?", "he said “wow.”", "(late on)", "y luego…"):
        assert codes(check_variant(variant(body=ending), sheet)) == [], ending
    expl = Explanation(headline="H", why_it_matters="It shifts the game", tactical_tag="chaos", key_fact_keys=[])
    assert [i.code for i in check_explanation(expl, sheet)] == ["cut_off"]
