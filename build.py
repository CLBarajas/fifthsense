"""Build the public, view-only copy of the Rochester charts for GitHub Pages.

    python3 build.py --clone ../curling_stats

Reads the viewer from the clone at games.json's `rev` (git show, so the
working tree's state doesn't matter) and each game's timeline from the
clone's `out/<run>/`, then writes, beside this script:

    index.html          the game list
    viewer/             app.js, style.css and the font, shared by every game
    <slug>/index.html   the viewer page, booted view-only
    <slug>/timeline.json
    .nojekyll           Pages serves the files as they are
    CNAME               the site's own domain, when games.json names one

Every game page sets window.CHART = {mode: "view"}, the hosted service's
view-only link: no editor, no save button, and nothing is ever POSTed.
window.SITE = {read_only: true} hides the sign-in features (I played,
Teams, Flag). Two things the hosted mode would otherwise do are handled here:

- The header's name links to /games, the hosted game list. Here the list is
  one folder up, so a small script points that link at "../".
- A game analysed in the last 21 days offers "Clip…", which asks the hosted
  clips API for a video. There is none here, so `source.analysed_at` is left
  out of the published timeline and the button never shows.

Timelines are written compact (the run's are indented): same data, about a
quarter of the size.
"""

import argparse
import datetime as dt
import html
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEWER = "src/curling_score/viewer"
ASSETS = ("app.js", "style.css", "source-sans-3.woff2", "source-sans-3.OFL.txt")
ANCHOR = "<!--CHART-->"
PER_END = 16

# The header's name is a link to /games on the hosted site. MutationObserver,
# not a one-off fix: the header is re-rendered when the layout changes
# (phone, tablet, wide), and each new one carries the hosted href again.
LINK_FIX = """new MutationObserver(function () {
  document.querySelectorAll('h1 a[href="/games"]').forEach(function (a) {
    a.setAttribute("href", "../"); a.title = "All charted games";
  });
}).observe(document.documentElement, { childList: true, subtree: true });"""

# The wide layout's house card says "Click the ice to add a stone, drag to
# move..." under the house in view mode too (App.jsx shows it on !desk alone);
# here nobody can, so it goes.
VIEW_CSS = '<style>body[data-mode="view"] #houseCard > .muted { display: none; }</style>'


def git(clone, *args):
    return subprocess.run(["git", "-C", str(clone), *args], check=True,
                          capture_output=True).stdout


def replace_once(text, old, new, what):
    if text.count(old) != 1:
        raise SystemExit(f"index.html: expected one {what} ({old!r}), found {text.count(old)}")
    return text.replace(old, new)


def team_name(teams, colour):
    return ((teams or {}).get(colour) or {}).get("name")


def stone_names(doc):
    names = {"red": "Red", "yellow": "Yellow"}
    for c, s in (doc.get("stones") or {}).items():
        if s.get("name"):
            names[c] = s["name"]
    return names


def build_game(game, doc, page, site_name, site, rev_short):
    g = doc["games"][0]
    if "teams" in game:
        g["teams"] = {c: {"name": n} for c, n in game["teams"].items()}
    doc.get("source", {}).pop("analysed_at", None)

    out = HERE / game["slug"]
    out.mkdir(exist_ok=True)
    (out / "timeline.json").write_text(json.dumps(doc, separators=(",", ":")))
    # The page asks for the game's corrections before it renders. There are
    # none; an empty map answers that without a 404 in the console.
    (out / "overrides.json").write_text("{}\n")

    names = stone_names(doc)
    red, yellow = (team_name(g["teams"], c) or names[c] for c in ("red", "yellow"))
    sheet = doc["source"].get("sheet") or "?"
    day = dt.date.fromisoformat(game["date"])
    title = f"{red} v {yellow}, Sheet {sheet}, {day:%b} {day.day} · {site_name}"

    boot = (f"<script>\nwindow.CHART = {json.dumps({'mode': 'view', 'slug': game['slug']})};\n"
            f"window.SITE = {json.dumps(site)};\n{LINK_FIX}\n</script>")
    text = page
    text = replace_once(text, "<title>curling.dimmit.net</title>",
                        f"<title>{html.escape(title)}</title>\n"
                        f'<meta name="generator" content="curling_stats rochester-ny {rev_short}, '
                        f'run {html.escape(game["run"])}">', "title")
    text = replace_once(text, 'href="style.css">', f'href="../viewer/style.css">\n{VIEW_CSS}',
                        "stylesheet")
    text = replace_once(text, 'src="app.js"', 'src="../viewer/app.js"', "script")
    text = replace_once(text, ANCHOR, boot, "CHART anchor")
    (out / "index.html").write_text(text)

    found = sum(1 for e in g["ends"] for s in e.get("shots") or [] if not s.get("missing"))
    return {
        "slug": game["slug"], "day": day, "sheet": sheet, "league": game.get("league", ""),
        "note": game.get("note", ""), "red": team_name(g["teams"], "red"),
        "yellow": team_name(g["teams"], "yellow"), "names": names,
        "colours": {c: (doc.get("stones") or {}).get(c, {}).get("color") for c in ("red", "yellow")},
        "ends": len(g["ends"]), "found": found, "slots": PER_END * len(g["ends"]),
        "video": doc["source"].get("url"),
    }


def card(c):
    def team(colour):
        name = c[colour] or c["names"][colour]
        swatch = c["colours"][colour]
        style = f' style="--stone:{swatch}"' if swatch else ""
        return (f'<span class="team"><span class="stone {colour}"{style}></span>'
                f'{html.escape(name)}</span>')

    unnamed = "" if (c["red"] and c["yellow"]) else '<span class="soon">team names to come</span>'
    note = f'<p class="note">{html.escape(c["note"])}</p>' if c["note"] else ""
    return f"""
    <li><a class="game" href="{c['slug']}/">
      <p class="when">{c['day']:%a %b} {c['day'].day} · Sheet {c['sheet']} · {html.escape(c['league'])}</p>
      <p class="teams">{team('red')} <span class="v">v</span> {team('yellow')} {unnamed}</p>
      <p class="stats">{c['ends']} ends · {c['found']} of {c['slots']} rocks tracked</p>
      {note}
    </a></li>"""


def index_page(cards, site_name, tagline, rev_short, built):
    items = "".join(card(c) for c in cards)
    site_name, tagline = html.escape(site_name), html.escape(tagline)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{site_name} · {tagline}</title>
<meta name="description" content="{site_name}: rock-by-rock charts of games at Rochester Curling Club, tracked by computer vision from the club’s live streams.">
<meta name="generator" content="curling_stats rochester-ny {rev_short}">
<style>
@font-face {{ font-family: "Source Sans 3"; font-weight: 200 900; font-display: swap;
             src: url("viewer/source-sans-3.woff2") format("woff2"); }}
:root {{
  --bg: #f3f0e7; --panel: #fffdf7; --ink: #16130c; --muted: #6d6455; --rule: #ddd5c3;
  --teal: #3f8577; --purple: #6a5a94; --red: #d13438; --yellow: #4d8ef0;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #17140f; --panel: #211c14; --ink: #f2ece0; --muted: #a2977f; --rule: #3a3226;
           --teal: #6cbcac; --purple: #a596cf; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
       font: 17px/1.5 "Source Sans 3", system-ui, -apple-system, sans-serif;
       -webkit-text-size-adjust: 100%; }}
main {{ max-width: 680px; margin: 0 auto;
       padding: max(28px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right))
                48px max(16px, env(safe-area-inset-left)); }}
h1 {{ font-size: 30px; line-height: 1.15; margin: 0 0 12px; letter-spacing: -.01em; }}
h1 {{ font-size: 36px; margin-bottom: 4px; }}
.tagline {{ font-size: 18px; font-weight: 600; color: var(--teal); margin: 0 0 18px; }}
h2 {{ font-size: 15px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
     color: var(--muted); margin: 36px 0 8px; }}
p {{ margin: 0 0 12px; }}
a {{ color: var(--teal); }}
.lede {{ font-size: 19px; }}
ul.games {{ list-style: none; padding: 0; margin: 20px 0 0; display: grid; gap: 12px; }}
a.game {{ display: block; text-decoration: none; color: inherit; background: var(--panel);
         border: 1px solid var(--rule); border-left: 4px solid var(--purple); border-radius: 10px;
         padding: 14px 16px; }}
a.game:hover, a.game:focus-visible {{ border-left-color: var(--teal); outline: none;
         box-shadow: 0 0 0 2px var(--teal); }}
a.game p {{ margin: 0; }}
.when {{ font-size: 14px; color: var(--muted); }}
.teams {{ font-size: 21px; font-weight: 700; margin: 2px 0 !important; }}
.team {{ white-space: nowrap; }}
.v {{ font-weight: 400; color: var(--muted); padding: 0 2px; }}
.stone {{ display: inline-block; width: .62em; height: .62em; border-radius: 50%;
         margin-right: .3em; vertical-align: .06em; background: var(--stone, var(--muted));
         box-shadow: 0 0 0 2px var(--panel), 0 0 0 3px var(--rule); }}
.stone.red {{ --stone: var(--red); }}
.soon {{ font-size: 14px; font-weight: 400; color: var(--muted); white-space: nowrap; }}
.stats {{ font-size: 15px; }}
.note {{ font-size: 14px; color: var(--muted); margin-top: 4px !important; }}
ul.how {{ padding-left: 20px; margin: 0; }}
ul.how li {{ margin-bottom: 6px; }}
footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--rule);
         font-size: 14px; color: var(--muted); }}
footer a {{ color: inherit; }}
</style>
<main>
  <h1>{site_name}</h1>
  <p class="tagline">{tagline}</p>
  <p class="lede">Using computer vision models customized for Rochester Curling Club’s cameras, {site_name} finds and tracks stones from the club’s live streams. Every rock tracked is charted: where it stopped, when it was thrown, and what kind of shot it most likely was.</p>
  <p>Pick a game. Step through it a rock at a time, with the stream beside it.</p>
  <p class="note">Want a game tracked? Get in touch with Chris Barajas.</p>

  <ul class="games">{items}
  </ul>

  <h2>Reading a game</h2>
  <ul class="how">
    <li>The house shows every stone in play after the rock you’re on, and the path of the rock that was thrown.</li>
    <li>Step with the arrow keys or the arrows under the video. On a phone, tap ‹ and ›, or swipe across the house.</li>
    <li>The video jumps to each rock as you step.</li>
    <li>Change ends from the menu at the top. On a phone, tap the rock’s name above the house.</li>
    <li>A “?” is a rock that couldn’t be tracked.</li>
    <li>Report gives the score by end and each team’s thinking time.</li>
  </ul>

  <h2>How good is it?</h2>
  <p>Close, but not perfect. Stones in the house are placed from the overhead cameras. Guards are placed from the end cameras, which is harder: some are a foot or more out. Shot types are a best guess from each rock’s path.</p>
  <p>Nothing here has been corrected by hand. Feedback is welcome.</p>

  <h2>Where it comes from</h2>
  <p>{site_name} is built on <a href="https://github.com/wdimmit/curling_stats">curling_stats</a>, the charting software Will Dimmit wrote for Granite Curling Club in Seattle and made public. It was adapted for Rochester Curling Club’s streams. This isn’t an official club site.</p>

  <footer>
    Built on <a href="https://github.com/wdimmit/curling_stats">curling_stats</a> by Will Dimmit.
    Source: <a href="https://github.com/CLBarajas/curling_stats/tree/rochester-ny">CLBarajas/curling_stats, branch rochester-ny</a>.
    Last updated: {built:%b} {built.day}, {built:%Y}.
  </footer>
</main>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clone", type=Path, default=HERE.parent / "curling_stats",
                    help="the curling_stats clone holding the viewer and out/<run>/")
    a = ap.parse_args()

    cfg = json.loads((HERE / "games.json").read_text())
    site_name = cfg["site_name"]
    site = {"name": site_name, "read_only": True}
    rev = git(a.clone, "rev-parse", cfg["rev"]).decode().strip()
    rev_short = rev[:7]

    (HERE / "viewer").mkdir(exist_ok=True)
    for name in ASSETS:
        (HERE / "viewer" / name).write_bytes(git(a.clone, "show", f"{rev}:{VIEWER}/{name}"))
    page = git(a.clone, "show", f"{rev}:{VIEWER}/index.html").decode()

    slugs = {g["slug"] for g in cfg["games"]}
    for d in HERE.iterdir():
        if d.is_dir() and (d / "timeline.json").is_file() and d.name not in slugs:
            shutil.rmtree(d)
            print(f"removed {d.name}/ (no longer in games.json)")

    cards = []
    for game in cfg["games"]:
        doc = json.loads((a.clone / "out" / game["run"] / "timeline.json").read_text())
        c = build_game(game, doc, page, site_name, site, rev_short)
        cards.append(c)
        print(f"{c['slug']}/  {c['found']}/{c['slots']} rocks, "
              f"{(HERE / c['slug'] / 'timeline.json').stat().st_size // 1024} KB")

    (HERE / "index.html").write_text(
        index_page(cards, site_name, cfg.get("tagline", ""), rev_short, dt.date.today()))
    (HERE / ".nojekyll").write_text("")
    # GitHub Pages reads the site's own domain from a CNAME file on the
    # published branch. Written here so a rebuild can't drop it; with no
    # "domain" in games.json the site stays at <user>.github.io/<repo>/.
    domain = (cfg.get("domain") or "").strip()
    if domain:
        (HERE / "CNAME").write_text(domain + "\n")
    else:
        (HERE / "CNAME").unlink(missing_ok=True)

    leaks = [p for p in HERE.rglob("*") if p.is_file() and ".git" not in p.parts
             and p.suffix in (".html", ".json") and b"/Users/" in p.read_bytes()]
    if leaks:
        raise SystemExit(f"a local path is in {', '.join(map(str, leaks))}")
    print(f"viewer {rev_short}; {len(cards)} games")


if __name__ == "__main__":
    main()
