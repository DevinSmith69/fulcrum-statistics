"""Deterministic template fallback (AI_PROMPT_DESIGN §6).

Used when the LLM path fails twice, errors, or times out. Every template is filled from the moment's
facts only, so it always passes the fact-checker (a unit test proves this for every MomentType × persona).

Placeholders are fact keys (`{scorer}`, `{xg}`) or deterministic aliases built in `build_slots`:

- `{team}` / `{opp}`: the moment's team and its opponent; `{us}` / `{them}`: the club-fan perspective;
  `{team_poss}` etc. are the possessive forms.
- Side-relative fact keys: `wp_home_after` is also available as `wp_team_after` / `wp_opp_after` (relative to the
  moment's team) and `wp_us_after` / `wp_them_after` (relative to the perspective team).
- `{key_abs}`: magnitude of a numeric fact (the fact-checker accepts magnitudes).
- `{home_name}`, `{away_name}`, `{score}`, `{minute}`, `{phase_before_desc}`, `{phase_after_desc}`.

Template text itself never contains digits — all numbers come from facts.
For each (MomentType, persona key) the first template whose placeholders are all available and whose output
fits the contract lengths wins; `GENERIC` templates (names, score and minute only) always render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter

from fulcrum.contracts import Explanation, InsightVariant, Moment, MomentType, Persona, TacticalTag, Team

from .context import fact_map, fmt_value, moment_minute
from .factcheck import (
    MAX_BODY,
    MAX_COMMENTARY,
    MAX_COMMENTARY_WORDS,
    MAX_HEADLINE,
    MAX_WHY,
    FactSheet,
    context_sheet,
    required_variants,
)
from .schemas import MomentContext


@dataclass(frozen=True)
class Template:
    """Copy for one persona: headline, body and a spoken commentary line."""

    headline: str
    body: str
    line: str

    @property
    def fields(self) -> set[str]:
        return {f for part in (self.headline, self.body, self.line) for _, f, _, _ in Formatter().parse(part) if f}


@dataclass(frozen=True)
class WhyTemplate:
    """The persona-neutral explanation used on fallback."""

    why: str

    @property
    def fields(self) -> set[str]:
        return {f for _, f, _, _ in Formatter().parse(self.why) if f}


T = Template

# Persona keys: analyst | casual | fan_for (moment is by the fan's team) | fan_against | fan_neutral (no team)
TEMPLATES: dict[MomentType, dict[str, list[Template]]] = {
    MomentType.GOAL: {
        "analyst": [
            T(
                "{scorer} scores: {team} {wp_scorer_before}% → {wp_scorer_after}% to win",
                "{scorer} finishes a {xg} xG chance from {distance_m} m at {shot_speed_kmh} km/h. {team} make it "
                "{score_after} and their win probability moves from {wp_scorer_before}% to {wp_scorer_after}%.",
                "{xg} xG, {shot_speed_kmh} km/h, and {team} are now at {wp_scorer_after}% to win.",
            ),
            T(
                "{scorer} scores for {team}",
                "{scorer} scores and {team} make it {score_after}. Win probability now {wp_scorer_after}%.",
                "{scorer} scores: {team} at {wp_scorer_after}% to win.",
            ),
        ],
        "casual": [
            T(
                "{scorer} scores for {team}!",
                "{scorer} scores! {team} make it {score_after} and their chances of winning jump to "
                "{wp_scorer_after}%.",
                "{scorer}! {team} make it {score_after}!",
            ),
        ],
        "fan_for": [
            T(
                "YES! {scorer} scores for {us}!",
                "{scorer} does it! {us} make it {score_after} and our chances of winning are up to "
                "{wp_scorer_after}%. Get in!",
                "Get in! {scorer} scores and {us} make it {score_after}!",
            ),
        ],
        "fan_against": [
            T(
                "{scorer} hurts us: {them} score",
                "Gutting. {scorer} finds the net and {them} make it {score_after}. Their chances of winning climb "
                "to {wp_scorer_after}%, and {us} need a response.",
                "Gutting for {us}: {scorer} scores and it is {score_after}.",
            ),
        ],
    },
    MomentType.BIG_CHANCE: {
        # The first template per persona uses the engine's what-if facts (MODEL_SPEC §3.1) when present.
        "analyst": [
            T(
                "Big chance for {shooter}: {xg} xG, {outcome}",
                "{shooter} gets a {xg} xG look from {distance_m} m (outcome: {outcome}). Had it gone in, it would "
                "be {whatif_goal_score} and {team} would be {whatif_goal_win_pct}% to win, not {whatif_win_pct_now}%.",
                "A {xg} xG chance: had it gone in, {team} would be {whatif_goal_win_pct}% to win.",
            ),
            T(
                "Big chance for {shooter}: {xg} xG, {outcome}",
                "{shooter} gets a {xg} xG look from {distance_m} m (outcome: {outcome}). {team} are up to "
                "{team_xg_total} xG for the match.",
                "A {xg} xG chance for {shooter}, and {team} are building: {team_xg_total} xG so far.",
            ),
            T(
                "Big chance for {shooter}",
                "{shooter} gets a {xg} xG look for {team} but cannot convert.",
                "That was a {xg} xG chance for {shooter}.",
            ),
        ],
        "casual": [
            T(
                "So close for {shooter}!",
                "{shooter} had a golden chance for {team} and couldn't take it. Had it gone in, their chances of "
                "winning would have jumped to {whatif_goal_win_pct}%.",
                "How has that not gone in? {shooter} will want that one back.",
            ),
            T(
                "So close for {shooter}!",
                "{shooter} had a golden chance for {team} and couldn't take it. That was one of the best openings "
                "of the game so far.",
                "How has that not gone in? {shooter} will want that one back.",
            ),
        ],
        "fan_for": [
            T(
                "{shooter} so nearly does it for {us}",
                "Agonising! {shooter} had the chance for {us} and it wouldn't go in. That would have put our "
                "chances of winning at {whatif_goal_win_pct}%. Keep creating like that.",
                "Come on! {shooter} so close for {us}.",
            ),
            T(
                "{shooter} so nearly does it for {us}",
                "Agonising! {shooter} had the chance for {us} and it wouldn't go in. Keep creating like that and "
                "the goal will come.",
                "Come on! {shooter} so close for {us}.",
            ),
        ],
        "fan_against": [
            T(
                "Let-off! {shooter} misses for {them}",
                "Phew. {shooter} had a real chance for {them} and didn't take it. Had it gone in, their chances of "
                "winning would have been {whatif_goal_win_pct}%. {us} got away with one there.",
                "Big let-off for {us}: {shooter} should have scored.",
            ),
            T(
                "Let-off! {shooter} misses for {them}",
                "Phew. {shooter} had a real chance for {them} and didn't take it. {us} got away with one there.",
                "Big let-off for {us}: {shooter} should have scored.",
            ),
        ],
    },
    MomentType.WP_SWING: {
        "analyst": [
            T(
                "{team} win probability {wp_team_before}% → {wp_team_after}%",
                "{cause_event} moves the match: {team} are now {wp_team_after}% to win (from {wp_team_before}%), "
                "{opp} {wp_opp_after}% (from {wp_opp_before}%).",
                "That {cause_event} changes the picture: {team} now at {wp_team_after}%.",
            ),
            T(
                "{team} win probability {wp_team_before}% → {wp_team_after}%",
                "The match swings: {team} are now {wp_team_after}% to win, from {wp_team_before}%.",
                "A real swing: {team} now at {wp_team_after}%.",
            ),
        ],
        "casual": [
            T(
                "The match just tilted",
                "After that {cause_event}, the picture has changed: {team} now have a {wp_team_after}% chance of "
                "winning.",
                "Feel that? The game has shifted after that {cause_event}.",
            ),
            T(
                "The match just tilted",
                "Something has changed: {team} now have a {wp_team_after}% chance of winning.",
                "Feel that? The game has shifted.",
            ),
        ],
        "fan_for": [
            T(
                "Big shift for {us}",
                "That {cause_event} changes things for {us}: our chances of winning are now {wp_team_after}%, "
                "from {wp_team_before}%.",
                "Huge moment for {us}: that {cause_event} changes everything.",
            ),
            T(
                "Big shift for {us}",
                "Things are changing for {us}: our chances of winning are now {wp_team_after}%.",
                "Huge moment for {us}.",
            ),
        ],
        "fan_against": [
            T(
                "Big shift for {them}",
                "That {cause_event} changes things for {them}: their chances of winning are now {wp_team_after}%. "
                "{us} have to respond.",
                "Careful now, {us}: that {cause_event} has changed the game.",
            ),
            T(
                "Big shift for {them}",
                "Things are changing for {them}: their chances of winning are now {wp_team_after}%.",
                "Careful now, {us}.",
            ),
        ],
    },
    MomentType.PRESSURE_SHIFT: {
        "analyst": [
            T(
                "Pressure flips to {team}",
                "{team_poss} pressure index goes from {pressure_index_team_before} to {pressure_index_team_after} "
                "while {opp} fall from {pressure_index_opp_before} to {pressure_index_opp_after}. "
                "{shots_last_5} shots and {box_entries_last_5} box entries in the recent spell.",
                "{team} have taken over: pressure index up to {pressure_index_team_after}.",
            ),
            T(
                "Pressure flips to {team}",
                "{team_poss} pressure index goes from {pressure_index_team_before} to {pressure_index_team_after}.",
                "{team} have taken over this spell.",
            ),
        ],
        "casual": [
            T(
                "{team} are turning the screw",
                "The game has turned: {team} are now the ones pushing, and {opp} are hanging on.",
                "{team} have taken over this spell.",
            ),
        ],
        "fan_for": [
            T(
                "{us} are on top now",
                "Here we go! {us} have flipped this game and {them} are pinned back. Keep it up.",
                "This is more like it from {us}!",
            ),
        ],
        "fan_against": [
            T(
                "{them} are piling on the pressure",
                "Uncomfortable viewing: {them} have taken over this spell and {us} are pinned back. We need an outlet.",
                "{us} need to weather this storm.",
            ),
        ],
    },
    MomentType.PHASE_CHANGE: {
        "analyst": [
            T(
                "Phase change: {phase_before_desc} → {phase_after_desc}",
                "The match moves from {phase_before_desc} to {phase_after_desc}. Control index {home_name} "
                "{control_index_home}, {away_name} {control_index_away}; chaos index {chaos_index}, "
                "{turnovers_last_5} recent turnovers.",
                "The shape of this game has changed: {phase_after_desc}.",
            ),
            T(
                "Phase change: {phase_after_desc}",
                "The match moves from {phase_before_desc} to {phase_after_desc}.",
                "The shape of this game has changed.",
            ),
        ],
        "casual": [
            T(
                "The game has changed character",
                "The feel of the match is different now: {phase_after_desc}.",
                "You can feel the game changing.",
            ),
        ],
        "fan_for": [
            T(
                "{us}: the game is changing",
                "The mood shifts: {phase_after_desc}. Time for {us} to make it count.",
                "Big spell coming for {us}.",
            ),
        ],
        "fan_against": [
            T(
                "{us}: the game is changing",
                "The mood shifts: {phase_after_desc}. {us} need to stay switched on.",
                "Stay switched on, {us}.",
            ),
        ],
        "fan_neutral": [
            T(
                "{us}: the game is changing",
                "The mood shifts: {phase_after_desc}. A big spell for {us}.",
                "Big spell coming for {us}.",
            ),
        ],
    },
    MomentType.TEMPO_CHANGE: {
        "analyst": [
            T(
                "Tempo shift: {tempo_ppm} passes a minute",
                "The tempo is {tempo_ppm} passes per minute against a match average of {tempo_match_avg}. The rhythm "
                "of the game has changed.",
                "Different rhythm now: {tempo_ppm} passes a minute.",
            ),
        ],
        "casual": [
            T(
                "The pace of the game has changed",
                "Notice the rhythm? The ball is moving at a very different pace to earlier in the match.",
                "Feel the tempo change?",
            ),
        ],
        "fan_neutral": [
            T(
                "{us}: the rhythm is changing",
                "The tempo has shifted to {tempo_ppm} passes a minute. {us} need to set the pace.",
                "Time for {us} to dictate the tempo.",
            ),
        ],
    },
    MomentType.RED_CARD: {
        "analyst": [
            T(
                "Red card: {player} off, {team} down to ten",
                "{player} is sent off. {opp} move from {wp_opp_before}% to {wp_opp_after}% to win; {team} from "
                "{wp_team_before}% to {wp_team_after}%.",
                "Red for {player}, and {opp} are now {wp_opp_after}% to win.",
            ),
            T(
                "Red card: {player} off, {team} down to ten",
                "{player} is sent off and {team} must play the rest of the match a player down.",
                "Red for {player}: {team} down to ten.",
            ),
        ],
        "casual": [
            T(
                "{player} is sent off!",
                "{team} are down to ten after {player} sees red, and {opp} now have a {wp_opp_after}% chance of "
                "winning.",
                "Red card! {player} is off and {team} must dig in.",
            ),
            T(
                "{player} is sent off!",
                "{team} are down to ten after {player} sees red.",
                "Red card! {player} is off and {team} must dig in.",
            ),
        ],
        "fan_for": [
            T(
                "Disaster: {player} sent off",
                "Nightmare for {us}. {player} sees red and we are down to ten; our chances of winning slip to "
                "{wp_team_after}%.",
                "That could cost {us} dearly.",
            ),
            T(
                "Disaster: {player} sent off",
                "Nightmare for {us}. {player} sees red and we are down to ten.",
                "That could cost {us} dearly.",
            ),
        ],
        "fan_against": [
            T(
                "{them} down to ten!",
                "{player} sees red and {them} are down to ten. Our chances of winning are up to {wp_opp_after}%, "
                "so it is time for {us} to pounce.",
                "Advantage {us}: {them} are down to ten!",
            ),
            T(
                "{them} down to ten!",
                "{player} sees red and {them} are down to ten. Time for {us} to pounce.",
                "Advantage {us}: {them} are down to ten!",
            ),
        ],
    },
    MomentType.SPEED_RECORD: {
        "analyst": [
            T(
                "{player} hits {speed_kmh} km/h",
                "{player} sets a new match high at {speed_kmh} km/h, beating the previous best of "
                "{previous_match_high} km/h.",
                "{speed_kmh} km/h from {player}: the quickest of the match.",
            ),
        ],
        "casual": [
            T(
                "Rocket from {player}!",
                "{player} just produced the quickest moment of the match at {speed_kmh} km/h.",
                "Did you see the speed on that from {player}?",
            ),
        ],
        "fan_for": [
            T(
                "Pure power from {player}",
                "That's our {player}: {speed_kmh} km/h, the fastest we've seen all match. {them} won't want to see "
                "that again.",
                "Electric from {player}!",
            ),
        ],
        "fan_against": [
            T(
                "Rapid {player} worries {us}",
                "{player} is flying for {them} at {speed_kmh} km/h. {us} need to keep tabs on that.",
                "{us} need to watch {player}.",
            ),
        ],
    },
    MomentType.ELITE_PASS: {
        "analyst": [
            T(
                "{passer} → {recipient}: difficulty {difficulty}",
                "{passer} completes a difficulty {difficulty} pass to {recipient}: {length_m} m, {progressive_m} m "
                "gained at {ball_speed_kmh} km/h, with only {expected_completion_pct}% expected to complete.",
                "Difficulty {difficulty} and only {expected_completion_pct}% expected: superb from {passer}.",
            ),
            T(
                "{passer} → {recipient}: an elite pass",
                "{passer} completes a difficulty {difficulty} pass to {recipient}.",
                "Superb from {passer}.",
            ),
        ],
        "casual": [
            T(
                "What a pass from {passer}!",
                "{passer} found {recipient} with a pass that almost never comes off: only {expected_completion_pct}% "
                "of passes like that are completed.",
                "{passer} threads it to {recipient}. Delicious.",
            ),
            T(
                "What a pass from {passer}!",
                "{passer} found {recipient} with a pass that almost never comes off.",
                "{passer} threads it to {recipient}. Delicious.",
            ),
        ],
        "fan_for": [
            T(
                "Magic from {passer}",
                "That's why we love {passer}! A pass that only comes off {expected_completion_pct}% of the time, "
                "straight to {recipient}.",
                "{passer} to {recipient}: pure class from {us}.",
            ),
            T(
                "Magic from {passer}",
                "That's why we love {passer}! A ridiculous pass, straight to {recipient}.",
                "{passer} to {recipient}: pure class from {us}.",
            ),
        ],
        "fan_against": [
            T(
                "{passer} carves {us} open",
                "{passer} picks out {recipient} with a pass that only comes off {expected_completion_pct}% of the "
                "time. {us} were sliced open there.",
                "{us} need to close {passer} down.",
            ),
            T(
                "{passer} carves {us} open",
                "{passer} picks out {recipient} and {us} were sliced open there.",
                "{us} need to close {passer} down.",
            ),
        ],
    },
    MomentType.PLAYER_MILESTONE: {
        "analyst": [
            T(
                "Milestone: {player}, {milestone}",
                "{player} reaches a milestone for {team}: {milestone}.",
                "{milestone} for {player}.",
            ),
        ],
        "casual": [
            T(
                "Milestone for {player}",
                "A landmark moment for {player}: {milestone}.",
                "Nice moment for {player}.",
            ),
        ],
        "fan_for": [
            T(
                "Milestone for {player}",
                "{player} keeps delivering for {us}: {milestone}.",
                "Take a bow, {player}.",
            ),
        ],
        "fan_against": [
            T(
                "{player} keeps going for {them}",
                "{player} reaches {milestone} for {them}. {us} need to cut off the supply.",
                "{us} must quieten {player}.",
            ),
        ],
    },
    MomentType.MOMENTUM_RUN: {
        # `{run_shots}` exists only for shot-triggered runs, `{run_possessions}` only for territory-triggered
        # ones (see `build_slots`), so the copy always describes what actually triggered the moment.
        "analyst": [
            T(
                "{team} on a run: {run_shots} shots to {opp_shots_last_5}",
                "{team} have {run_shots} shots and {xg_last_5} xG in the recent spell while {opp} have "
                "{opp_shots_last_5}. The momentum is all one way.",
                "{run_shots} shots to {opp_shots_last_5}: this is all {team}.",
            ),
            T(
                "{team} pinning {opp} back",
                "{team} have reached the final third on {run_possessions} possessions in a row and {opp} "
                "can't get out of their half.",
                "{run_possessions} straight attacks for {team}.",
            ),
            T(
                "{team} on a run",
                "{team} are dominating the recent spell. The momentum is all one way.",
                "This is all {team}.",
            ),
        ],
        "casual": [
            T(
                "{team} are on a roll",
                "{team} keep coming forward and {opp} can't get out. Something has to give.",
                "Wave after wave from {team}.",
            ),
        ],
        "fan_for": [
            T(
                "Wave after wave from {us}",
                "We are all over them! {run_shots} shots in this spell and {them} can't get out.",
                "Keep coming, {us}!",
            ),
            T(
                "Wave after wave from {us}",
                "We are camped in their half! {run_possessions} attacks in a row and {them} can't get out.",
                "Keep coming, {us}!",
            ),
        ],
        "fan_against": [
            T(
                "{them} are all over us",
                "Hold on, {us}! {them} have {run_shots} shots in this spell and we can't get out.",
                "{us} just need to clear their lines.",
            ),
            T(
                "{them} are all over us",
                "Hold on, {us}! {them} keep getting into our final third and we can't get out.",
                "{us} just need to clear their lines.",
            ),
        ],
    },
    MomentType.HALF_TIME: {
        "analyst": [
            T(
                "Half-time: {home_name} {score} {away_name}",
                "xG {home_name} {xg_home}, {away_name} {xg_away}. Possession {possession_home}% to {possession_away}%."
                " Win probability: {home_name} {wp_home}%, draw {wp_draw}%, {away_name} {wp_away}%.",
                "Half-time, {score}: xG {xg_home} to {xg_away}.",
            ),
            T(
                "Half-time: {home_name} {score} {away_name}",
                "It is {score} at the break. xG {home_name} {xg_home}, {away_name} {xg_away}.",
                "Half-time, {score}.",
            ),
        ],
        "casual": [
            T(
                "Half-time: {home_name} {score} {away_name}",
                "It is {score} at the break. {top_player_1} has been the standout performer so far.",
                "Half-time. Plenty still to play for.",
            ),
            T(
                "Half-time: {home_name} {score} {away_name}",
                "It is {score} at the break. Plenty still to play for.",
                "Half-time. Plenty still to play for.",
            ),
        ],
        "fan_neutral": [
            T(
                "Half-time for {us}",
                "{home_name} {score} {away_name} at the break. {us} have created {xg_us} xG; our chances of winning "
                "stand at {wp_us}%.",
                "Half-time, {score}. Big second half ahead for {us}.",
            ),
        ],
    },
    MomentType.FULL_TIME: {
        "analyst": [
            T(
                "Full time: {home_name} {score} {away_name}",
                "xG {home_name} {xg_home}, {away_name} {xg_away}; possession {possession_home}% to {possession_away}%."
                " Top performer: {top_player_1} ({top_player_1_rating}).",
                "Full time, {score}. xG {xg_home} to {xg_away}.",
            ),
        ],
        "casual": [
            T(
                "Full time: {home_name} {score} {away_name}",
                "That's it! It finishes {score}. {top_player_1} was the star of the show.",
                "Full time: it finishes {score}.",
            ),
        ],
        "fan_neutral": [
            T(
                "Full time for {us}",
                "It finishes {home_name} {score} {away_name}. {us} ended with {xg_us} xG.",
                "Full time, {score}, for {us}.",
            ),
        ],
    },
}

# Always renderable: needs only names, score and minute (always present).
GENERIC: dict[str, list[Template]] = {
    "analyst": [
        T(
            "Key moment on {minute}",
            "A moment that shapes the match between {home_name} and {away_name}. The score is {score}.",
            "One to watch back: it is {score}.",
        ),
        T("Key moment", "A moment that shapes the match.", "One to watch back."),
    ],
    "casual": [
        T(
            "A big moment on {minute}",
            "Something important just happened between {home_name} and {away_name}. It is {score}.",
            "Keep your eyes on this one: it is {score}.",
        ),
        T("A big moment", "Something important just happened.", "Keep your eyes on this one."),
    ],
    "fan": [
        T(
            "{us}: a moment that matters",
            "Big moment for {us} on {minute}. It is {home_name} {score} {away_name}.",
            "Big moment for {us}.",
        ),
        T("A moment that matters", "Big moment in this match.", "Big moment."),
    ],
}

WHY: dict[MomentType, list[WhyTemplate]] = {
    MomentType.GOAL: [
        WhyTemplate(
            "{scorer}'s goal makes it {score_after} and moves {team_poss} win probability from {wp_scorer_before}% to "
            "{wp_scorer_after}%."
        ),
        WhyTemplate("{scorer}'s goal makes it {score_after}."),
    ],
    MomentType.BIG_CHANCE: [
        WhyTemplate(
            "Had {shooter}'s {xg} xG chance gone in, it would be {whatif_goal_score} and {team_poss} win probability "
            "would be {whatif_goal_win_pct}% instead of {whatif_win_pct_now}%."
        ),
        WhyTemplate(
            "Had {shooter}'s chance gone in, {team_poss} win probability would be {whatif_goal_win_pct}% instead of "
            "{whatif_win_pct_now}%."
        ),
        WhyTemplate(
            "A {xg} xG chance for {shooter} shows {team} can open up {opp}; {team} have {team_xg_total} xG so far."
        ),
        WhyTemplate("A {xg} xG chance for {shooter} shows {team} can open up {opp}."),
    ],
    MomentType.WP_SWING: [
        WhyTemplate(  # what-if facts are only present when the swing came from the team's own shot
            "After that {cause_event}, {team_poss} win probability moves from {wp_team_before}% to {wp_team_after}%; "
            "had it gone in, it would be {whatif_goal_win_pct}%."
        ),
        WhyTemplate(
            "After {cause_event}, {team_poss} win probability moves from {wp_team_before}% to {wp_team_after}%."
        ),
        WhyTemplate("{team_poss} win probability moves from {wp_team_before}% to {wp_team_after}%."),
    ],
    MomentType.PRESSURE_SHIFT: [
        WhyTemplate(
            "{team_poss} pressure index moves from {pressure_index_team_before} to {pressure_index_team_after}, so the "
            "territorial balance of the match has flipped."
        ),
    ],
    MomentType.PHASE_CHANGE: [
        WhyTemplate("The match moves from {phase_before_desc} to {phase_after_desc}, changing who dictates play."),
    ],
    MomentType.TEMPO_CHANGE: [
        WhyTemplate(
            "Tempo is {tempo_ppm} passes per minute against a match average of {tempo_match_avg}, changing the "
            "rhythm of the game."
        ),
    ],
    MomentType.RED_CARD: [
        WhyTemplate(
            "{player}'s red card leaves {team} a player down and moves {opp_poss} win probability from "
            "{wp_opp_before}% to {wp_opp_after}%."
        ),
        WhyTemplate("{player}'s red card leaves {team} a player down for the rest of the match."),
    ],
    MomentType.SPEED_RECORD: [
        WhyTemplate("{player} sets a match-high {speed_kmh} km/h, a sign of the individual quality on the pitch."),
    ],
    MomentType.ELITE_PASS: [
        WhyTemplate(
            "{passer}'s pass to {recipient} had only {expected_completion_pct}% expected completion and gained "
            "{progressive_m} m, breaking open the defensive structure."
        ),
        WhyTemplate("{passer}'s pass to {recipient} breaks open the defensive structure."),
    ],
    MomentType.PLAYER_MILESTONE: [
        WhyTemplate("{player} reaches {milestone}, underlining their influence on this match."),
    ],
    MomentType.MOMENTUM_RUN: [
        WhyTemplate(
            "{team} have {run_shots} shots to {opp_shots_last_5} in the recent spell, so the momentum is "
            "firmly with them."
        ),
        WhyTemplate(
            "{team} have reached the final third on {run_possessions} possessions in a row, so {opp} are "
            "penned in and the momentum is firmly with them."
        ),
    ],
    MomentType.HALF_TIME: [WhyTemplate("At the break it is {score}, with xG {xg_home} to {xg_away}.")],
    MomentType.FULL_TIME: [WhyTemplate("It ends {score}, with xG {xg_home} to {xg_away}.")],
}
GENERIC_WHY = [
    WhyTemplate("This moment on {minute} shapes a match that stands at {score}."),
    WhyTemplate("This moment shapes the match."),  # slot-free: always passes the fact-checker
]

TACTICAL_TAGS: dict[MomentType, TacticalTag] = {
    MomentType.GOAL: TacticalTag.TURNING_POINT,
    MomentType.BIG_CHANCE: TacticalTag.PRESSURE_SHIFT,
    MomentType.WP_SWING: TacticalTag.TURNING_POINT,
    MomentType.PRESSURE_SHIFT: TacticalTag.PRESSURE_SHIFT,
    MomentType.PHASE_CHANGE: TacticalTag.CONTROL,
    MomentType.TEMPO_CHANGE: TacticalTag.RHYTHM,
    MomentType.RED_CARD: TacticalTag.TURNING_POINT,
    MomentType.SPEED_RECORD: TacticalTag.INDIVIDUAL_BRILLIANCE,
    MomentType.ELITE_PASS: TacticalTag.INDIVIDUAL_BRILLIANCE,
    MomentType.PLAYER_MILESTONE: TacticalTag.INDIVIDUAL_BRILLIANCE,
    MomentType.MOMENTUM_RUN: TacticalTag.PRESSURE_SHIFT,
    MomentType.HALF_TIME: TacticalTag.CONTROL,
    MomentType.FULL_TIME: TacticalTag.TURNING_POINT,
}


# --------------------------------------------------------------------------- #
# Slots
# --------------------------------------------------------------------------- #


@dataclass
class Slots:
    """Placeholder values (all strings) and, for fact-derived slots, the fact key they came from."""

    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def put(self, name: str, value: str, source: str | None = None) -> None:
        self.values[name] = value
        if source:
            self.sources[name] = source


def possessive(name: str) -> str:
    """'Riverside Rovers' → "Riverside Rovers'", 'Harlow Cross' → "Harlow Cross'", else "Name's"."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _phase_desc(phase: str, home: Team, away: Team) -> str:
    return {
        "control_home": f"{home.name} in control",
        "control_away": f"{away.name} in control",
        "chaos": "chaos",
        "balanced": "balance",
    }.get(phase, phase.replace("_", " "))


def build_slots(moment: Moment, home: Team, away: Team, score: str, perspective_team_id: str | None = None) -> Slots:
    """All placeholder values for `moment`, optionally from a club-fan perspective."""
    slots = Slots()
    names = {p.player_id: p.short_name for t in (home, away) for p in t.players}
    team_ids = {"home": home.team_id, "away": away.team_id}
    other = {home.team_id: away, away.team_id: home}
    team_by_id = {home.team_id: home, away.team_id: away}

    # relations: alias → team_id, e.g. {"team": "NBA", "opp": "RVR", "us": "RVR", "them": "NBA"}
    relations: dict[str, str] = {}
    if moment.team_id in team_by_id:
        relations |= {"team": moment.team_id, "opp": other[moment.team_id].team_id}
    if perspective_team_id in team_by_id:
        relations |= {"us": perspective_team_id, "them": other[perspective_team_id].team_id}
    for rel, tid in relations.items():
        slots.put(rel, team_by_id[tid].name)
        slots.put(f"{rel}_poss", possessive(team_by_id[tid].name))

    slots.put("home_name", home.name)
    slots.put("away_name", away.name)
    facts = fact_map(moment.facts)
    slots.put("score", score, "score" if "score" in facts else None)
    minute = moment_minute(moment)
    slots.put("minute", minute if minute.endswith("'") else f"{minute}'", "minute" if "minute" in facts else None)

    for fact in moment.facts:
        if isinstance(fact.value, str):
            value = names.get(fact.value) or fact.value.replace("_", " ")
        else:
            value = fmt_value(fact.value)
            magnitude = fmt_value(abs(fact.value))
            slots.put(f"{fact.key}_abs", magnitude, fact.key)
        if fact.key not in slots.values:
            slots.put(fact.key, value, fact.key)
        tokens = fact.key.split("_")
        for side in ("home", "away"):
            if side not in tokens:
                continue
            for rel, tid in relations.items():
                if tid == team_ids[side]:
                    alias = "_".join(rel if t == side else t for t in tokens)
                    slots.put(alias, value, fact.key)
                    if not isinstance(fact.value, str):
                        slots.put(f"{alias}_abs", fmt_value(abs(fact.value)), fact.key)
    for key in ("phase_before", "phase_after"):
        if key in facts:
            slots.put(f"{key}_desc", _phase_desc(str(facts[key].value), home, away), key)
    if moment.type == MomentType.MOMENTUM_RUN:
        # The engine fires a run on shots OR on final-third possessions; only offer the matching slot so a
        # territory run is never described as "0 shots to 0".
        if "final third" in moment.trigger and "final_third_possessions_in_row" in facts:
            slots.put(
                "run_possessions", slots.values["final_third_possessions_in_row"], "final_third_possessions_in_row"
            )
        elif "shots" in moment.trigger and "shots_last_5" in facts:
            slots.put("run_shots", slots.values["shots_last_5"], "shots_last_5")
    return slots


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _fits(headline: str, body: str, line: str) -> bool:
    return (
        len(headline) <= MAX_HEADLINE
        and len(body) <= MAX_BODY
        and len(line) <= MAX_COMMENTARY
        and len(line.split()) <= MAX_COMMENTARY_WORDS
    )


def render(template: Template, slots: Slots, sheet: FactSheet | None = None) -> tuple[str, str, str] | None:
    """Fill `template`, or return None if a placeholder is missing, a length limit is exceeded or (given a
    `sheet`) the output fails the fact-checker — e.g. a string fact naming a player not in this match."""
    if not template.fields <= slots.values.keys():
        return None
    out = tuple(part.format_map(slots.values) for part in (template.headline, template.body, template.line))
    if not _fits(*out):
        return None
    if sheet is not None and any(sheet.check_text(part, "template") for part in out):
        return None
    return out  # type: ignore[return-value]


def persona_key(persona: str, moment_team_id: str | None, perspective_team_id: str | None) -> str:
    """Template group for a persona: analyst | casual | fan_for | fan_against | fan_neutral."""
    if persona != Persona.CLUB_FAN:
        return str(persona)
    if moment_team_id is None or perspective_team_id is None:
        return "fan_neutral"
    return "fan_for" if moment_team_id == perspective_team_id else "fan_against"


def candidates(mtype: MomentType | str, key: str) -> list[Template]:
    """Templates to try, in order, for a moment type and persona key (ending with the generic ones)."""
    by_type = TEMPLATES.get(MomentType(mtype), {})
    specific = by_type.get(key, [])
    if not specific and key.startswith("fan_"):
        specific = by_type.get("fan_neutral", [])
    return [*specific, *GENERIC["fan" if key.startswith("fan_") else key]]


def _first(templates: list[Template], slots: Slots, sheet: FactSheet | None) -> tuple[Template, tuple[str, str, str]]:
    for template in templates:
        if (out := render(template, slots, sheet)) is not None:
            return template, out
    raise AssertionError("GENERIC templates must always render")  # pragma: no cover


def render_variant(
    moment: Moment,
    home: Team,
    away: Team,
    score: str,
    persona: str,
    perspective_team_id: str | None = None,
    sheet: FactSheet | None = None,
) -> InsightVariant:
    """The fallback variant for one persona (English). With `sheet`, only fact-check-clean copy is used."""
    slots = build_slots(moment, home, away, score, perspective_team_id)
    _, (headline, body, line) = _first(
        candidates(moment.type, persona_key(persona, moment.team_id, perspective_team_id)), slots, sheet
    )
    return InsightVariant(
        persona=persona,
        language="en",
        perspective_team_id=perspective_team_id,
        headline=headline,
        body=body,
        commentary_line=line,
    )


def fallback_variants(ctx: MomentContext) -> list[InsightVariant]:
    """The four required variants (analyst, casual, club_fan home, club_fan away) from templates."""
    sheet = context_sheet(ctx)
    return [
        render_variant(ctx.moment, ctx.home, ctx.away, ctx.score, persona, team, sheet)
        for persona, team in required_variants(ctx.home.team_id, ctx.away.team_id)
    ]


def fallback_explanation(ctx: MomentContext) -> Explanation:
    """A persona-neutral explanation from templates; `key_fact_keys` lists the facts actually used."""
    slots = build_slots(ctx.moment, ctx.home, ctx.away, ctx.score)
    sheet = context_sheet(ctx)
    template, (headline, _, _) = _first(candidates(ctx.moment.type, "analyst"), slots, sheet)
    headline_fields = {f for _, f, _, _ in Formatter().parse(template.headline) if f}
    for why in [*WHY.get(MomentType(ctx.moment.type), []), *GENERIC_WHY]:
        if not why.fields <= slots.values.keys():
            continue
        text = why.why.format_map(slots.values)
        if len(text) > MAX_WHY or sheet.check_text(text, "template"):
            continue
        keys = sorted({slots.sources[f] for f in why.fields | headline_fields if f in slots.sources})
        return Explanation(
            headline=headline,
            why_it_matters=text,
            tactical_tag=TACTICAL_TAGS.get(MomentType(ctx.moment.type), TacticalTag.TURNING_POINT),
            key_fact_keys=keys,
        )
    raise AssertionError("GENERIC_WHY must always render")  # pragma: no cover
