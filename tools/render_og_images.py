"""
Render the Open Graph preview images (1200×630) used when a link is shared
in WhatsApp/Telegram/social: one per game plus a default.

    python -m tools.render_og_images

Run by hand when a game or the design changes; the PNGs in public/og/ are
committed (the daily pipeline never re-renders them, so they cost no churn).
Text only — no publisher logos or artwork.
"""

import pathlib

from playwright.sync_api import sync_playwright

from pipeline.pages import GAME_SLUGS

OUT = pathlib.Path("public/og")
ICON = pathlib.Path("public/icons/icon.svg").read_text(encoding="utf-8")

ACCENTS = {
    "Magic: The Gathering": "#f97316", "Star Wars: Unlimited": "#2563eb",
    "Riftbound": "#0891b2", "One Piece": "#dc2626", "Pokémon": "#ca8a04",
    "Lorcana": "#9333ea", "Digimon": "#16a34a", "Yu-Gi-Oh!": "#db2777",
    "Flesh and Blood": "#7f1d1d", "Final Fantasy TCG": "#4f46e5",
    "Weiß Schwarz": "#4b5563", "Naruto Mythos": "#475569",
}
DEFAULT_ACCENT = "#5b21b6"

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;width:1200px;height:630px;}}
  body{{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;color:#fff;
       background:linear-gradient(135deg,{accent} 0%,#1e1b2e 100%);
       display:flex;flex-direction:column;justify-content:space-between;padding:64px 72px;box-sizing:border-box}}
  .top{{display:flex;align-items:center;gap:20px;font-size:30px;font-weight:600;opacity:.95}}
  .top svg{{width:64px;height:64px}}
  h1{{font-size:{size}px;line-height:1.05;margin:0;font-weight:800;letter-spacing:-1px}}
  .sub{{font-size:38px;margin-top:18px;opacity:.9}}
  .foot{{font-size:26px;opacity:.75}}
</style></head><body>
  <div class="top">{icon}<span>TCG Madrid</span></div>
  <div><h1>{title}</h1><div class="sub">{subtitle}</div></div>
  <div class="foot">madrid-tcg.pages.dev · todos los torneos, todas las tiendas</div>
</body></html>"""


def render() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cards = [("default", "Torneos de cartas en Madrid", "Magic, Pokémon, One Piece, Lorcana y más", DEFAULT_ACCENT)]
    cards += [(slug, game, "Torneos en Madrid", ACCENTS.get(game, DEFAULT_ACCENT))
              for game, slug in GAME_SLUGS.items()]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        for slug, title, subtitle, accent in cards:
            size = 104 if len(title) <= 16 else 84 if len(title) <= 24 else 72
            page.set_content(TEMPLATE.format(accent=accent, icon=ICON, title=title,
                                             subtitle=subtitle, size=size))
            page.screenshot(path=str(OUT / f"{slug}.jpg"), type="jpeg", quality=82)
            print(slug)
        browser.close()


if __name__ == "__main__":
    render()
