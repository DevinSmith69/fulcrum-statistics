from fulcrum.agents.recap import RecapService, fallback_recap, recap_sheet, select_moments
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.contracts import CheckStatus, Insight, MomentType
from fulcrum.llm import FakeLLM
from tests.agents.factories import make_context, make_moment
from tests.agents.fakes import ScriptedLLM


def history():
    out = []
    for i, mtype in enumerate(
        [MomentType.ELITE_PASS, MomentType.GOAL, MomentType.HALF_TIME, MomentType.BIG_CHANCE, MomentType.RED_CARD]
    ):
        moment = make_moment(mtype, significance=[0.4, 0.9, 0.5, 0.6, 0.8][i])
        ctx = make_context(moment)
        insight = Insight(
            match_id=moment.match_id,
            moment_id=moment.moment_id,
            clock_ms=moment.clock_ms,
            explanation=fallback_explanation(ctx),
            variants=fallback_variants(ctx),
            check_status=CheckStatus.PASSED,
        )
        out.append((moment, insight))
    return out


def full_time():
    return make_moment(MomentType.FULL_TIME, significance=1.0)


def args(**kw):
    ctx = make_context(full_time())
    return {
        "full_time": ctx.moment,
        "history": history(),
        "home": ctx.home,
        "away": ctx.away,
        "score": ctx.score,
        "foreign_names": ctx.foreign_names,
        **kw,
    }


def test_select_moments_top_by_significance_in_match_order():
    h = history()
    picked = select_moments(h, n=3)
    assert [m.type for m, _ in picked] == ["big_chance", "goal", "red_card"]  # 23', 67', 71'


GOOD = {
    "title": "Northbridge Athletic 2-1 Riverside Rovers: Brennick decides it",
    "summary": "Northbridge Athletic win 2-1. Brennick's 0.34 xG finish on 67' lifted them to 71% to win; "
    "Castellane's red on 71' sealed it. Brennick (8.6) was the standout, and xG ended 2.31 to 1.05.",
    "key_moment_ids": [],
    "player_of_the_match_id": "NBA-10",
}


async def test_recap_passes_and_is_cached():
    llm = ScriptedLLM({"match_reporter": GOOD})
    svc = RecapService(llm)
    recap = await svc.recap(**args(persona="casual"))
    assert recap.check_status == CheckStatus.PASSED and recap.title == GOOD["title"]
    assert recap.persona == "casual" and recap.language == "en"
    assert await svc.recap(**args(persona="casual")) is recap and len(llm.calls) == 1
    assert svc.cached(recap.match_id, "casual", "EN") is recap
    user = llm.calls[0]["user"]
    assert "MOMENTS (chronological" in user and "FACTS (full time)" in user and "casual" in llm.calls[0]["system"]


async def test_recap_invented_number_is_repaired():
    bad = {**GOOD, "summary": "Brennick scored his 20th goal of the season."}
    llm = ScriptedLLM({"match_reporter": [bad, GOOD]})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.REPAIRED
    assert "number '20' is not in FACTS" in llm.calls[1]["user"]


async def test_recap_bad_ids_are_rejected():
    bad = {**GOOD, "key_moment_ids": ["nope"], "player_of_the_match_id": "HLC-10"}
    llm = ScriptedLLM({"match_reporter": bad})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.FALLBACK
    assert "'nope' is not one of the MOMENTS" in llm.calls[1]["user"]
    assert "'HLC-10' not in ROSTER" in llm.calls[1]["user"]


async def test_recap_fallback_after_llm_failures_passes_factcheck():
    a = args()
    recap = await RecapService(FakeLLM(fail_times=2)).recap(**a)
    assert recap.check_status == CheckStatus.FALLBACK
    assert recap.title == "Northbridge Athletic 2-1 Riverside Rovers"
    assert recap.player_of_the_match_id == "NBA-10"
    moments = select_moments(a["history"])
    sheet = recap_sheet(a["full_time"], moments, a["score"], a["foreign_names"])
    assert sheet.check_text(recap.title, "t") == [] and sheet.check_text(recap.summary, "s") == []
    assert "The turning points: 23'" in recap.summary
    assert len(recap.key_moment_ids) == 3
    assert recap.social_posts == [
        "Full time: Northbridge Athletic 2-1 Riverside Rovers. #NBARVR",
        "Standout performer: Brennick. #NBARVR",
    ]
    assert all(sheet.check_text(p.replace("#NBARVR", ""), "p") == [] for p in recap.social_posts)


def test_fallback_recap_with_no_history():
    a = args()
    recap = fallback_recap(
        match_id="m",
        persona="casual",
        full_time=a["full_time"],
        moments=[],
        home=a["home"],
        away=a["away"],
        score="0-0",
    )
    assert recap.summary.startswith("Full time: Northbridge Athletic 0-0 Riverside Rovers.")
    assert recap.key_moment_ids == []


async def test_recap_player_ids_in_text_are_repaired():
    """The first live Foundry recap wrote "Hartsell's second yellow (MDU-03)"; ids belong in fields only."""
    bad = {**GOOD, "summary": GOOD["summary"].replace("Brennick (8.6)", "Brennick (NBA-10, 8.6)")}
    llm = ScriptedLLM({"match_reporter": [bad, GOOD]})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.REPAIRED
    assert "use names, not ids: NBA-10" in llm.calls[1]["user"]


async def test_recap_cut_off_mid_sentence_is_repaired():
    bad = {**GOOD, "summary": GOOD["summary"].rstrip(".") + " and then Brenn"}
    llm = ScriptedLLM({"match_reporter": [bad, GOOD]})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.REPAIRED
    assert "recap.summary: ends mid-sentence" in llm.calls[1]["user"]


POSTS = [
    "Northbridge Athletic edge it 2-1 and Brennick is the hero. #NBARVR",
    "The 67' moment: Brennick's finish lifted Northbridge to 71% and they never looked back. #NBARVR",
    "Brennick rated 8.6 on the day.",
]


async def test_recap_carries_checked_social_posts():
    llm = ScriptedLLM({"match_reporter": {**GOOD, "social_posts": POSTS}})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.PASSED and recap.social_posts == POSTS
    assert "HASHTAG\n#NBARVR" in llm.calls[0]["user"]


async def test_social_posts_with_invented_numbers_or_stray_hashtags_are_repaired():
    bad = {**GOOD, "social_posts": ["Brennick hit 0.52 xG! #NBARVR", "What a day #football", NBA_ID_POST]}
    llm = ScriptedLLM({"match_reporter": [bad, {**GOOD, "social_posts": POSTS}]})
    recap = await RecapService(llm).recap(**args())
    assert recap.check_status == CheckStatus.REPAIRED and recap.social_posts == POSTS
    repair = llm.calls[1]["user"]
    assert "recap.social_posts[0]: number '0.52' is not in FACTS" in repair
    assert "recap.social_posts[1]: only #NBARVR is allowed, not #football" in repair
    assert "recap.social_posts[2]: use names, not ids: NBA-10" in repair


NBA_ID_POST = "Brennick (NBA-10) was everywhere. #NBARVR"


async def test_concurrent_requests_share_one_reporter_call():
    import asyncio

    llm = ScriptedLLM({"match_reporter": GOOD}, delays={"match_reporter": 0.05})
    svc = RecapService(llm)
    a = args()
    got = await asyncio.gather(*(svc.recap(**a) for _ in range(5)))
    assert len(llm.calls) == 1 and all(r == got[0] for r in got)
