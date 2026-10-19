"""Turns ESPN league data into Gazette issues: results, stories, awards and rankings.

Pure functions only (no network), so they can be tested with fixture data.
"""

import hashlib
import statistics

# ESPN lineup slot ids.
SLOT_NAMES = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 16: "D/ST", 17: "K",
              20: "Bench", 21: "IR", 23: "FLEX"}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH_SLOTS = {20, 21}
# Fill single-position slots before flex slots when building the best possible lineup.
SLOT_FILL_ORDER = [0, 2, 4, 6, 16, 17, 3, 5, 23, 7]
PRO_TEAMS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
    11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX",
    33: "BAL", 34: "HOU",
}


def r2(x):
    return round(x or 0, 2)


# ---------- League ----------

def parse_league(raw, cfg):
    settings = raw.get("settings") or {}
    members = {m.get("id"): m for m in raw.get("members", [])}
    owner_names = cfg.get("owners", {})  # optional {"teamId": "Name"} set in gazette.json
    teams = []
    for t in raw.get("teams", []):
        tid = t.get("id")
        owner = owner_names.get(str(tid))
        if not owner and cfg.get("show_owner_first_names"):
            m = members.get((t.get("owners") or [None])[0]) or {}
            owner = m.get("firstName")
        teams.append({
            "id": tid,
            "name": t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {tid}",
            "abbrev": t.get("abbrev") or f"T{tid}",
            "owner": owner,
            "logo": t.get("logo"),
        })
    counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    return {
        "id": raw.get("id"),
        "season": raw.get("seasonId"),
        "name": cfg.get("name") or settings.get("name") or "Our League",
        "espn_name": settings.get("name"),
        "my_team": cfg.get("my_team"),
        "regular_season_weeks": (settings.get("scheduleSettings") or {}).get("matchupPeriodCount"),
        "slot_counts": {int(k): v for k, v in counts.items() if v},
        "teams": teams,
    }


def finished_weeks(raw):
    return sorted({m["matchupPeriodId"] for m in raw.get("schedule", [])
                   if m.get("winner") in ("HOME", "AWAY", "TIE") and m.get("home") and m.get("away")})


# ---------- One week ----------

def parse_player(entry, week):
    ppe = entry.get("playerPoolEntry") or {}
    p = ppe.get("player") or {}
    proj = None
    for s in p.get("stats") or []:
        if s.get("statSourceId") == 1 and s.get("scoringPeriodId") == week:
            proj = s.get("appliedTotal")
    slot = entry.get("lineupSlotId")
    return {
        "name": p.get("fullName", "Unknown"),
        "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
        "nfl": PRO_TEAMS.get(p.get("proTeamId"), ""),
        "slot": SLOT_NAMES.get(slot, str(slot)),
        "slot_id": slot,
        "starter": slot not in BENCH_SLOTS,
        "pts": r2(ppe.get("appliedStatTotal")),
        "proj": r2(proj) if proj is not None else None,
        "eligible": p.get("eligibleSlots") or [],
    }


def optimal_points(players, slot_counts):
    """Best possible starting lineup score from the players on the roster that week (greedy, position slots first)."""
    pool = sorted([p for p in players if p["slot_id"] != 21], key=lambda p: -p["pts"])
    used, total = set(), 0.0
    for slot in SLOT_FILL_ORDER:
        for _ in range(slot_counts.get(slot, 0)):
            for i, p in enumerate(pool):
                if i not in used and slot in p["eligible"]:
                    used.add(i)
                    total += p["pts"]
                    break
    return r2(total)


def parse_week(raw, week, league):
    matchups = []
    for m in raw.get("schedule", []):
        if m.get("matchupPeriodId") != week or not m.get("home") or not m.get("away"):
            continue
        sides = []
        for key in ("home", "away"):
            s = m[key]
            roster = s.get("rosterForCurrentScoringPeriod") or s.get("rosterForMatchupPeriod") or {}
            players = [parse_player(e, week) for e in roster.get("entries", [])]
            pts = r2(s.get("totalPoints"))
            sides.append({
                "team": s.get("teamId"),
                "pts": pts,
                "players": sorted(players, key=lambda p: (not p["starter"], -p["pts"])),
                "optimal": max(pts, optimal_points(players, league["slot_counts"])) if players else pts,
            })
        winner = m.get("winner")
        matchups.append({"home": sides[0], "away": sides[1], "winner": winner,
                         "playoff": m.get("playoffTierType", "NONE") != "NONE"})
    return {"week": week, "matchups": matchups}


# ---------- Season context ----------

def add_season_context(league, weeks):
    """Standings after every week, plus a week-by-week log per team."""
    rec = {t["id"]: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "aw": 0, "al": 0, "streak": "", "scores": []}
           for t in league["teams"]}
    log = {t["id"]: [] for t in league["teams"]}
    snapshots = {}
    for wk in weeks:
        scores = [s["pts"] for m in wk["matchups"] for s in (m["home"], m["away"])]
        for m in wk["matchups"]:
            for me, opp in ((m["home"], m["away"]), (m["away"], m["home"])):
                r = rec[me["team"]]
                result = "W" if me["pts"] > opp["pts"] else "L" if me["pts"] < opp["pts"] else "T"
                if not m["playoff"]:
                    r["w" if result == "W" else "l" if result == "L" else "t"] += 1
                    r["pf"] += me["pts"]
                    r["pa"] += opp["pts"]
                    # All-play: how this score would have done against every other team this week.
                    r["aw"] += sum(1 for x in scores if x < me["pts"])
                    r["al"] += sum(1 for x in scores if x > me["pts"])
                r["scores"].append(me["pts"])
                r["streak"] = (r["streak"] + result)[-10:]
                log[me["team"]].append({
                    "week": wk["week"], "opp": opp["team"], "pf": me["pts"], "pa": opp["pts"], "result": result,
                    "optimal": me["optimal"], "playoff": m["playoff"],
                    "week_rank": sorted(scores, reverse=True).index(me["pts"]) + 1,
                })
        snapshots[wk["week"]] = standings_table(league, rec)
    league["standings"] = snapshots[weeks[-1]["week"]] if weeks else standings_table(league, rec)
    league["snapshots"] = snapshots
    league["log"] = log


def standings_table(league, rec):
    rows = []
    n = len(league["teams"])
    for t in league["teams"]:
        r = rec[t["id"]]
        games = r["w"] + r["l"] + r["t"]
        win_pct = (r["w"] + r["t"] / 2) / games if games else 0
        all_pct = r["aw"] / (r["aw"] + r["al"]) if (r["aw"] + r["al"]) else 0
        recent = r["scores"][-3:]
        rows.append({
            "team": t["id"], "w": r["w"], "l": r["l"], "t": r["t"], "pf": r2(r["pf"]), "pa": r2(r["pa"]),
            "all_w": r["aw"], "all_l": r["al"], "win_pct": round(win_pct, 3), "all_pct": round(all_pct, 3),
            "streak": streak_label(r["streak"]), "recent_avg": r2(sum(recent) / len(recent)) if recent else 0,
        })
    rows.sort(key=lambda x: (-x["win_pct"], -x["pf"]))
    for i, row in enumerate(rows):
        row["rank"] = i + 1
    # Power score: mostly all-play (skill), then record (results), then recent form.
    max_recent = max((x["recent_avg"] for x in rows), default=1) or 1
    for row in rows:
        row["power"] = round(0.55 * row["all_pct"] + 0.3 * row["win_pct"] + 0.15 * row["recent_avg"] / max_recent, 4)
    for i, row in enumerate(sorted(rows, key=lambda x: -x["power"])):
        row["power_rank"] = i + 1
    return rows


def streak_label(s):
    if not s:
        return ""
    last = s[-1]
    n = len(s) - len(s.rstrip(last))
    return f"{last}{n}"


# ---------- Issue (what the site shows for a week) ----------

def pick(options, *seed):
    """Deterministic 'random' choice so the same week always gets the same wording."""
    h = int(hashlib.md5("|".join(map(str, seed)).encode()).hexdigest(), 16)
    return options[h % len(options)]


def headline(w, l, margin, week):
    if margin < 3:
        verbs = ["survives", "escapes", "squeaks past", "holds off"]
        return f"{w} {pick(verbs, week, w, l)} {l} in a {margin:.1f}-point thriller"
    if margin < 12:
        return f"{w} {pick(['edges', 'outlasts', 'gets past', 'tops'], week, w, l)} {l}"
    if margin < 30:
        return f"{w} {pick(['handles', 'takes care of', 'cruises past', 'beats'], week, w, l)} {l}"
    return f"{w} {pick(['demolishes', 'routs', 'flattens', 'embarrasses', 'runs over'], week, w, l)} {l}"


def top_starter(side):
    starters = [p for p in side["players"] if p["starter"]]
    return max(starters, key=lambda p: p["pts"]) if starters else None


def biggest_dud(side):
    """Starter who fell furthest short of their projection (only counts players projected for 8+)."""
    cands = [p for p in side["players"] if p["starter"] and p["proj"] and p["proj"] >= 8]
    if not cands:
        return None
    d = min(cands, key=lambda p: p["pts"] - p["proj"])
    return d if d["pts"] - d["proj"] <= -6 else None


def build_issue(wk, weeks, league, note):
    names = {t["id"]: t["name"] for t in league["teams"]}
    week = wk["week"]
    scores = [s["pts"] for m in wk["matchups"] for s in (m["home"], m["away"])]
    median = statistics.median(scores) if scores else 0

    games = []
    for m in wk["matchups"]:
        a, b = m["home"], m["away"]
        w, l = (a, b) if a["pts"] >= b["pts"] else (b, a)
        margin = r2(w["pts"] - l["pts"])
        wt, lt = top_starter(w), top_starter(l)
        story = [f"{names[w['team']]} beat {names[l['team']]} {w['pts']:.2f} to {l['pts']:.2f}."]
        if wt:
            story.append(f"{wt['name']} led the way with {wt['pts']:.1f} points.")
        if lt:
            story.append(f"For {names[l['team']]}, {lt['name']} put up {lt['pts']:.1f}.")
        for side in (w, l):
            d = biggest_dud(side)
            if d:
                story.append(f"{d['name']} was a letdown for {names[side['team']]}: {d['pts']:.1f} points on a {d['proj']:.1f} projection.")
                break
        if l["optimal"] > w["pts"]:
            story.append(f"The painful part: {names[l['team']]} had {l['optimal']:.2f} possible points on the roster, "
                         f"enough to win, but {l['optimal'] - l['pts']:.2f} of them sat on the bench.")
        key = f"{min(a['team'], b['team'])}-{max(a['team'], b['team'])}"
        games.append({
            "winner": w["team"], "loser": l["team"], "w_pts": w["pts"], "l_pts": l["pts"], "margin": margin,
            "headline": headline(names[w["team"]], names[l["team"]], margin, week),
            "story": " ".join(story),
            "note": (note.get("matchups") or {}).get(key),
            "playoff": m["playoff"],
            "sides": [w, l],
        })
    games.sort(key=lambda g: g["margin"])

    sides = [s for m in wk["matchups"] for s in (m["home"], m["away"])]
    starters = [(p, s["team"]) for s in sides for p in s["players"] if p["starter"]]
    win_ids = {g["winner"] for g in games}
    awards = []

    def award(key, title, team, value, detail):
        awards.append({"key": key, "title": title, "team": team, "value": value, "detail": detail})

    hi = max(sides, key=lambda s: s["pts"])
    lo = min(sides, key=lambda s: s["pts"])
    award("high", "High Score", hi["team"], f"{hi['pts']:.2f}", "Top score of the week.")
    award("low", "Basement Dweller", lo["team"], f"{lo['pts']:.2f}", "Lowest score of the week.")
    blow = max(games, key=lambda g: g["margin"])
    award("blowout", "Blowout of the Week", blow["winner"], f"+{blow['margin']:.2f}",
          f"Beat {names[blow['loser']]} by {blow['margin']:.2f}.")
    close = min(games, key=lambda g: g["margin"])
    award("close", "Nail-Biter", close["winner"], f"+{close['margin']:.2f}",
          f"Survived {names[close['loser']]} by {close['margin']:.2f}.")
    blunder = max(sides, key=lambda s: s["optimal"] - s["pts"])
    left = r2(blunder["optimal"] - blunder["pts"])
    if left > 0:
        cost = blunder["team"] not in win_ids and blunder["optimal"] > next(g["w_pts"] for g in games if g["loser"] == blunder["team"])
        award("bench", "Bench Blunder", blunder["team"], f"{left:.2f}",
              f"Points left on the bench{' — and it cost them the game' if cost else ''}.")
    best = max(sides, key=lambda s: s["pts"] / s["optimal"] if s["optimal"] else 0)
    if best["optimal"]:
        award("coach", "Coach of the Week", best["team"], f"{best['pts'] / best['optimal'] * 100:.0f}%",
              "Closest to a perfect lineup.")
    lucky = [s for s in sides if s["team"] in win_ids and s["pts"] < median]
    if lucky:
        s = min(lucky, key=lambda s: s["pts"])
        award("lucky", "Lucky Win", s["team"], f"{s['pts']:.2f}", f"Won with a below-median score (median {median:.2f}).")
    unlucky = [s for s in sides if s["team"] not in win_ids and s["pts"] > median]
    if unlucky:
        s = max(unlucky, key=lambda s: s["pts"])
        award("unlucky", "Tough Luck", s["team"], f"{s['pts']:.2f}", f"Lost despite an above-median score (median {median:.2f}).")
    if starters:
        mvp, mt = max(starters, key=lambda x: x[0]["pts"])
        award("mvp", "Player of the Week", mt, f"{mvp['pts']:.1f}", f"{mvp['name']} ({mvp['pos']}, {mvp['nfl']})")
        projected = [(p, t) for p, t in starters if p["proj"] and p["proj"] >= 8]
        if projected:
            dud, dt = min(projected, key=lambda x: x[0]["pts"] - x[0]["proj"])
            if dud["pts"] - dud["proj"] <= -6:
                award("dud", "Dud of the Week", dt, f"{dud['pts']:.1f}",
                      f"{dud['name']} ({dud['pos']}) on a {dud['proj']:.1f} projection.")

    # Lead story: a close game if there was one, otherwise the highest-scoring matchup.
    lead = games[0] if games and games[0]["margin"] < 5 else max(games, key=lambda g: g["w_pts"] + g["l_pts"])

    standings = league["snapshots"][week]
    prev = league["snapshots"].get(week - 1)
    prev_rank = {r["team"]: r["power_rank"] for r in prev} if prev else {}
    power = sorted(standings, key=lambda r: r["power_rank"])
    power = [{**r, "move": (prev_rank[r["team"]] - r["power_rank"]) if r["team"] in prev_rank else 0} for r in power]

    for g in games:
        for s in g["sides"]:
            s["players"] = [{k: v for k, v in p.items() if k not in ("eligible", "slot_id")} for p in s["players"]]

    return {
        "week": week,
        "lead": games.index(lead),
        "headline": note.get("headline") or lead["headline"],
        "column": note.get("column"),
        "games": games,
        "awards": awards,
        "median": r2(median),
        "average": r2(sum(scores) / len(scores)) if scores else 0,
        "standings": standings,
        "power": power,
    }
