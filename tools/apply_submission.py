"""
Turn an approved submission issue into an entry of data/manual_events.json.

    python -m tools.apply_submission <issue-number> < issue-body.md

Run by .github/workflows/submissions.yml when a `submission` issue gets
the `approved` label. Reads the ```json block the /api/submit function
wrote, validates it again, and appends it (idempotent per issue number).
Exit code 2 = nothing to apply (store-info request, or invalid data).
"""

import json
import pathlib
import re
import sys
from datetime import date, datetime

DATA_FILE = pathlib.Path("data/manual_events.json")
GAMES = {
    "Magic: The Gathering", "Pokémon", "One Piece", "Digimon", "Lorcana",
    "Star Wars: Unlimited", "Yu-Gi-Oh!", "Flesh and Blood", "Riftbound",
    "Weiß Schwarz", "Final Fantasy TCG", "Naruto Mythos",
}


def extract(body: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if not m:
        raise ValueError("no ```json block in the issue body")
    return json.loads(m.group(1))


def to_entry(sub: dict, issue: int) -> dict:
    if sub.get("kind") != "event":
        raise ValueError("store-info requests are applied by hand")
    date.fromisoformat(sub["date"])
    datetime.strptime(sub["time"], "%H:%M")
    if sub.get("weekly_until"):
        if date.fromisoformat(sub["weekly_until"]) < date.fromisoformat(sub["date"]):
            raise ValueError("weekly_until is before date")
    link = sub.get("link") or None
    if link and not re.match(r"^https?://", link, re.I):
        link = None
    return {
        "id": str(issue), "store": sub["store"].strip(), "title": sub["title"].strip(),
        "game": sub["game"] if sub.get("game") in GAMES else None,
        "date": sub["date"], "time": sub["time"],
        "weekly_until": sub.get("weekly_until") or None, "link": link,
        "added_at": date.today().isoformat(),
    }


def apply(body: str, issue: int, path: pathlib.Path = DATA_FILE) -> bool:
    """Append the submission; False if this issue was already applied."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if any(e["id"] == str(issue) for e in data["events"]):
        return False
    data["events"].append(to_entry(extract(body), issue))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    try:
        added = apply(sys.stdin.read(), int(argv[1]))
    except (ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"Not applied: {exc}")
        return 2
    print("Added." if added else "Already applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
