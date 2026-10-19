"""Turns ESPN league data into Gazette issues: results, stories, awards and rankings.

Pure functions only (no network), so they can be tested with fixture data.
"""

import hashlib
import statistics
from datetime import datetime

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


# ESPN abbreviations that differ from nflverse's (used for the NFL schedule).
NFL_ABBR = {"LAR": "LA"}
ALL_NFL = {NFL_ABBR.get(t, t) for t in PRO_TEAMS.values()}


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
        "schedule": [{"week": m["matchupPeriodId"], "home": m["home"]["teamId"], "away": m["away"]["teamId"]}
                     for m in raw.get("schedule", []) if m.get("home") and m.get("away")],
    }


def finished_weeks(raw):
    return sorted({m["matchupPeriodId"] for m in raw.get("schedule", [])
                   if m.get("winner") in ("HOME", "AWAY", "TIE") and m.get("home") and m.get("away")})


def current_week(raw):
    """The week being played right now (not finished yet), or None."""
    cur = (raw.get("status") or {}).get("currentMatchupPeriod")
    has_games = any(m.get("matchupPeriodId") == cur and m.get("home") and m.get("away") for m in raw.get("schedule", []))
    return cur if cur and has_games and cur not in finished_weeks(raw) else None


# ---------- NFL schedule (which games are done, which are on Monday night) ----------

def nfl_week(rows, season, week):
    """{team: {"kick", "day", "final"}} for one NFL week, from nflverse's games.csv rows."""
    out = {}
    for g in rows:
        if g["season"] != str(season) or g["week"] != str(week) or g["game_type"] != "REG":
            continue
        kick = f"{g['gameday']}T{g['gametime'] or '13:00'}"
        info = {"kick": kick, "day": g["weekday"], "final": g["home_score"] not in ("", "NA")}
        out[g["home_team"]] = out[g["away_team"]] = info
    return out


# ---------- One week ----------

def parse_player(entry, week, nfl):
    ppe = entry.get("playerPoolEntry") or {}
    p = ppe.get("player") or {}
    proj = None
    for s in p.get("stats") or []:
        if s.get("statSourceId") == 1 and s.get("scoringPeriodId") == week:
            proj = s.get("appliedTotal")
    slot = entry.get("lineupSlotId")
    team = PRO_TEAMS.get(p.get("proTeamId"), "")
    game = nfl.get(NFL_ABBR.get(team, team)) or {}
    return {
        "name": p.get("fullName", "Unknown"),
        "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
        "nfl": team,
        "slot": SLOT_NAMES.get(slot, str(slot)),
        "slot_id": slot,
        "starter": slot not in BENCH_SLOTS,
        "pts": r2(ppe.get("appliedStatTotal")),
        "proj": r2(proj) if proj is not None else None,
        "eligible": p.get("eligibleSlots") or [],
        # NFL game timing: whether it's over, and when it kicked off (to spot Monday night).
        "kick": game.get("kick"),
        "day": game.get("day"),
        "done": game.get("final", True) if game else True,
    }


def optimal_lineup(players, slot_counts):
    """Best possible starting lineup from the players on the roster that week (greedy, position slots first).
    Returns (total points, indexes into `players` of the players it starts)."""
    order = sorted([i for i, p in enumerate(players) if p["slot_id"] != 21], key=lambda i: -players[i]["pts"])
    used, total = [], 0.0
    for slot in SLOT_FILL_ORDER:
        for _ in range(slot_counts.get(slot, 0)):
            for i in order:
                if i not in used and slot in players[i]["eligible"]:
                    used.append(i)
                    total += players[i]["pts"]
                    break
    return r2(total), used


def pair_swaps(should_start, should_sit):
    """[(bench player, starter they'd replace)], biggest gain first. Same position first, then any slot they can fill."""
    sit = list(should_sit)
    pairs = []
    for p in sorted(should_start, key=lambda p: -p["pts"]):
        same = [q for q in sit if q["pos"] == p["pos"]]
        fits = same or [q for q in sit if q["slot_id"] in p["eligible"]]
        if not fits:
            continue
        out = min(fits, key=lambda q: q["pts"])
        sit.remove(out)
        pairs.append((p, out))
    return sorted(pairs, key=lambda x: -(x[0]["pts"] - x[1]["pts"]))


def optimal_points(players, slot_counts):
    return optimal_lineup(players, slot_counts)[0]


# ESPN box-score order: QB, RB, WR, TE, FLEX, D/ST, K, then the bench and IR.
SLOT_ORDER = {0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 23: 6, 7: 7, 16: 8, 17: 9, 20: 10, 21: 11}


def parse_week(raw, week, league, nfl=None):
    nfl = nfl or {}
    kicks = [g["kick"] for g in nfl.values() if g.get("kick")]
    matchups = []
    for m in raw.get("schedule", []):
        if m.get("matchupPeriodId") != week or not m.get("home") or not m.get("away"):
            continue
        sides = []
        for key in ("home", "away"):
            s = m[key]
            roster = s.get("rosterForCurrentScoringPeriod") or s.get("rosterForMatchupPeriod") or {}
            players = [parse_player(e, week, nfl) for e in roster.get("entries", [])]
            # ESPN leaves totalPoints at 0 until a week is final; use the live total or add up the starters.
            pts = r2(s.get("totalPoints"))
            if not pts:
                pts = r2(s.get("totalPointsLive") or sum(p["pts"] for p in players if p["starter"]))
            best, picked = optimal_lineup(players, league["slot_counts"]) if players else (pts, [])
            # Bench players the best lineup would have started, and the starters they'd have replaced.
            should_start = [players[i] for i in picked if not players[i]["starter"]]
            should_sit = [p for i, p in enumerate(players) if p["starter"] and i not in picked]
            sides.append({
                "team": s.get("teamId"),
                "pts": pts,
                "players": sorted(players, key=lambda p: (SLOT_ORDER.get(p["slot_id"], 12), -p["pts"])),
                "optimal": max(pts, best),
                "should_start": sorted(should_start, key=lambda p: -p["pts"]),
                "swaps": pair_swaps(should_start, should_sit),
            })
        matchups.append({"home": sides[0], "away": sides[1], "winner": m.get("winner"),
                         "playoff": m.get("playoffTierType", "NONE") != "NONE"})
    # The week's last kickoff slot (usually Monday night).
    return {"week": week, "matchups": matchups, "last_kick": max(kicks) if kicks else None}


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


# ---------- Writing helpers ----------

def pick(options, *seed):
    """Deterministic 'random' choice so the same week always gets the same wording."""
    h = int(hashlib.md5("|".join(map(str, seed)).encode()).hexdigest(), 16)
    return options[h % len(options)]


def headline(w, l, margin, week, late=None):
    if late == "comeback":
        return f"{w} {pick(['steals one from', 'stuns', 'rallies past'], week, w, l)} {l} on Monday night"
    if margin < 3:
        verbs = ["survives", "escapes", "squeaks past", "holds off"]
        return f"{w} {pick(verbs, week, w, l)} {l} in a {margin:.1f}-point thriller"
    if margin < 12:
        return f"{w} {pick(['edges', 'outlasts', 'gets past', 'tops'], week, w, l)} {l}"
    if margin < 30:
        return f"{w} {pick(['handles', 'takes care of', 'cruises past', 'beats'], week, w, l)} {l}"
    return f"{w} {pick(['demolishes', 'routs', 'flattens', 'embarrasses', 'runs over'], week, w, l)} {l}"


def starters(side):
    return [p for p in side["players"] if p["starter"]]


def top_starter(side):
    s = starters(side)
    return max(s, key=lambda p: p["pts"]) if s else None


def duds(side, limit=6):
    """Starters projected for 8+ who missed by `limit` or more, worst first."""
    out = [p for p in starters(side) if p["proj"] and p["proj"] >= 8 and p["pts"] - p["proj"] <= -limit]
    return sorted(out, key=lambda p: p["pts"] - p["proj"])


def is_late(p, last_kick):
    return bool(last_kick and p.get("kick") == last_kick)


def late_points(side, last_kick):
    return r2(sum(p["pts"] for p in starters(side) if is_late(p, last_kick)))


def pts_list(players, n=3):
    return ", ".join(f"{p['name']} ({p['pts']:.1f})" for p in players[:n])


def record_str(r):
    return f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else "")


# ---------- Articles ----------

def share_phrase(part, total):
    """'over a third of', 'nearly half of' ... the team's points."""
    if not total:
        return None
    f = part / total
    return ("more than half of" if f > 0.5 else "nearly half of" if f >= 0.43 else "over a third of" if f >= 0.33
            else "about a quarter of" if f >= 0.24 else None)


def score_context(side, nm, week_rank, n_teams):
    """'Oops put up 132.10 this week, 18.0 over their projection and the 2nd-highest score in the league.'"""
    bits = []
    proj = side.get("proj_total")
    if proj:
        diff = side["pts"] - proj
        if abs(diff) >= 5:
            bits.append(f"{abs(diff):.1f} {'over' if diff > 0 else 'under'} their projected {proj:.1f}")
    if week_rank == 1:
        bits.append("the highest score in the league")
    elif week_rank == n_teams:
        bits.append("the lowest score in the league")
    elif week_rank <= 3:
        bits.append(f"the {ordinal(week_rank)}-highest score of the week")
    elif week_rank >= n_teams - 2:
        bits.append(f"the {ordinal(n_teams - week_rank + 1)}-lowest score of the week")
    if not bits:
        return None
    verb = "put up" if side["pts"] >= 100 else "managed"
    return f"{nm} {verb} {side['pts']:.2f} this week, " + " and ".join(bits) + "."


def a_an(num_text):
    """'an 8.8' / 'an 11.2' / 'an 18.0' / 'a 12.3' — by how the number sounds."""
    head = num_text.split(".")[0]
    return "an" if head.startswith("8") or head in ("11", "18") or (len(head) in (5, 6) and head.startswith(("11", "18"))) else "a"


def last_result_phrase(entry, names):
    """From a team's log entry: 'coming off a 12.3-point win over X' / 'coming off a loss to X'."""
    margin = abs(entry["pf"] - entry["pa"])
    opp = names.get(entry["opp"], "their opponent")
    if entry["result"] == "W":
        if margin < 3:
            return f"coming off a thrilling {margin:.1f}-point win over {opp}"
        return f"coming off {a_an(f'{margin:.1f}')} {margin:.1f}-point win over {opp}"
    if entry["result"] == "L":
        return f"coming off {a_an(f'{margin:.1f}')} {margin:.1f}-point loss to {opp}"
    return f"coming off a tie with {opp}"


def last_name(full):
    parts = [x for x in full.split() if x.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV", "V")]
    return parts[-1] if parts else full


def swaps_text(swaps, n=2):
    """'Matthew Stafford (27.3) instead of Joe Burrow (24.6) and Emeka Egbuka (17.2) instead of Mike Gesicki (7.4)'."""
    return join_names([f"{a['name']} ({a['pts']:.1f}) instead of {b['name']} ({b['pts']:.1f})" for a, b in swaps[:n]])


def names_with_pts(players):
    return join_names([f"{p['name']} ({p['pts']:.1f})" for p in players])


def final_article(g, names, week, ctx):
    """The long write-up for a finished matchup, as a list of paragraphs.

    ctx: standings (after this week), scores (all scores this week), n_teams, last_kick, late_day,
         schedule (whole season), log (per-team results), regular_season_weeks, next_byes (NFL teams on bye next week)
    """
    w, l = g["sides"]
    W, L = names[w["team"]], names[l["team"]]
    late_day = ctx["late_day"]
    n = ctx["n_teams"]
    scores = sorted(ctx["scores"], reverse=True)
    rank_of = lambda pts: scores.index(pts) + 1
    seed = (week, w["team"], l["team"])
    paras = []

    # --- Lede: result, size, season series.
    m = g["margin"]
    size = ("a nail-biter" if m < 5 else "a close one" if m < 12 else "a solid win" if m < 25
            else "a comfortable win" if m < 40 else "a beatdown")
    lede = f"{W} beat {L} {w['pts']:.2f} to {l['pts']:.2f} in Week {week}, {size} decided by {m:.2f} points."
    if m >= 40:
        lede += " " + pick([f"{L} never stood a chance.", f"This one was over early.", f"{L} would like to forget this one."], *seed)
    elif m < 3:
        lede += " " + pick(["It doesn't get much closer than that.", "Every point mattered in this one."], *seed)
    earlier = [e for e in ctx["log"].get(w["team"], []) if e["opp"] == l["team"] and e["week"] < week]
    if earlier:
        e = earlier[-1]
        hi, lo = max(e["pf"], e["pa"]), min(e["pf"], e["pa"])
        if e["result"] == "T":
            lede += f" These two tied back in Week {e['week']}."
        elif e["result"] == "W":
            lede += f" {W} also won the first meeting back in Week {e['week']}, {hi:.2f} to {lo:.2f}, so they sweep the season series."
        else:
            lede += f" {L} won the first meeting back in Week {e['week']}, {hi:.2f} to {lo:.2f}, so the season series is split."
    paras.append(lede)

    # --- Winner: how good was the score, who did it, and was it lucky?
    wr = rank_of(w["pts"])
    lucky = n - wr + 1 <= 3
    txt = score_context(w, W, wr if not lucky else n // 2, n) or ""
    ws = sorted(starters(w), key=lambda p: -p["pts"])
    if ws:
        best = ws[0]
        share = share_phrase(best["pts"], w["pts"])
        txt += f" {best['name']} carried them with {best['pts']:.1f} points" + (f", {share} the team's total" if share else "")
        if best["proj"] and abs(best["pts"] - best["proj"]) >= 3:
            diff = best["pts"] - best["proj"]
            txt += f", {abs(diff):.1f} {'over' if diff > 0 else 'under'} his projection"
        txt += "."
        if len(ws) > 2:
            txt += f" {ws[1]['name']} ({ws[1]['pts']:.1f}) and {ws[2]['name']} ({ws[2]['pts']:.1f}) were the other big contributors."
    low_rank = n - wr + 1
    if lucky:
        txt += (f" It wasn't pretty: {w['pts']:.2f} was the {'lowest' if low_rank == 1 else ordinal(low_rank) + '-lowest'} score of the week. "
                + pick(["But a win is a win.", "They'll take it.", "Sometimes the schedule is kind."], *seed))
    paras.append(txt.strip())

    # --- Loser: their score in context, their best players, their biggest letdown.
    lr = rank_of(l["pts"])
    ls = sorted(starters(l), key=lambda p: -p["pts"])
    if ls:
        ctx_line = score_context(l, L, lr, n)
        txt = (ctx_line + " ") if ctx_line else ""
        txt += (f"{ls[0]['name']} led the way with {ls[0]['pts']:.1f}" if ctx_line else f"{ls[0]['name']} led {L} with {ls[0]['pts']:.1f}")
        txt += f", and {ls[1]['name']} added {ls[1]['pts']:.1f}." if len(ls) > 1 and ls[1]["pts"] >= 10 else "."
        if lr <= 3:
            txt += f" {L} scored the {'most' if lr == 1 else ordinal(lr) + '-most'} points in the league this week and still lost. Brutal."
        elif lr <= n // 2:
            txt += f" That would have beaten most teams this week. {L} just ran into {W}."
        paras.append(txt)

    # --- Biggest letdown, and whether there's an obvious replacement on the bench.
    for side, nm in ((l, L), (w, W)):
        bad = duds(side)
        if not bad:
            continue
        d = bad[0]
        txt = f"The letdown for {nm} was {d['name']}, who managed {d['pts']:.1f} on a {d['proj']:.1f} projection."
        backup = max((p for p in side["players"] if not p["starter"] and p["pos"] == d["pos"] and p["slot_id"] != 21),
                     key=lambda p: p["proj"] or 0, default=None)
        if backup and d["pos"] in ("QB", "TE", "K", "D/ST") and (backup["proj"] or 0) > 0:
            pos_word = {"QB": "quarterback", "TE": "tight end", "K": "kicker", "D/ST": "defense"}[d["pos"]]
            txt += (f" {nm} might have a {pos_word} decision to make if {last_name(d['name'])} doesn't figure it out:"
                    f" {backup['name']} is sitting right there on the bench" + (f" (and scored {backup['pts']:.1f} this week)." if backup["pts"] >= d["pts"] + 5 else "."))
        paras.append(txt)

    # --- The bench: name the real swaps the best lineup would have made.
    if l["optimal"] > w["pts"] and l["swaps"]:
        paras.append(f"Here's the painful part for {L}: starting {swaps_text(l['swaps'])} "
                     f"would have produced {l['optimal']:.2f} points, enough to win.")
    elif m >= 15:
        paras.append(f"Not much {L} could have done about this one: even their best possible lineup ({l['optimal']:.2f}) "
                     f"wouldn't have caught {W}.")
    elif l["swaps"] and l["optimal"] - l["pts"] >= 10:
        paras.append(f"{L} left {l['optimal'] - l['pts']:.2f} points on the bench, with {names_with_pts(l['should_start'][:2])} sitting, "
                     f"though even that wouldn't have been enough.")
    if w["swaps"] and w["optimal"] - w["pts"] >= 10:
        paras.append(f"{W} could have scored even more by starting {swaps_text(w['swaps'])}.")

    qbs = [next((p for p in starters(s) if p["pos"] == "QB"), None) for s in (w, l)]
    if all(qbs) and abs(qbs[0]["pts"] - qbs[1]["pts"]) >= 3:
        hi, lo = (qbs[0], qbs[1]) if qbs[0]["pts"] > qbs[1]["pts"] else (qbs[1], qbs[0])
        paras.append(f"At quarterback, {hi['name']} ({hi['pts']:.1f}) outdueled {lo['name']} ({lo['pts']:.1f}).")

    # --- Monday night, only when it mattered.
    if g["late"] in ("comeback", "held"):
        lk = ctx["last_kick"]
        w_late = [p for p in starters(w) if is_late(p, lk)]
        l_late = [p for p in starters(l) if is_late(p, lk)]
        if g["late"] == "comeback":
            txt = f"Going into {late_day} night, {W} trailed by {g['late_margin']:.2f} and needed a big night."
            if w_late:
                txt += f" {names_with_pts(w_late)} delivered"
                txt += f", while {L} got just {sum(p['pts'] for p in l_late):.1f} from {join_names([p['name'] for p in l_late])}." if l_late else "."
        else:
            txt = f"Going into {late_day} night, {W} led by {g['late_margin']:.2f}, and {L} still had {join_names([p['name'] for p in l_late])} to play."
            l_pts = sum(p["pts"] for p in l_late)
            if w_late:
                txt += f" {names_with_pts(l_late)} put in work, but {W}'s {names_with_pts(w_late)} more than answered."
            else:
                txt += f" {names_with_pts(l_late)} " + ("came up short." if l_pts < g["late_margin"] else "made it close.")
        paras.append(txt)

    # --- Standings.
    st = {r["team"]: r for r in ctx["standings"]}
    if w["team"] in st and l["team"] in st and not g["playoff"]:
        a, b = st[w["team"]], st[l["team"]]
        txt = f"{W} improves to {record_str(a)} ({ordinal(a['rank'])} place)"
        if a["streak"].startswith("W") and int(a["streak"][1:]) >= 3:
            txt += f", winners of {a['streak'][1:]} straight"
        txt += f". {L} drops to {record_str(b)} ({ordinal(b['rank'])})"
        if b["streak"].startswith("L") and int(b["streak"][1:]) >= 3:
            txt += f" and has now lost {b['streak'][1:]} in a row"
        paras.append(txt + ".")

    # --- Next week: opponents, their form, and bye-week holes.
    nxt = week + 1
    reg = ctx.get("regular_season_weeks")
    if not g["playoff"] and (not reg or nxt <= reg):
        for team, nm, side in ((w["team"], W, w), (l["team"], L, l)):
            game = next((x for x in ctx["schedule"] if x["week"] == nxt and team in (x["home"], x["away"])), None)
            if not game:
                continue
            opp = game["away"] if game["home"] == team else game["home"]
            o = st.get(opp)
            txt = f"Next week, {nm} faces {names.get(opp, 'TBD')}" + (f" ({record_str(o)})" if o else "")
            last = next((e for e in reversed(ctx["log"].get(opp, [])) if e["week"] == week), None)
            if last:
                txt += f", who are {last_result_phrase(last, names)}"
                if o and o["streak"] and o["streak"][0] in "WL" and int(o["streak"][1:]) >= 3:
                    txt += f" and have {'won' if o['streak'][0] == 'W' else 'lost'} {o['streak'][1:]} straight"
            txt += "."
            byes = [p for p in starters(side) if NFL_ABBR.get(p["nfl"], p["nfl"]) in ctx.get("next_byes", set())]
            if byes:
                txt += (f" {nm} will be without {join_names([p['name'] for p in byes])} (bye), "
                        + pick(["so expect some waiver-wire shopping.", "so it's time to check the waiver wire.",
                                "so the bench is about to get some work."], week, team))
            paras.append(txt)
    return paras


def join_names(items):
    """'A', 'A and B', 'A, B and C'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def live_article(g, names, late_day):
    a, b = g["sides"]  # a = leader, b = trailing
    A, B = names[a["team"]], names[b["team"]]
    night = f"{late_day} night" if late_day else "tonight"
    if not g["remaining"]:
        return [f"This one's in the books: {A} beat {B} {a['pts']:.2f} to {b['pts']:.2f}."]
    paras = [f"{A} leads {B} {a['pts']:.2f} to {b['pts']:.2f} heading into {night}."]
    margin = a["pts"] - b["pts"]
    for side, nm, trailing in ((b, B, True), (a, A, False)):
        left = sorted([p for p in starters(side) if not p["done"]], key=lambda p: -(p["proj"] or 0))
        if not left:
            continue
        proj = sum(p["proj"] or 0 for p in left)
        if len(left) == 1:
            p = left[0]
            txt = f"{nm} has one player left: {p['name']}" + (f", projected for {p['proj']:.1f} points." if p["proj"] else ".")
        else:
            txt = f"{nm} still has {join_names([p['name'] for p in left])} to play, projected for about {proj:.1f} points combined."
        if trailing:
            other = sum(p["proj"] or 0 for p in starters(a) if not p["done"])
            if proj > margin + other:
                txt += f" If {'he hits' if len(left) == 1 else 'they hit'} that, {nm} wins this one."
            elif proj > 0:
                txt += f" {nm} needs a big night: about {margin + other - proj:.1f} more than projected."
        paras.append(txt)
    star = max((p for s in (a, b) for p in starters(s) if not p["done"]), key=lambda p: p["proj"] or 0, default=None)
    if star:
        paras.append(f"Look out for {star['name']} on {night}. This one isn't over.")
    return paras


def ordinal(n):
    s = ["th", "st", "nd", "rd"]
    v = n % 100
    return f"{n}{s[(v - 20) % 10] if (v - 20) % 10 < 4 and v > 20 else s[v] if v < 4 else 'th'}"


# ---------- Issue (what the site shows for a week) ----------

def strip_players(side):
    side = dict(side)
    side["players"] = [{k: v for k, v in p.items() if k not in ("eligible", "slot_id", "kick")} for p in side["players"]]
    side["should_start"] = [p["name"] for p in side.get("should_start", [])]
    side["swaps"] = [[a["name"], b["name"]] for a, b in side.get("swaps", [])]
    return side


def build_issue(wk, weeks, league, note, live=False):
    names = {t["id"]: t["name"] for t in league["teams"]}
    week = wk["week"]
    last_kick = wk.get("last_kick")
    late_day = datetime.fromisoformat(last_kick).strftime("%A") if last_kick else "Monday"
    scores = [s["pts"] for m in wk["matchups"] for s in (m["home"], m["away"])]
    median = statistics.median(scores) if scores else 0
    snaps = league["snapshots"]
    standings = snaps.get(week) or (snaps[max(snaps)] if snaps else [])

    games = []
    for m in wk["matchups"]:
        a, b = m["home"], m["away"]
        w, l = (a, b) if a["pts"] >= b["pts"] else (b, a)
        margin = r2(w["pts"] - l["pts"])
        key = f"{min(a['team'], b['team'])}-{max(a['team'], b['team'])}"
        g = {"winner": w["team"], "loser": l["team"], "w_pts": w["pts"], "l_pts": l["pts"], "margin": margin,
             "note": (note.get("matchups") or {}).get(key), "playoff": m["playoff"], "sides": [w, l],
             "late": None, "late_margin": None}
        if live:
            # Players whose NFL game isn't over yet, with projections for what they might add.
            rem = [p for s in (w, l) for p in starters(s) if not p["done"]]
            g["remaining"] = len(rem)
            w_rem = sum(p["proj"] or 0 for p in starters(w) if not p["done"])
            l_rem = sum(p["proj"] or 0 for p in starters(l) if not p["done"])
            g["proj_margin"] = r2(margin + w_rem - l_rem)
            if not rem:
                g["headline"] = headline(names[w["team"]], names[l["team"]], margin, week)
                g["status"] = "final"
            elif l_rem >= margin:
                g["headline"] = f"Down to {late_day} night: {names[w['team']]} clings to a {margin:.1f}-point lead over {names[l['team']]}"
                g["status"] = "live"
            else:
                g["headline"] = f"{names[w['team']]} in control against {names[l['team']]}, up {margin:.1f}"
                g["status"] = "live"
            g["article"] = live_article(g, names, late_day)
        else:
            # Did it come down to the last game of the week (usually Monday night)?
            wl, ll = late_points(w, last_kick), late_points(l, last_kick)
            if wl or ll:
                before = r2((w["pts"] - wl) - (l["pts"] - ll))
                if before < 0:
                    g["late"], g["late_margin"] = "comeback", r2(-before)
                elif before < 15 and ll > 0:
                    g["late"], g["late_margin"] = "held", before
            g["headline"] = headline(names[w["team"]], names[l["team"]], margin, week, g["late"])
            for side in (w, l):
                projs = [p["proj"] for p in starters(side) if p["proj"] is not None]
                side["proj_total"] = r2(sum(projs)) if projs else None
            g["article"] = final_article(g, names, week, {
                "standings": standings, "scores": scores, "n_teams": len(names), "last_kick": last_kick,
                "late_day": late_day, "schedule": league.get("schedule", []), "log": league.get("log", {}),
                "regular_season_weeks": league.get("regular_season_weeks"),
                "next_byes": set(wk.get("next_byes", [])),
            })
        games.append(g)

    if live:
        # Lead with the tightest game that's still in doubt ("Down to Monday night"), then any game still going.
        in_doubt = [g for g in games if g["headline"].startswith("Down to")]
        open_games = [g for g in games if g["status"] == "live"]
        lead = (min(in_doubt, key=lambda g: g["margin"]) if in_doubt
                else min(open_games, key=lambda g: abs(g["proj_margin"])) if open_games
                else min(games, key=lambda g: g["margin"]))
        games.sort(key=lambda g: (not g["headline"].startswith("Down to"), g["status"] != "live", g["margin"]))
    else:
        games.sort(key=lambda g: g["margin"])
        lead = (next((g for g in games if g["late"] == "comeback"), None)
                or (games[0] if games and games[0]["margin"] < 5 else max(games, key=lambda g: g["w_pts"] + g["l_pts"])))
    for g in games:
        g["story"] = " ".join(g["article"][:2])

    awards = [] if live else make_awards(games, wk, names, median)
    watch = []
    if live:
        for g in games:
            for s in g["sides"]:
                watch += [{"name": p["name"], "pos": p["pos"], "nfl": p["nfl"], "proj": p["proj"], "team": s["team"],
                           "day": p["day"]} for p in starters(s) if not p["done"]]
        watch.sort(key=lambda x: -(x["proj"] or 0))

    prev = snaps.get(week - 1)
    prev_rank = {r["team"]: r["power_rank"] for r in prev} if prev else {}
    power = [{**r, "move": (prev_rank[r["team"]] - r["power_rank"]) if r["team"] in prev_rank else 0}
             for r in sorted(standings, key=lambda r: r["power_rank"])]

    out_games = [{**g, "sides": [strip_players(s) for s in g["sides"]]} for g in games]
    return {
        "week": week,
        "live": live,
        "late_day": late_day,
        "lead": games.index(lead),
        "headline": note.get("headline") or lead["headline"],
        "column": note.get("column"),
        "games": out_games,
        "awards": awards,
        "watch": watch,
        "median": r2(median),
        "average": r2(sum(scores) / len(scores)) if scores else 0,
        "standings": standings,
        "power": power,
    }


def make_awards(games, wk, names, median):
    sides = [s for m in wk["matchups"] for s in (m["home"], m["away"])]
    all_starters = [(p, s["team"]) for s in sides for p in starters(s)]
    win_ids = {g["winner"] for g in games}
    awards = []

    def award(key, title, team, value, detail):
        awards.append({"key": key, "title": title, "team": team, "value": value, "detail": detail})

    hi = max(sides, key=lambda s: s["pts"])
    lo = min(sides, key=lambda s: s["pts"])
    award("high", "High Score", hi["team"], f"{hi['pts']:.2f}", "Top score of the week.")
    award("low", "Basement Dweller", lo["team"], f"{lo['pts']:.2f}", "Lowest score of the week.")
    blow = max(games, key=lambda g: g["margin"])
    award("blowout", "Blowout of the Week", blow["winner"], f"+{blow['margin']:.2f}", f"Beat {names[blow['loser']]} by {blow['margin']:.2f}.")
    close = min(games, key=lambda g: g["margin"])
    award("close", "Nail-Biter", close["winner"], f"+{close['margin']:.2f}", f"Survived {names[close['loser']]} by {close['margin']:.2f}.")
    comeback = next((g for g in games if g["late"] == "comeback"), None)
    if comeback:
        award("comeback", "Monday Night Miracle", comeback["winner"], f"-{comeback['late_margin']:.2f}",
              f"Trailed {names[comeback['loser']]} by {comeback['late_margin']:.2f} going into the final game and won.")
    blunder = max(sides, key=lambda s: s["optimal"] - s["pts"])
    left = r2(blunder["optimal"] - blunder["pts"])
    if left > 0:
        lost_to = next((g for g in games if g["loser"] == blunder["team"]), None)
        cost = lost_to is not None and blunder["optimal"] > lost_to["w_pts"]
        award("bench", "Bench Blunder", blunder["team"], f"{left:.2f}", f"Points left on the bench{' — and it cost them the game' if cost else ''}.")
    best = max(sides, key=lambda s: s["pts"] / s["optimal"] if s["optimal"] else 0)
    if best["optimal"]:
        award("coach", "Coach of the Week", best["team"], f"{best['pts'] / best['optimal'] * 100:.0f}%", "Closest to a perfect lineup.")
    lucky = [s for s in sides if s["team"] in win_ids and s["pts"] < median]
    if lucky:
        s = min(lucky, key=lambda s: s["pts"])
        award("lucky", "Lucky Win", s["team"], f"{s['pts']:.2f}", f"Won with a below-median score (median {median:.2f}).")
    unlucky = [s for s in sides if s["team"] not in win_ids and s["pts"] > median]
    if unlucky:
        s = max(unlucky, key=lambda s: s["pts"])
        award("unlucky", "Tough Luck", s["team"], f"{s['pts']:.2f}", f"Lost despite an above-median score (median {median:.2f}).")
    if all_starters:
        mvp, mt = max(all_starters, key=lambda x: x[0]["pts"])
        award("mvp", "Player of the Week", mt, f"{mvp['pts']:.1f}", f"{mvp['name']} ({mvp['pos']}, {mvp['nfl']})")
        projected = [(p, t) for p, t in all_starters if p["proj"] and p["proj"] >= 8]
        if projected:
            dud, dt = min(projected, key=lambda x: x[0]["pts"] - x[0]["proj"])
            if dud["pts"] - dud["proj"] <= -6:
                award("dud", "Dud of the Week", dt, f"{dud['pts']:.1f}", f"{dud['name']} ({dud['pos']}) on a {dud['proj']:.1f} projection.")
    return awards
