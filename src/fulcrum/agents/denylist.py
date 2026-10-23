"""Words the agents must never output: real football brands and gambling language.

Fulcrum is fictional end to end (CLAUDE.md rule 2), and the copy must never read like betting content
(AI_PROMPT_DESIGN §5.4). Matching is done by `factcheck.py` on word boundaries:
multi-word phrases and `CASE_INSENSITIVE` entries ignore case; single capitalised club words are
matched case-sensitively so ordinary words ("arsenal of passes", "wolves") are not flagged in lower case.

Deliberately NOT listed: generic words that collide with our fictional clubs or normal prose
("United", "City", "Villa", "Forest", "Palace", "Rovers", "Athletic", "Saints", "Blues", "Reds").
"""

from __future__ import annotations

# Competition / brand names. Case-insensitive.
COMPETITION_NAMES: tuple[str, ...] = (
    "Premier League",
    "Premiership",
    "English Premier League",
    "EPL",
    "Barclays",
    "Championship play-off",
    "FA Cup",
    "Carabao Cup",
    "League Cup",
    "Champions League",
    "Europa League",
)

# Real English top-flight clubs (recent seasons), full names and common short forms. Case-sensitive.
REAL_CLUB_NAMES: tuple[str, ...] = (
    "Arsenal",
    "Aston Villa",
    "Bournemouth",
    "AFC Bournemouth",
    "Brentford",
    "Brighton",
    "Brighton & Hove Albion",
    "Brighton and Hove Albion",
    "Burnley",
    "Chelsea",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Ipswich",
    "Ipswich Town",
    "Leeds",
    "Leeds United",
    "Leicester",
    "Leicester City",
    "Liverpool",
    "Luton",
    "Luton Town",
    "Manchester City",
    "Man City",
    "Manchester United",
    "Man United",
    "Man Utd",
    "Manchester",
    "Newcastle",
    "Newcastle United",
    "Nottingham Forest",
    "Nott'm Forest",
    "Sheffield United",
    "Southampton",
    "Sunderland",
    "Tottenham",
    "Tottenham Hotspur",
    "Spurs",
    "West Ham",
    "West Ham United",
    "Wolverhampton",
    "Wolverhampton Wanderers",
    "Wolves",
    "Watford",
    "Norwich",
    "West Brom",
    "West Bromwich Albion",
    # distinctive nicknames
    "Gunners",
    "Red Devils",
    "Hammers",
    "Magpies",
    "Toffees",
    "Foxes",
    "Cherries",
    "Cottagers",
    "Villans",
    "Seagulls",
    "Hatters",
)

# Gambling language in every demo language (en, es, fr, pt, de). Case-insensitive, whole words.
BANNED_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "odds",
        "bet",
        "bets",
        "betting",
        "bettor",
        "price",
        "prices",
        "priced",
        "market",
        "markets",
        "bookmaker",
        "bookmakers",
        "bookie",
        "bookies",
        "wager",
        "wagers",
        "wagering",
        "gamble",
        "gambling",
        "punter",
        "punters",
        "accumulator",
    ),
    "es": ("apuesta", "apuestas", "apostar", "apostador", "cuota", "cuotas", "casa de apuestas", "mercado", "precio"),
    "fr": ("pari", "paris sportifs", "parier", "parieur", "cote", "cotes", "bookmaker", "marché", "marchés"),
    "pt": ("aposta", "apostas", "apostar", "apostador", "casa de apostas", "mercado", "preço", "cotação"),
    "de": ("Wette", "Wetten", "wetten", "Buchmacher", "Wettquote", "Quote", "Quoten", "Markt", "Preis"),
}

ALL_BANNED_TERMS: tuple[str, ...] = tuple(sorted({t for terms in BANNED_TERMS.values() for t in terms}))
