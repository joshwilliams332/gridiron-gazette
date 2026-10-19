// Shared by every Gazette page: masthead, footer, data loading and helpers.

const GZ = {
  cache: {},
  async load(path) {
    if (!this.cache[path]) {
      this.cache[path] = fetch(`data/${path}`, { cache: "no-cache" }).then(r => {
        if (!r.ok) throw new Error(`Couldn't load data/${path}`);
        return r.json();
      });
    }
    return this.cache[path];
  },

  chrome(league, subtitle) {
    const here = location.pathname.split("/").pop() || "index.html";
    const link = (href, label) => `<a href="${href}" ${here === href ? 'aria-current="page"' : ""}>${label}</a>`;
    document.body.insertAdjacentHTML("afterbegin", `
      <header class="masthead">
        <div class="top-line"><span>${esc(league?.name || "")}</span>
          <span class="top-right"><span class="season">${league?.season ? `${league.season} Season` : ""}</span>
          <button class="theme-toggle" type="button"></button></span></div>
        <h1><a href="index.html">The Gridiron Gazette</a></h1>
        <p class="motto">All the news that's fit to bench</p>
        <div class="dateline">
          <span>${esc(subtitle || "")}</span>
          <nav>${link("index.html", "Latest Issue")}${link("standings.html", "Standings")}${link("teams.html", "Teams")}</nav>
        </div>
      </header>`);
    this.wireTheme();
    document.body.insertAdjacentHTML("beforeend", `
      <footer class="site-footer">The Gridiron Gazette · scores from ESPN Fantasy${league?.updated ? ` · updated ${esc(new Date(league.updated).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}` : ""}</footer>`);
  },

  // Light/dark toggle. The page's <head> applies a saved choice before drawing, so there's no flash.
  wireTheme() {
    const btn = document.querySelector(".theme-toggle");
    const root = document.documentElement;
    const current = () => root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const label = () => {
      const dark = current() === "dark";
      btn.textContent = dark ? "☀️ Light" : "🌙 Dark";
      btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    };
    btn.onclick = () => {
      root.dataset.theme = current() === "dark" ? "light" : "dark";
      try { localStorage.setItem("gz-theme", root.dataset.theme); } catch (e) { /* storage blocked */ }
      label();
    };
    label();
  },

  async start(subtitleFn, fn) {
    const main = document.querySelector("main");
    let league = null;
    try {
      league = await this.load("league.json");
    } catch (e) {
      this.chrome(null, "");
      main.innerHTML = `<div class="notice"><strong>No issues yet.</strong> Run <code>python scripts/update.py</code> to pull the league from ESPN.</div>`;
      return;
    }
    const team = Object.fromEntries(league.teams.map(t => [t.id, t]));
    this.chrome(league, subtitleFn ? subtitleFn(league) : "");
    try {
      await fn(main, league, team);
    } catch (e) {
      console.error(e);
      main.innerHTML = `<div class="notice">Something went wrong loading this page. ${esc(e.message)}</div>`;
    }
  },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function f2(n) { return (n ?? 0).toFixed(2); }
function teamLink(team, id) {
  const t = team[id];
  return t ? `<a href="team.html?id=${id}">${esc(t.name)}</a>${t.owner ? ` <span class="muted small">(${esc(t.owner)})</span>` : ""}` : "?";
}
function record(r) { return `${r.w}-${r.l}${r.t ? `-${r.t}` : ""}`; }
function moveTag(m) {
  if (!m) return `<span class="move">–</span>`;
  return m > 0 ? `<span class="move up">▲${m}</span>` : `<span class="move down">▼${-m}</span>`;
}
function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
