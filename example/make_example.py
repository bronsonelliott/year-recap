"""Draws example/recap.html. Pat and everything here are invented."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "year-recap" / "scripts"))

import page  # noqa: E402
import sources  # noqa: E402
from condense import save  # noqa: E402
from datetime import date  # noqa: E402

YEAR = sources.resolve_range(date(2026, 11, 20))
MONTHS = sources.months_in(YEAR)
PROMPTS_ONLY = ("2026-01", "2026-02", "2026-03")


def stat(value, caption, months=MONTHS):
    return {"value": value, "caption": caption, "months": [m for m in months if m != "2026-07"]}


STATS = {
    "messages": stat("2,316", "things you asked or said"),
    "active_days": stat("143", "days you worked with Claude"),
    "longest_streak": stat("12 days", "longest run of days in a row"),
    "busiest_hour": stat("6 am", "your most common hour"),
    "busiest_month": stat("May", "your busiest month, with 402 messages"),
    "top_projects": stat([["garden/planner", "640"], ["garden/pond", "388"], ["recipes", "251"],
                          ["club-newsletter", "190"], ["home folder", "84"]], "where you spent your messages"),
}
LABELS = {m: "none" if m == "2026-07" else "partial" if m in PROMPTS_ONLY else "full" for m in MONTHS}
EVIDENCE = {
    "P0001": {"kind": "prompts", "date": "2026-01-11", "project": "garden", "title": "9 typed prompts, no transcript"},
    "P0014": {"kind": "prompts", "date": "2026-02-22", "project": "garden", "title": "14 typed prompts, no transcript"},
    "S0003": {"kind": "session", "date": "2026-04-04", "project": "garden/planner", "title": "Plan every bed at once"},
    "S0011": {"kind": "session", "date": "2026-05-09", "project": "garden/planner", "title": "Start over with one bed"},
    "S0019": {"kind": "session", "date": "2026-06-13", "project": "garden/pond", "title": "Pond liner arithmetic"},
    "S0027": {"kind": "session", "date": "2026-08-30", "project": "club-newsletter", "title": "Newsletter template"},
    "S0035": {"kind": "session", "date": "2026-10-18", "project": "recipes", "title": "Put recipes in one file"},
    "C0002": {"kind": "commit", "date": "2026-05-10", "project": "planner", "title": "One bed, start to finish"},
    "C0009": {"kind": "commit", "date": "2026-09-02", "project": "newsletter", "title": "First issue from the template"},
    "N0001": {"kind": "note", "date": "2026-07-28", "project": "notes", "title": "garden-log.md"},
}
RUN = {"label": YEAR["label"], "start": YEAR["start"].isoformat(), "end": YEAR["end"].isoformat(),
       "batches": [{"name": m + "-1", "read": True} for m in MONTHS if m != "2026-07"], "unreadable_lines": 3}


def make(folder):
    save(folder / "run.json", RUN)
    save(folder / "stats.json", {"stats": STATS, "labels": LABELS})
    save(folder / "evidence.json", EVIDENCE)
    save(folder / "story.json", json.loads((HERE / "story.json").read_text(encoding="utf-8")))
    return page.build(folder)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        problems = make(Path(temporary))
        if problems:
            sys.exit("\n".join(problems))
        (HERE / "recap.html").write_text((Path(temporary) / "recap.html").read_text(encoding="utf-8"), encoding="utf-8")
        print("Drew", HERE / "recap.html")
