"""
Mirror scraper health from public/events_stats.json into GitHub issues.

One open issue per unhealthy store, titled "[health] <store>" and labelled
`scraper-health`:
  - a store whose scraper errored or is flagged suspect_zero / stale gets an issue
    (created if missing, body refreshed if already open);
  - an open issue whose store is healthy again gets a comment and is closed.

Bodies are refreshed in place rather than commented on, so a store that
stays broken does not produce a notification every day.

    python -m tools.sync_health_issues            # needs `gh` + GH_TOKEN
    python -m tools.sync_health_issues --dry-run  # print the plan only
"""

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Callable, Optional

STATS_FILE = pathlib.Path("public/events_stats.json")
LABEL = "scraper-health"
TITLE_PREFIX = "[health] "


def unhealthy_stores(stats: dict) -> dict[str, dict]:
    """store -> {reason, module, error, raw, active} for every broken store."""
    module_by_store = {
        ms.get("store"): (name, ms) for name, ms in (stats.get("scrapers") or {}).items()
    }
    out: dict[str, dict] = {}

    for name, ms in (stats.get("scrapers") or {}).items():
        if ms.get("error"):
            row = (stats.get("by_store") or {}).get(ms.get("store")) or {}
            out[ms.get("store") or name] = {
                "reason": "scraper_failed",
                "module": name,
                "error": ms["error"],
                "raw": ms.get("raw", 0),
                "active": row.get("active", 0),
            }

    # Multi-store sources (official locators) have no by_store row of their
    # own, so their suspect_zero flag lives only on the scraper entry.
    for name, ms in (stats.get("scrapers") or {}).items():
        store = ms.get("store") or name
        if store not in out and ms.get("anomaly") == "suspect_zero":
            out[store] = {
                "reason": "suspect_zero",
                "module": name,
                "error": None,
                "raw": ms.get("raw", 0),
                "active": 0,
            }

    for store, row in (stats.get("by_store") or {}).items():
        if store in out or row.get("anomaly") not in ("suspect_zero", "stale"):
            continue
        name, ms = module_by_store.get(store, (None, {}))
        out[store] = {
            "reason": row["anomaly"],
            "module": name,
            "error": None,
            "raw": row.get("raw_this_run", 0),
            "active": row.get("active", 0),
        }
    # A scraper with no record at all in by_store has never produced a
    # single event since it was added: broken from day one.
    for name, ms in (stats.get("scrapers") or {}).items():
        store = ms.get("store") or name
        if store in out or store in (stats.get("by_store") or {}) or ms.get("raw"):
            continue
        out[store] = {
            "reason": "never_seen",
            "module": name,
            "error": None,
            "raw": 0,
            "active": 0,
        }
    return out


def known_stores(stats: dict) -> set[str]:
    stores = {ms.get("store") for ms in (stats.get("scrapers") or {}).values()}
    return {s for s in stores if s}


def issue_body(store: str, info: dict, generated_at: Optional[str]) -> str:
    reason = {
        "scraper_failed": "the scraper raised an error",
        "suspect_zero": "the scraper returned 0 events while upcoming events are still on record",
        "stale": "the scraper has returned 0 events for 14+ days (site changed or store stopped posting?)",
        "never_seen": "the scraper has never produced a single event",
    }[info["reason"]]
    lines = [
        f"**{store}**: {reason}.",
        "",
        f"- Module: `{info['module'] or 'unknown'}`",
        f"- Raw events this run: {info['raw']}",
        f"- Active events kept on record: {info['active']}",
        f"- Last checked: {generated_at or 'unknown'}",
    ]
    if info.get("error"):
        lines += ["", "```", str(info["error"])[:2000], "```"]
    if info["reason"] in ("scraper_failed", "suspect_zero"):
        lines += ["", "Existing events for this store are preserved until the scraper recovers."]
    lines += [
        "",
        "This issue is updated by the daily `Update events` workflow and closes "
        "automatically once the store is healthy again.",
    ]
    return "\n".join(lines)


def plan_actions(stats: dict, open_issues: list[dict]) -> list[dict]:
    """
    Pure decision step. open_issues: [{"number": int, "title": str}, ...].
    Returns [{"op": "create"|"update"|"close", ...}].
    """
    broken = unhealthy_stores(stats)
    generated_at = stats.get("generated_at")
    by_title = {i["title"]: i for i in open_issues}
    actions: list[dict] = []

    for store in sorted(broken):
        title = TITLE_PREFIX + store
        body = issue_body(store, broken[store], generated_at)
        if title in by_title:
            actions.append({"op": "update", "number": by_title[title]["number"], "body": body})
        else:
            actions.append({"op": "create", "title": title, "body": body})

    healthy = known_stores(stats) - set(broken)
    for title, issue in sorted(by_title.items()):
        store = title[len(TITLE_PREFIX):]
        if title.startswith(TITLE_PREFIX) and store in healthy:
            actions.append({
                "op": "close",
                "number": issue["number"],
                "comment": f"Recovered: {store} is healthy again as of {generated_at}.",
            })
    return actions


def _gh(args: list[str]) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True,
    ).stdout


def apply_actions(actions: list[dict], gh: Callable[[list[str]], str] = _gh) -> None:
    for a in actions:
        if a["op"] == "create":
            gh(["issue", "create", "--title", a["title"], "--body", a["body"], "--label", LABEL])
        elif a["op"] == "update":
            gh(["issue", "edit", str(a["number"]), "--body", a["body"]])
        elif a["op"] == "close":
            gh(["issue", "close", str(a["number"]), "--comment", a["comment"]])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))

    if args.dry_run:
        open_issues: list[dict] = []
    else:
        _gh(["label", "create", LABEL, "--color", "d93f0b",
             "--description", "Automated scraper health alerts", "--force"])
        open_issues = json.loads(_gh([
            "issue", "list", "--label", LABEL, "--state", "open",
            "--limit", "200", "--json", "number,title",
        ]))

    actions = plan_actions(stats, open_issues)
    for a in actions:
        target = a.get("title") or f"#{a['number']}"
        print(f"{a['op']:>6}  {target}")
    if not args.dry_run:
        apply_actions(actions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
