"""Generate data/teams.json — four FICTIONAL clubs. Deterministic (seeded)."""

import json
import random
from pathlib import Path

from fulcrum.contracts import Team

rng = random.Random(2026)
FIRST = [
    "Alvo",
    "Bram",
    "Caius",
    "Dario",
    "Emre",
    "Felix",
    "Goran",
    "Hale",
    "Idris",
    "Jory",
    "Kian",
    "Lucan",
    "Mateo",
    "Niko",
    "Oren",
    "Pavel",
    "Quin",
    "Rafe",
    "Soren",
    "Tobin",
    "Ulric",
    "Vito",
    "Wren",
    "Xavi",
    "Yanis",
    "Zeke",
    "Arlo",
    "Basil",
    "Cormac",
    "Dax",
    "Ezra",
    "Finn",
    "Gael",
    "Hugo",
    "Iker",
    "Jonah",
    "Kofi",
    "Leif",
    "Milo",
    "Nuno",
    "Otto",
    "Pim",
    "Rio",
    "Sami",
    "Teo",
    "Umar",
    "Vadim",
    "Wilf",
    "Yusuf",
    "Zane",
    "Ansel",
    "Bruno",
    "Cyrus",
    "Dorian",
    "Elio",
    "Fabian",
    "Gideon",
    "Hamza",
    "Ivo",
    "Jago",
    "Kasim",
    "Lazlo",
    "Marek",
    "Nils",
    "Oskar",
    "Petr",
    "Remy",
    "Silas",
    "Tariq",
    "Valen",
    "Wim",
    "Yori",
]
LAST = [
    "Ashgrove",
    "Brennick",
    "Calloway",
    "Dunmore",
    "Eskildsen",
    "Fairbairn",
    "Garroway",
    "Halvorsen",
    "Ingleby",
    "Jarrow",
    "Kessler",
    "Lindqvist",
    "Marchetti",
    "Northcott",
    "Okonkwo-Reid",
    "Pellerin",
    "Quarrie",
    "Rasmussen-Vale",
    "Strand",
    "Thornbury",
    "Ullmann",
    "Varga",
    "Whitlock",
    "Yardley",
    "Zelenko",
    "Abernethy",
    "Blackwood",
    "Castellane",
    "Delacroix-Hunt",
    "Ellery",
    "Faraday",
    "Grimaldi",
    "Haverford",
    "Iversen",
    "Juhasz",
    "Kowalczyk",
    "Larkspur",
    "Mendhall",
    "Nakamura-Bell",
    "Ostrander",
    "Penhallow",
    "Quennell",
    "Rookwood",
    "Saltmarsh",
    "Trevanion",
    "Underhill",
    "Vickery",
    "Westmacott",
    "Yelland",
    "Zamorano",
    "Achterberg",
    "Bellweather",
    "Coldridge",
    "Draycott",
    "Eversley",
    "Fenwick-Shaw",
    "Gallardo-Penn",
    "Holloway",
    "Ironside",
    "Jessop",
    "Kittredge",
    "Loxley",
    "Mortlake",
    "Nettleford",
    "Orrell",
    "Pickering-Lowe",
    "Ravensworth",
    "Sedgewick",
    "Tolliver",
    "Upshaw",
    "Venn",
    "Aldane",
    "Brightmore",
    "Carwen",
    "Dewhurst",
    "Elstow",
    "Frome-Ashby",
    "Gillow",
    "Hartsell",
    "Ilchester",
    "Jolliffe",
    "Kerridge",
    "Lanyon",
    "Maltravers",
    "Nantes-Okafor",
    "Oddie",
    "Plumridge",
]
XI = [
    ("GK", "GK"),
    ("DF", "RB"),
    ("DF", "RCB"),
    ("DF", "LCB"),
    ("DF", "LB"),
    ("MF", "CDM"),
    ("MF", "RCM"),
    ("MF", "LCM"),
    ("FW", "RW"),
    ("FW", "ST"),
    ("FW", "LW"),
]
SUBS = [("GK", "GK"), ("DF", "CB"), ("DF", "FB"), ("MF", "CM"), ("MF", "CAM"), ("FW", "W"), ("FW", "ST")]
CLUBS = [  # id, name, short, colours, attack, defence, goal rate
    ("NBA", "Northbridge Athletic", "NBA", "#1E3A8A", "#F8FAFC", 82, 78, 1.75),
    ("RVR", "Riverside Rovers", "RVR", "#B91C1C", "#FDE68A", 76, 74, 1.45),
    ("HLC", "Harlow Cross", "HLC", "#065F46", "#F0FDF4", 70, 80, 1.20),
    ("MDU", "Marsh Dock United", "MDU", "#6D28D9", "#FACC15", 72, 66, 1.35),
]
used: set[str] = set()


assert len(set(LAST)) >= 72, "need >= 72 unique surnames for 4 x 18 players"


def name() -> tuple[str, str]:
    while True:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        if last not in used:
            used.add(last)
            return f"{first} {last}", last


def ratings(pos: str, base: int) -> dict:
    j = lambda m: max(20, min(99, int(rng.gauss(base + m, 5))))  # noqa: E731
    r = {"passing": j(0), "finishing": j(-25), "pace": j(0), "tackling": j(0), "dribbling": j(-5), "goalkeeping": 10}
    if pos == "GK":
        r.update(goalkeeping=j(5), finishing=15, dribbling=j(-30), tackling=j(-30))
    elif pos == "DF":
        r.update(tackling=j(8), finishing=j(-30))
    elif pos == "MF":
        r.update(passing=j(8), finishing=j(-12))
    else:
        r.update(finishing=j(6), dribbling=j(5), pace=j(5), tackling=j(-20))
    return r


teams = []
for cid, cname, short, c1, c2, att, dfn, gr in CLUBS:
    players = []
    for i, (pos, role) in enumerate(XI + SUBS):
        full, last = name()
        base = (att + dfn) // 2 - (6 if i >= 11 else 0)
        players.append(
            {
                "player_id": f"{cid}-{i + 1:02d}",
                "name": full,
                "short_name": last,
                "shirt_number": [1, 2, 5, 6, 3, 4, 8, 10, 7, 9, 11, 13, 15, 22, 16, 14, 17, 19][i],
                "position": pos,
                "role": role,
                "ratings": ratings(pos, base),
            }
        )
    t = Team(
        team_id=cid,
        name=cname,
        short_name=short,
        primary_colour=c1,
        secondary_colour=c2,
        formation="4-3-3",
        attack_rating=att,
        defence_rating=dfn,
        pre_match_goal_rate=gr,
        players=players,
    )
    teams.append(t.model_dump(mode="json"))

out = Path(__file__).resolve().parents[1] / "data" / "teams.json"
out.write_text(json.dumps(teams, indent=2))
print("wrote", out, sum(len(t["players"]) for t in teams), "players")
