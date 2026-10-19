# The Gridiron Gazette

*All the news that's fit to bench.* A weekly newspaper for our family ESPN fantasy football league, in the spirit of the old CBS Sports weekly wrap-ups.

Each finished week becomes an **issue**:
- **Front page**: the game of the week (the closest game, or the highest-scoring one), with a box score
- **Weekly awards**: High Score, Basement Dweller, Blowout, Nail-Biter, Bench Blunder, Coach of the Week, Lucky Win, Tough Luck, Player of the Week, Dud of the Week
- **Around the League**: a story and box score for every other matchup
- **Scoreboard** and **Power Rankings** with week-to-week movement

Every headline opens the **full story**: who carried each team, the duds, the bench, the quarterback duel, Monday night drama and what it means for the standings.

While a week is still being played, it gets a **live issue**: current scores, a "Down to Monday night" headline when a game is still in doubt, a **Monday Night Watch** list of players still to play, and previews like "look out for George Kittle tonight." Once the week ends, the final issue calls out **Monday night comebacks** and games that came down to the last game.

Plus **Standings** (with all-play records) and a page for every **team**.

## How it works
`scripts/update.py` reads the league from ESPN's (unofficial) fantasy API and `scripts/recap.py` turns each finished week into `site/data/weeks/<week>.json`. The site in `site/` is plain HTML/CSS/JS that reads those files.

A GitHub Action (`.github/workflows/publish.yml`) runs **Monday morning** (live issue: Sunday's games in, Monday night still to play) and **Tuesday morning** (final issue), saves the issues to the repo and publishes the site to GitHub Pages. The NFL schedule from [nflverse](https://github.com/nflverse/nflverse-data) tells it which players have played and which game was on Monday night. Run it any time from **Actions → Publish the Gazette → Run workflow**.

## One-time setup
1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → New repository secret**, twice:
   - `ESPN_S2`: the `espn_s2` cookie from fantasy.espn.com
   - `ESPN_SWID`: the `SWID` cookie (with the curly braces)

   (Lock icon left of the address bar → Cookies → espn.com → Cookies → copy each one's Content.) These stay hidden; they're never in the code.
3. Run the workflow once from the Actions tab.

The site is at **https://joshwilliams332.github.io/gridiron-gazette/**.

## Customizing
- `gazette.json`: league ID, your team, a custom league name, and optionally owners' names (`"owners": {"3": "Josh"}`).
- `notes.json`: the commissioner's desk. Per week, set a `headline` (replaces the front-page headline), a `column` (your own write-up at the top of the issue), or notes on individual games. Commit the change and the site updates.

## Running it on your computer
```sh
cp scripts/espn_cookies.example.json scripts/espn_cookies.json   # then paste in your cookies
python3 scripts/update.py
python3 -m http.server -d site 8000                                # open http://localhost:8000
```
`scripts/espn_cookies.json` is git-ignored, so your cookies never get uploaded.
