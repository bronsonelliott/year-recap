"""The commands the skill runs. Use: python3 recap.py COMMAND --help

  scan         what history exists and how big the job is (reads no messages)
  candidates   likely automated runs and git emails, for the person to confirm
  collect      remove secrets, count, condense, and write the month batches
  status       where a run has got to
  read         mark a batch as read, after its notes are saved
  build        check story.json and draw the page
  review       check review.json and show the list to go through before sharing
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import condense
import page
import sources
from condense import load, save, working_folder


def fail(message):
    sys.exit("Stopped: " + message)


def get_range(args):
    """The dates to cover, or a question for the person."""
    try:
        span = sources.resolve_range(date.today(), args.year)
    except ValueError as problem:
        fail(str(problem))
    if span["ask"]:
        print("ASK: It's January, so this year has only just started. Cover %d instead?" % span["suggest"])
        print("Then run again with --year %d, or --year %d for this year so far."
              % (span["suggest"], span["suggest"] + 1))
        sys.exit(0)
    return span


def get_folder(args):
    """The working folder of a run that has already been collected."""
    span = get_range(args)
    folder = working_folder(args.home, span["year"])
    if not (folder / "run.json").exists():
        fail("there is no run for %d yet. Run scan, ask the person, then run candidates." % span["year"])
    return folder


def scan(args):
    span = get_range(args)
    found = sources.scan(args.home, span)
    print("Covers %s to %s (%s)" % (span["start"], span["end"], span["label"]))
    print("Claude Code sessions: %d %s" % (found["code"]["sessions"], json.dumps(found["code"]["months"], sort_keys=True)))
    print("Cowork sessions: %d %s" % (found["cowork"]["sessions"], json.dumps(found["cowork"]["months"], sort_keys=True)))
    print("Typed prompts: %d" % found["prompts"]["prompts"])
    totals = found["totals"]
    print("Daily totals: %s" % ("up to %s" % totals["last"] if totals["found"] else "none"))
    if not (found["code"]["sessions"] or found["cowork"]["sessions"] or found["prompts"]["prompts"]):
        print("There is no Claude Code history for these dates on this computer, so there is nothing to recap.")
        return
    print("Estimated reading: about %d,000 tokens of the person's Claude plan (rough)"
          % max(1, round(found["estimated_tokens"] / 1000)))
    days = found["retention_days"]
    print("Transcript retention: %d days%s" % (days, " (Claude Code's default)" if days == 30 else ""))
    print("Nothing has been written. Ask the person to confirm before going on.")
    print("Add --year %d to every command from here, so a pause past New Year still works." % span["year"])


def candidates(args):
    span = get_range(args)
    sessions, _ = sources.read_sessions(args.home, span)
    prompts, _ = sources.read_prompts(args.home, span)
    found = condense.find_candidates(sessions, args.home)
    flagged = {c["key"] for c in found}
    groups = condense.group_sessions(sessions, args.home)
    mine = [s for key, g in groups.items() if key not in flagged for s in g["sessions"]]
    folders = {s["folder"] for s in mine if s["source"] == "code"} | {p["folder"] for p in prompts}
    repositories = sources.find_repositories(args.home, folders)
    failed = []
    commits = sources.read_all_commits(args.home, repositories, span, failed)
    emails = sources.likely_own_emails(args.home, repositories, commits)
    jobs = condense.find_commit_candidates([c for c in commits if c["email"] in emails])

    folder = working_folder(args.home, span["year"])
    condense.make_private(folder.parent)
    condense.make_private(folder)
    run = load(folder / "run.json", {})
    listed = {"e%d" % n: email for n, email in enumerate(sorted(emails), 1)}
    if "batches" not in run:
        run["state"] = "Confirmed"
    run.update(year=span["year"], candidates={c["id"]: c["session_ids"] for c in found},
               commit_candidates={c["id"]: c["shas"] for c in jobs}, email_choices=listed)
    save(folder / "run.json", run)

    print("Likely automated runs: %d" % len(found))
    for c in found:
        print("  %s: %d sessions in %s (%s). First words, quoted from history: %s"
              % (c["id"], c["sessions"], c["folder"], ", ".join(c["reasons"]), json.dumps(c["first_words"])))
    print("Likely automated commits: %d" % len(jobs))
    for c in jobs:
        print("  %s: %d commits in %s with the same wording. Example, quoted from history: %s"
              % (c["id"], c["commits"], c["repository"], json.dumps(c["example"])))
    print("Git emails that look like theirs: %d" % len(emails))
    for key, email in listed.items():
        print("  %s: %s, %d commits%s" % (key, email, emails[email]["commits"],
                                      "" if emails[email]["sure"] else " (not certain, ask)"))
    if failed:
        print("Repositories git couldn't read, so their commits are missing: %d" % len(failed))
    print("Ask the person which runs and commits were automated and which emails are theirs, then run collect.")


def split_list(text):
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def collect(args):
    span = get_range(args)
    folder = get_folder(args)
    run = load(folder / "run.json")
    known = run.get("candidates") or {}
    jobs = run.get("commit_candidates") or {}
    chosen = split_list(args.automated)
    emails = run.get("email_choices") or {}
    own = split_list(args.emails)
    # An email the person types is accepted too, if it is shaped like one and nothing else.
    unknown = [c for c in chosen if c not in known and c not in jobs]
    unknown += [e for e in own if e not in emails and not sources.EMAIL.match(e.lower())]
    if unknown:
        fail("nothing on the confirmed list is called %s." % ", ".join(unknown))
    missing = [n for n in args.notes or [] if not Path(n).expanduser().exists()]
    if missing:
        fail("these notes don't exist: %s" % ", ".join(missing))
    run["state"] = "ListConfirmed"
    save(folder / "run.json", run)
    automated = [session for c in chosen for session in known.get(c, [])]
    made_by_jobs = [sha for c in chosen for sha in jobs.get(c, [])]
    run = condense.collect(args.home, span, automated, [emails.get(e, e) for e in own], args.notes or [], made_by_jobs)
    size = sum(b["characters"] for b in run["batches"])
    if run["stats"] < 5:
        fail("there is too little history left for a recap (only %d facts, and a page needs 5). "
             "Tell the person, and offer to count some runs as theirs after all." % run["stats"])
    print("Working folder: %s" % folder)
    for name in run["skipped_notes"]:
        print("Skipped, not readable as text: %s" % name)
    if run["unreadable_repositories"]:
        print("Repositories git couldn't read: %d" % run["unreadable_repositories"])
    print("Batches to read: %d, about %d,000 tokens" % (len(run["batches"]), max(1, round(size / 4000))))
    print("Left out as automated: %d sessions" % run["automated_sessions"])
    if run["untyped_sessions"]:
        print("Sessions where nothing was typed: %d" % run["untyped_sessions"])
    if run["unreadable_lines"]:
        print("Lines that couldn't be read: %d" % run["unreadable_lines"])
    status(args)


def status(args):
    folder = get_folder(args)
    run = load(folder / "run.json")
    print("State: %s" % run["state"])
    if "batches" not in run:
        print("Next: collect")
        return
    lost = condense.missing_notes(folder, run)
    if lost:
        print("Notes missing for batches marked read: %s. Read these again." % ", ".join(lost))
    left = condense.unread(run)
    print("Unread batches, %d of %d: %s" % (len(left), len(run["batches"]), ", ".join(left) if left else "none"))
    print("Next: %s" % ("read %s" % (folder / "batches" / (left[0] + ".md")) if left else "write the story"))


def read(args):
    folder = get_folder(args)
    left = condense.mark_read(folder, args.batch)
    print("Unread batches, %d: %s" % (len(left), ", ".join(left) if left else "none. Write the story."))


def build(args):
    folder = get_folder(args)
    run = load(folder / "run.json")
    if "batches" not in run:
        fail("there is nothing to build from yet. Run collect first.")
    lost = condense.missing_notes(folder, run)
    if lost:
        fail("notes are missing for %s, so the story can't be trusted. Read those batches again." % ", ".join(lost))
    problems = page.build(folder)
    if problems:
        fail("the story needs fixing:\n  " + "\n  ".join(problems))
    print("Page: %s" % (folder / "recap.html"))
    print("If it will be published as an artifact, use: %s" % (folder / "recap-page.html"))


def review(args):
    folder = get_folder(args)
    story = load(folder / "story.json")
    entries = load(folder / "review.json")
    if entries is None or story is None or not (folder / "recap.html").exists():
        fail("build the page and write review.json in %s first." % folder)
    if load(folder / "run.json").get("built_story") != page.fingerprint(story):
        fail("the story changed after the page was built. Run build again first.")
    problems = page.check_review(entries, story)
    if problems:
        fail("the review list needs fixing:\n  " + "\n  ".join(problems))
    entries = entries + page.credential_entries(story, page.relabeled(load(folder / "evidence.json"), story))
    print("Check before sharing: %d places" % len(entries))
    for entry in entries:
        print("  %s | %s | %s" % (entry["place"], entry["category"], entry["what"]))
    run = load(folder / "run.json")
    run["state"] = "Reviewed"
    save(folder / "run.json", run)
    print("Working folder: %s" % folder)
    print("To delete everything this run saved: rm -r \"%s\"" % folder)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, action in (("scan", scan), ("candidates", candidates), ("collect", collect), ("status", status),
                         ("read", read), ("build", build), ("review", review)):
        command = commands.add_parser(name)
        command.set_defaults(action=action)
        command.add_argument("--year", type=int, help="a year to cover; the default is this year so far")
        command.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
        if name == "collect":
            command.add_argument("--automated", help="automated runs and commits to leave out, such as a1,a3,c1")
            command.add_argument("--emails", help="the person's own git emails, such as e1,e2")
            command.add_argument("--notes", nargs="*", help="notes or changelog files and folders to include")
        if name == "read":
            command.add_argument("batch", help="the batch just read, such as 2026-03-1")
    args = parser.parse_args(arguments)
    os.umask(0o077)      # anything written is readable only by this person
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")      # names this terminal can't show
    try:
        args.action(args)
    except condense.RecapError as problem:
        fail(str(problem))


if __name__ == "__main__":
    main()
