"""Turn history into statistics, evidence IDs and month batches Claude can read.

Everything is written to the working folder, ~/.claude-year-recap/<year>/:
  run.json        what the person confirmed, and which batches have been read
  stats.json      the numbers, each with the months it covers
  evidence.json   what each evidence ID stands for
  batches/        condensed history, one file per month (or part of a month)
  notes/          Claude's notes, one file per batch
"""
import hashlib
import json
import os
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path

import sources
from redact import redact

MESSAGE_LIMIT = 600          # characters kept from each thing the person typed
REPLY_LIMIT = 1200           # characters kept from a session's last reply
BATCH_LIMIT = 28000          # characters in one batch; under the limit of each tool Claude reads with
BURST = 15                   # sessions in one folder on one day
REPEATS = 5                  # sessions sharing the same opening words
TEMPORARY = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class RecapError(Exception):
    """Something the person or Claude can fix, said in plain words."""


# --- the working folder ------------------------------------------------------

def working_folder(home, year):
    return Path(home) / ".claude-year-recap" / str(year)


def make_private(folder):
    """Create a folder only its owner can open."""
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(str(folder), 0o700)


def save(path, data):
    """Write through a second file, so an interrupted run can't leave half a file."""
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(partial), str(path))


def load(path, otherwise=None):
    if not path.exists():
        return otherwise
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as problem:
        raise RecapError("%s isn't valid JSON (%s)." % (path, problem))


# --- likely automated runs ---------------------------------------------------

def _is_temporary(folder, home):
    place = folder + "/"
    return place.startswith(TEMPORARY) and not place.startswith(str(home) + "/")


def group_sessions(sessions, home):
    """{group: {"sessions", "reasons"}} for sessions where something was typed.

    Groups with no reasons are the person's own work. Whole folders are kept
    together when possible, so the list the person confirms stays short.
    """
    groups = {}

    def put(key, members, *reasons):
        group = groups.setdefault(key, {"sessions": [], "reasons": []})
        group["sessions"] += members
        group["reasons"] += [r for r in reasons if r not in group["reasons"]]

    folders = {}
    for session in (s for s in sessions if s["messages"]):
        folder = session["folder"] or session["project"]
        if _is_temporary(folder, home):
            put("temporary folders", [session], "in a temporary folder")
        elif session["launch"].startswith("sdk"):
            put(folder + " | started by a script", [session], "started by a script")
        else:
            folders.setdefault(folder, []).append(session)

    def opening(session):
        """The first words, unless all they say is that something was pasted."""
        words = session["opening"][:60]
        return "" if words == "[pasted text]" else words

    others = []
    for folder, members in folders.items():
        days = Counter(s["first"].date() for s in members)
        in_bursts = sum(n for n in days.values() if n >= BURST)
        openings = Counter(opening(s) for s in members if opening(s))
        repeated = sum(n for n in openings.values() if n >= REPEATS)
        reasons = []
        if in_bursts * 2 >= len(members):
            reasons.append("up to %d sessions in one day" % max(days.values()))
        if repeated * 2 >= len(members):
            reasons.append("%d of %d open with repeated words" % (repeated, len(members)))
        if reasons:
            put(folder, members, *reasons)
        else:
            others += [(folder, s) for s in members]

    # Compared across folders, because a test can give every run its own folder.
    openings = Counter(opening(s) for _, s in others if opening(s))
    for folder, session in others:
        words = opening(session)
        if openings[words] >= REPEATS:
            # The key is saved, so it holds a fingerprint and not the words themselves.
            mark = hashlib.sha256(words.encode("utf-8")).hexdigest()[:12]
            put("opens with: " + mark, [session], "%d sessions open with the same words" % openings[words])
        else:
            put(folder, [session])
    return groups


def find_candidates(sessions, home):
    """Groups that look automated, each with the reasons, for the person to confirm."""
    candidates = []
    for key, group in sorted(group_sessions(sessions, home).items()):
        if group["reasons"]:
            first = group["sessions"][0]
            projects = {s["project"] for s in group["sessions"]}
            place = key if key == "temporary folders" else \
                field(first["project"]) if len(projects) == 1 else "several folders"
            candidates.append({"id": "a%d" % (len(candidates) + 1), "key": key,
                               "sessions": len(group["sessions"]), "reasons": group["reasons"],
                               "session_ids": [s["id"] for s in group["sessions"]],
                               "folder": place,
                               "first_words": field(first["opening"], 80)})
    return candidates


REPEATED_COMMITS = 10        # commits in one repository with the same wording


def find_commit_candidates(commits):
    """Commits that look like a scheduled job's: the same words, with only numbers changing."""
    groups = {}
    for commit in commits:
        wording = re.sub(r"\d+", "#", commit["subject"].lower())[:40]
        groups.setdefault((commit["path"], wording), []).append(commit)
    candidates = []
    for (_, wording), members in sorted(groups.items()):
        if len(members) >= REPEATED_COMMITS:
            candidates.append({"id": "c%d" % (len(candidates) + 1), "commits": len(members),
                               "shas": [c["sha"] for c in members],
                               "repository": field(members[0]["repository"]),
                               "example": field(members[0]["subject"], 80)})
    return candidates


# --- the numbers ---------------------------------------------------------------

def longest_streak(days):
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def clock(hour):
    return "%d %s" % (hour % 12 or 12, "am" if hour < 12 else "pm")


def plural(number, word):
    return "{:,} {}{}".format(number, word, "" if number == 1 else "s")


def make_stats(mine, automated, prompts, commits):
    """Every number the page may show. Code fills these in; Claude only picks them."""
    # A forked session starts with a copy of the first one's messages, so each counts once.
    said = {(m["time"], m["text"]): s["project"] for s in mine for m in s["messages"]}
    asked = [(when, project) for (when, _), project in said.items()]
    asked += [(p["time"], p["project"]) for p in prompts]
    # Commits don't count as days: a scheduled job can commit without the person.
    days = {when.date() for when, _ in asked}
    stats = {}

    def add(name, value, caption, times):
        months = sorted({t.strftime("%Y-%m") for t in times})
        if months:
            stats[name] = {"value": value, "caption": caption, "months": months}

    times = [when for when, _ in asked]
    add("messages", "{:,}".format(len(asked)), "things you asked or said", times)
    add("sessions", "{:,}".format(len(mine)), "sessions with surviving transcripts", [s["first"] for s in mine])
    add("active_days", "{:,}".format(len(days)), "days you worked with Claude", times)
    add("longest_streak", plural(longest_streak(days), "day"), "longest run of days in a row", times)
    if asked:
        hour = Counter(t.hour for t in times).most_common(1)[0][0]
        add("busiest_hour", clock(hour), "your most common hour", times)
        weekday = Counter(t.weekday() for t in times).most_common(1)[0][0]
        add("busiest_weekday", WEEKDAYS[weekday], "your busiest day of the week", times)
        day, count = Counter(t.date() for t in times).most_common(1)[0]
        add("busiest_day", "{} {}".format(day.strftime("%B"), day.day),
            "your busiest day, with " + plural(count, "message"), times)
        month, count = Counter(t.strftime("%Y-%m") for t in times).most_common(1)[0]
        add("busiest_month", month_name(month), "your busiest month, with " + plural(count, "message"), times)
        late = [t for t in times if t.hour >= 22 or t.hour < 5]
        add("late_night", "{:,}".format(len(late)), "messages between 10 pm and 5 am", late)
        projects = Counter(p for _, p in asked)
        add("projects", "{:,}".format(len(projects)), "different folders you worked in", times)
        add("top_projects", [[redact(name), "{:,}".format(n)] for name, n in projects.most_common(5)],
            "the folders where you spent your messages", times)
    if mine:
        longest = max(mine, key=lambda s: (len(s["messages"]), s["id"]))
        add("longest_session", "{:,}".format(len(longest["messages"])),
            "messages in your longest conversation", [longest["first"]])
    add("commits", "{:,}".format(len(commits)), "commits of your own", [c["time"] for c in commits])
    if automated:
        add("automated", "{:,}".format(len(automated)), "automated runs, counted separately",
            [s["first"] for s in automated])
    return stats


def month_name(month):
    names = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
    return names[int(month[5:]) - 1]


def label_months(span, mine, prompts, commits, notes, totals):
    """How much evidence survives for each month.

    "full" means transcripts hold at least half of what the person typed that
    month. A month with one transcript and hundreds of bare prompts is partial.
    """
    told = Counter(m["time"].strftime("%Y-%m") for s in mine for m in s["messages"])
    bare = Counter(p["time"].strftime("%Y-%m") for p in prompts)
    full = {month for month in told if told[month] >= bare[month]}
    partial = {x["time"].strftime("%Y-%m") for x in list(prompts) + list(commits) + list(notes)}
    partial |= set(totals["months"]) | set(told)
    return {m: "full" if m in full else "partial" if m in partial else "none"
            for m in sources.months_in(span)}


# --- condensing ----------------------------------------------------------------

def shorten(text, limit):
    """Remove secrets from the whole text first, then trim it."""
    text = redact(text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " [trimmed]"


def field(text, limit=120):
    """Text for a heading line: no secrets, one line, trimmed, so it can't start a new item."""
    return shorten(" ".join("".join(c if c.isprintable() else " " for c in redact(text)).split()), limit)


def quoted(text):
    """Mark text as quoted material, so it reads as data and not as instructions."""
    return "\n".join("> " + line for line in text.splitlines()) or "> "


def build_items(mine, prompts, commits, notes):
    """Condensed items as (month, evidence ID, text), plus what each ID stands for."""
    items = []
    evidence = {}
    seen = set()         # a forked session starts with a copy of another's messages

    for number, session in enumerate(mine, 1):
        key = "S%04d" % number
        title = field(session["title"])
        evidence[key] = {"kind": "session", "date": session["first"].date().isoformat(),
                         "project": field(session["project"]), "title": title}
        for month in session["months"]:
            said = [m for m in session["messages"] if m["time"].strftime("%Y-%m") == month
                    and (m["time"], m["text"]) not in seen]
            seen.update((m["time"], m["text"]) for m in said)
            lines = ["### %s | session | %s | %s | %s" % (
                key, session["first"].date(), field(session["project"]), title)]
            lines += [quoted(shorten(m["text"], MESSAGE_LIMIT)) for m in said]
            if month == session["months"][-1] and session["last_reply"]:
                lines += ["Claude's last reply:", quoted(shorten(session["last_reply"], REPLY_LIMIT))]
            if len(lines) > 1:
                items.append((month, key, "\n".join(lines)))

    by_day = {}
    for prompt in prompts:
        by_day.setdefault(prompt["time"].date(), []).append(prompt)
    for number, day in enumerate(sorted(by_day), 1):
        key = "P%04d" % number
        projects = Counter(p["project"] for p in by_day[day])
        evidence[key] = {"kind": "prompts", "date": day.isoformat(),
                         "project": field(projects.most_common(1)[0][0]),
                         "title": "%d typed prompts, no transcript" % len(by_day[day])}
        lines = ["### %s | prompts only | %s" % (key, day)]
        lines += [quoted("(%s) %s" % (field(p["project"]), shorten(p["text"], MESSAGE_LIMIT))) for p in by_day[day]]
        items.append((day.strftime("%Y-%m"), key, "\n".join(lines)))

    for number, commit in enumerate(commits, 1):
        key = "C%04d" % number
        subject = field(commit["subject"], 200)
        evidence[key] = {"kind": "commit", "date": commit["time"].date().isoformat(),
                         "project": field(commit["repository"]), "title": subject}
        items.append((commit["time"].strftime("%Y-%m"), key, "%s | commit | %s | %s | %s" % (
            key, commit["time"].date(), field(commit["repository"]), subject)))

    for number, note in enumerate(notes, 1):
        key = "N%04d" % number
        evidence[key] = {"kind": "note", "date": note["time"].date().isoformat(), "project": "notes",
                         "title": field(note["title"])}
        items.append((note["time"].strftime("%Y-%m"), key, "### %s | note | %s | %s\n%s" % (
            key, note["time"].date(), field(note["title"]), quoted(shorten(note["text"], REPLY_LIMIT)))))
    return items, evidence


def split_item(text, limit):
    """One long item as several, each starting with the item's own heading."""
    if len(text) <= limit:
        return [text]
    heading, *lines = text.split("\n")
    pieces = [[heading]]
    size = len(heading)
    for line in lines:
        if len(pieces[-1]) > 1 and size + len(line) > limit:
            pieces.append([heading + " (continued)"])
            size = len(heading)
        pieces[-1].append(line)
        size += len(line) + 1
    return ["\n".join(piece) for piece in pieces]


def split_batches(items, limit=BATCH_LIMIT):
    """{"2026-03-1": text}. A month over the limit becomes several parts.

    Each part ends with a line saying so, to show it was read to the end.
    """
    batches = {}
    for month in sorted({m for m, _, _ in items}):
        parts = [[]]
        size = 0
        for text in (piece for m, _, t in items if m == month for piece in split_item(t, limit)):
            if parts[-1] and size + len(text) > limit:
                parts.append([])
                size = 0
            parts[-1].append(text)
            size += len(text)
        for number, texts in enumerate(parts, 1):
            name = "%s-%d" % (month, number)
            batches[name] = "\n\n".join(texts) + "\n\nEND OF BATCH %s\n" % name
    return batches


def set_aside(path):
    """Keep an outdated file under another name instead of using or deleting it."""
    if path.exists():
        path.replace(path.with_name(path.stem + "-previous" + path.suffix))


def set_aside_old_notes(folder, before, batches):
    """Move notes for batches that changed or are gone, since their evidence IDs may have moved."""
    old = {b["name"]: b["mark"] for b in before.get("batches", [])}
    new = {b["name"]: b["mark"] for b in batches}
    for path in (folder / "notes").glob("*.md"):
        if path.stem not in new or old.get(path.stem) != new[path.stem]:
            make_private(folder / "notes-previous")
            path.replace(folder / "notes-previous" / path.name)


def collect(home, span, automated_ids, emails, note_paths, automated_commits=()):
    """Condense the year into the working folder and return the manifest.

    `automated_ids` are the sessions the person confirmed were automated, and
    `automated_commits` the commits.
    """
    folder = working_folder(home, span["year"])
    make_private(folder.parent)
    for place in (folder, folder / "batches", folder / "notes"):
        make_private(place)

    sessions, broken = sources.read_sessions(home, span)
    prompts, broken_prompts = sources.read_prompts(home, span)
    automated = set(automated_ids) & {s["id"] for s in sessions if s["messages"]}
    mine = [s for s in sessions if s["messages"] and s["id"] not in automated]
    with_transcript = {s["id"] for s in sessions if s["messages"]}
    folders = {s["folder"] for s in mine if s["source"] == "code"} | {p["folder"] for p in prompts}
    prompts = [dict(p, project=sources.project_name(p["folder"], home))
               for p in prompts if p["session"] not in with_transcript]
    repositories = sources.find_repositories(home, folders)
    wanted = {e.lower() for e in emails}
    failed = []
    commits = [c for c in sources.read_all_commits(home, repositories, span, failed) if c["email"] in wanted]
    made_by_jobs = set(automated_commits)
    commits = [c for c in commits if c["sha"] not in made_by_jobs]
    notes, skipped_notes = sources.read_notes(note_paths, span)
    totals = sources.read_totals(home, span)

    labels = label_months(span, mine, prompts, commits, notes, totals)
    items, evidence = build_items(mine, prompts, commits, notes)
    batches = split_batches(items)
    stats = make_stats(mine, [s for s in sessions if s["id"] in automated], prompts, commits)

    before = load(folder / "run.json", {})
    read_before = {b["name"]: b for b in before.get("batches", []) if b.get("read")}
    for old in (folder / "batches").glob("*.md"):
        if old.stem not in batches:
            old.unlink()
    manifest_batches = []
    for name in sorted(batches, key=lambda n: (n[:7], int(n[8:]))):
        mark = hashlib.sha256(batches[name].encode("utf-8")).hexdigest()
        (folder / "batches" / (name + ".md")).write_text(batches[name], encoding="utf-8")
        same = read_before.get(name, {}).get("mark") == mark
        manifest_batches.append({"name": name, "characters": len(batches[name]), "mark": mark, "read": same})

    set_aside_old_notes(folder, before, manifest_batches)
    marks = [b["mark"] for b in manifest_batches]
    if marks != [b["mark"] for b in before.get("batches", [])]:
        # Evidence IDs may have moved, so a story written for the old ones can't be used.
        set_aside(folder / "story.json")
        set_aside(folder / "review.json")
    save(folder / "stats.json", {"stats": stats, "labels": labels})
    save(folder / "evidence.json", evidence)
    run = dict(before, state="Collected", year=span["year"], start=span["start"].isoformat(),
               end=span["end"].isoformat(), label=span["label"], automated=sorted(automated),
               emails=sorted(wanted), notes=[str(p) for p in note_paths], batches=manifest_batches,
               unreadable_lines=broken + broken_prompts, skipped_notes=skipped_notes,
               unreadable_repositories=len(failed), stats=len(stats), automated_sessions=len(automated),
               untyped_sessions=sum(1 for s in sessions if not s["messages"]))
    save(folder / "run.json", run)
    return run


# --- reading progress ------------------------------------------------------------

def notes_file(folder, name):
    return folder / "notes" / (name + ".md")


def mark_read(folder, name):
    """Record a batch as read. Its notes must be saved first, so a pause loses nothing."""
    run = load(folder / "run.json")
    batch = next((b for b in run.get("batches", []) if b["name"] == name), None)
    if batch is None:
        raise RecapError("There is no batch called %s." % name)
    path = notes_file(folder, name)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise RecapError("Save notes to %s before marking %s read." % (path, name))
    batch["read"] = True
    run["state"] = "Reading"
    save(folder / "run.json", run)
    return unread(run)


def unread(run):
    return [b["name"] for b in run["batches"] if not b["read"]]


def missing_notes(folder, run):
    """Batches marked read whose notes are gone. The story can't start until this is empty."""
    return [b["name"] for b in run["batches"] if b["read"]
            and not (notes_file(folder, b["name"]).exists()
                     and notes_file(folder, b["name"]).read_text(encoding="utf-8").strip())]
