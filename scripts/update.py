#!/usr/bin/env python3
"""Pull the league from ESPN and write the data files the Gazette is built from.

    python scripts/update.py              # current season
    python scripts/update.py --season 2025

Reads gazette.json (league id, names) and the ESPN login cookies from
scripts/espn_cookies.json (never committed) or the ESPN_S2 / ESPN_SWID
environment variables (GitHub secrets in the automatic update).

Writes:
    site/data/league.json      league info, teams, standings, power rankings
    site/data/weeks/<n>.json   one "issue" per finished week: scores, stories, awards
Uses only the Python standard library.
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import recap  # noqa: E402

CONFIG = ROOT / "gazette.json"
COOKIES = ROOT / "scripts" / "espn_cookies.json"
OUT = ROOT / "site" / "data"
NFL_SCHEDULE = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"


def load_cookies():
    cookies = {}
    if COOKIES.exists():
        cookies = json.loads(COOKIES.read_text())
    cookies["espn_s2"] = os.environ.get("ESPN_S2") or cookies.get("espn_s2")
    cookies["swid"] = os.environ.get("ESPN_SWID") or cookies.get("swid")
    return cookies


def espn_get(season, league_id, cookies, params):
    url = API.format(season=season, league=league_id) + "?" + "&".join(params)
    headers = {"User-Agent": "gridiron-gazette", "Accept": "application/json"}
    if cookies.get("espn_s2") and cookies.get("swid"):
        headers["Cookie"] = f"espn_s2={cookies['espn_s2']}; SWID={cookies['swid']}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(
                "ESPN says the league is private, or the cookies are wrong or expired.\n"
                "Put fresh espn_s2 and SWID values in scripts/espn_cookies.json "
                "(see scripts/espn_cookies.example.json)."
            ) from e
        raise


def nfl_schedule():
    """nflverse's NFL schedule, used to tell which players have played and which games were on Monday night."""
    try:
        req = urllib.request.Request(NFL_SCHEDULE, headers={"User-Agent": "gridiron-gazette"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))
    except Exception as e:  # the Gazette still works without it, minus the Monday-night stories
        print(f"  WARNING: couldn't load the NFL schedule ({e})")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int)
    ap.add_argument("--fixture", help="read ESPN responses from a local JSON file instead of ESPN (for testing)")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    today = date.today()
    season = args.season or cfg.get("season") or (today.year if today.month >= 3 else today.year - 1)
    league_id = os.environ.get("ESPN_LEAGUE_ID") or cfg["league_id"]
    cookies = load_cookies()

    if args.fixture:
        fixture = json.loads(Path(args.fixture).read_text())
        get_league = lambda: fixture["league"]
        get_week = lambda w: fixture["weeks"][str(w)]
    else:
        get_league = lambda: espn_get(season, league_id, cookies,
                                      ["view=mTeam", "view=mSettings", "view=mStatus", "view=mMatchup"])
        get_week = lambda w: espn_get(season, league_id, cookies,
                                      [f"scoringPeriodId={w}", "view=mMatchupScore", "view=mBoxscore"])

    print(f"Updating The Gridiron Gazette: league {league_id}, {season}")
    league_raw = get_league()
    league = recap.parse_league(league_raw, cfg)
    finished = recap.finished_weeks(league_raw)
    live_week = recap.current_week(league_raw) if cfg.get("live_issues") else None
    print(f"  {league['name']}: {len(league['teams'])} teams, {len(finished)} finished weeks"
          + (f", week {live_week} in progress" if live_week else ""))
    schedule = fixture.get("nfl", []) if args.fixture else nfl_schedule()

    weeks = []
    for w in finished:
        print(f"  week {w}")
        weeks.append(recap.parse_week(get_week(w), w, league, recap.nfl_week(schedule, season, w)))
    live = None
    if live_week:
        print(f"  week {live_week} (live)")
        live = recap.parse_week(get_week(live_week), live_week, league, recap.nfl_week(schedule, season, live_week))

    recap.add_season_context(league, weeks)
    notes = json.loads((ROOT / "notes.json").read_text()) if (ROOT / "notes.json").exists() else {}
    week_notes = notes.get("weeks", {})

    (OUT / "weeks").mkdir(parents=True, exist_ok=True)
    for wk in weeks:
        issue = recap.build_issue(wk, weeks, league, week_notes.get(str(wk["week"]), {}))
        (OUT / "weeks" / f"{wk['week']}.json").write_text(json.dumps(issue, indent=1))
    if live:
        issue = recap.build_issue(live, weeks, league, week_notes.get(str(live["week"]), {}), live=True)
        (OUT / "weeks" / f"{live['week']}.json").write_text(json.dumps(issue, indent=1))
    # Remove issues that are no longer published (e.g. an old live issue).
    keep = {f"{w['week']}.json" for w in weeks} | ({f"{live['week']}.json"} if live else set())
    for f in (OUT / "weeks").glob("*.json"):
        if f.name not in keep:
            f.unlink()
    league.pop("snapshots", None)  # already copied into each issue
    league["weeks"] = [w["week"] for w in weeks] + ([live["week"]] if live else [])
    league["live_week"] = live["week"] if live else None
    league["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    (OUT / "league.json").write_text(json.dumps(league, indent=1))
    print(f"Done: wrote {len(weeks)} issues" + (f" plus the live Week {live['week']} issue" if live else ""))


if __name__ == "__main__":
    main()
