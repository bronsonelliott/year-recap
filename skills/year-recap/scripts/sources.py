"""Find and read the history a recap is built from.

Nothing here writes anything. Every reader takes the home folder as an
argument, so tests can point it at an invented one.

The sources:
  code     Claude Code transcripts     ~/.claude/projects/<folder>/<id>.jsonl
  prompts  everything typed            ~/.claude/history.jsonl
  totals   daily activity counts       ~/.claude/stats-cache.json
  cowork   Cowork transcripts (Mac)    hidden folders inside each session folder
  git      the person's own commits    repositories in folders they worked in
  notes    files the person names
"""
import json
import os
import re
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

from redact import hide_lone_password, redact

COWORK_FOLDER = "Library/Application Support/Claude/local-agent-mode-sessions"
DEFAULT_RETENTION_DAYS = 30      # Claude Code's own default
REPO_SEARCH_DEPTH = 4
SKIP_FOLDERS = {"node_modules", "venv", ".venv", "__pycache__", "Library", "site-packages"}
YEAR_END_MONTH = 11              # a run in November or December is a year-end recap


# --- dates -----------------------------------------------------------------

def resolve_range(today, year=None):
    """The dates a run covers.

    Returns {"ask": True, "suggest": 2026} when the person must choose: a
    January run that names no year would otherwise cover a few days.
    """
    if year is None:
        if today.month == 1:
            return {"ask": True, "suggest": today.year - 1}
        year = today.year
    if year > today.year:
        raise ValueError("%d hasn't started yet." % year)
    end = today if year == today.year else date(year, 12, 31)
    finished = year < today.year
    label = "year-end" if finished or end.month >= YEAR_END_MONTH else "mid-year"
    return {"ask": False, "year": year, "start": date(year, 1, 1), "end": end, "label": label}


def months_in(span):
    """Every month the range touches, as "2026-01" strings."""
    return ["%d-%02d" % (span["year"], m) for m in range(1, span["end"].month + 1)]


def local_time(value):
    """A timestamp from any source as local time, or None if it isn't one."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000).astimezone()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        except ValueError:
            return None
    return None


def in_range(when, span):
    return when is not None and span["start"] <= when.date() <= span["end"]


# --- reading transcript files ----------------------------------------------

def read_lines(path):
    """Yield each record in a .jsonl file. A broken line, or a file that can't be opened, yields None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    yield None
                    continue
                yield record if isinstance(record, dict) else None
    except OSError:
        yield None


def code_files(home):
    """Main Claude Code transcripts. Subagent files sit deeper or start "agent-"."""
    root = Path(home) / ".claude" / "projects"
    return sorted(p for p in root.glob("*/*.jsonl") if not p.name.startswith("agent-"))


def cowork_files(home):
    """{session folder: [its transcripts]}. They sit in a hidden folder inside it."""
    root = Path(home) / COWORK_FOLDER
    found = {}
    # The exact layouts only, so a file Claude created elsewhere in a session's
    # folder is never mistaken for history. Some sessions sit in "agent".
    for session in ("*/*/local_*", "*/*/agent/local_*"):
        for path in root.glob(session + "/.claude/projects/*/*.jsonl"):
            if not path.name.startswith("agent-") and not path.is_symlink():
                found.setdefault(path.parents[3], []).append(path)
    return {key: sorted(found[key]) for key in sorted(found)}


REMINDER = re.compile(r"<(system-reminder|uploaded_files|local-command-[a-z]+|command-[a-z]+)>.*?</\1>", re.DOTALL)
# A slash command as Claude Code records it: these tags and nothing else.
COMMAND = re.compile(r"\s*(?P<skill><command-message>[^<]*</command-message>\s*)?"
                     r"<command-name>/?(?P<name>[^<]+)</command-name>\s*"
                     r"(?:<command-message>[^<]*</command-message>\s*)?"
                     r"(?:<command-args>(?P<words>.*)</command-args>\s*)?", re.DOTALL)
PASTE = re.compile(r"<pasted_content([^>]*)>.*?</pasted_content\1>", re.DOTALL)
UNCLOSED = re.compile(r"<(?:system-reminder|pasted_content|uploaded_files)[^>]*>.*", re.DOTALL)
NOT_TYPED = ("[Request interrupted", "Caveat:", "This session is being continued from",
             "[SYSTEM NOTIFICATION", "Another Claude session sent", "Base directory for this skill:",
             "Tool loaded.")
# Commands that run Claude Code itself. Transcripts already leave these out, so a
# typed-prompt list that kept them would count leaving the app as something said.
BUILT_IN = {"/" + name for name in (
    "add-dir agents bashes btw bug chrome clear compact config context copy cost desktop diff doctor effort exit "
    "export extra-usage fast feedback fork help hooks ide install-github-app keybindings login logout mcp memory "
    "mobile model output-style permissions plugin plugins privacy-settings quit rate-limit-options release-notes "
    "reload-plugins remote-control rename resume rewind sandbox settings skills stats status statusline stickers "
    "tasks terminal-setup theme tui usage vim").split()}
QUESTION_ANSWERS = re.compile(r"^(?:The user answered:|Your questions have been answered:)")
ANSWER = re.compile(r'"="(.*?)"(?=, "|\. (?:You can now|Read the answers)|$)', re.DOTALL)


def slash_command(part):
    """A typed skill command as "/name words", or "" for a built-in like /clear."""
    command = COMMAND.fullmatch(part)
    if not command:
        return ""
    words = clean(command.group("words") or "")
    if not words and not command.group("skill"):
        return ""
    return ("/" + command.group("name").strip() + " " + words).strip()


def clean(part):
    """Take out reminders and pasted material, which the person did not type."""
    return UNCLOSED.sub("", PASTE.sub("[pasted text]", REMINDER.sub("", part))).strip()


def question_ids(record):
    """IDs of questions Claude asked in this record."""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if record.get("type") != "assistant" or not isinstance(content, list):
        return set()
    return {str(i.get("id")) for i in content if isinstance(i, dict)
            and i.get("type") == "tool_use" and i.get("name") == "AskUserQuestion"}


def typed_text(record, questions=()):
    """What the person typed in this record, or "" when it's something else.

    `questions` holds the IDs of Claude's questions so far. Only results for
    those count as answers, so a file that looks like an answer does not.
    """
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain") \
            or record.get("isCompactSummary"):
        return ""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "tool_result" and str(item.get("tool_use_id")) in questions:
                # Answers to Claude's questions come back this way.
                answer = item.get("content")
                if isinstance(answer, list):
                    answer = " ".join(str(a.get("text") or "") for a in answer if isinstance(a, dict))
                if isinstance(answer, str) and QUESTION_ANSWERS.match(answer):
                    parts.extend(ANSWER.findall(answer))
    kept = []
    for part in parts:
        part = slash_command(part) or clean(part)
        if part and not part.startswith(NOT_TYPED) and not part.startswith("<"):
            kept.append(part)
    return "\n".join(kept)


SHELL_INPUT = re.compile(r"<bash-input>(.*?)</bash-input>", re.DOTALL)


def shell_command(record):
    """A command the person ran with "!", which is not kept as a message."""
    message = record.get("message")
    content = message.get("content") if record.get("type") == "user" and isinstance(message, dict) else None
    if isinstance(content, list):
        content = " ".join(str(i.get("text") or "") for i in content if isinstance(i, dict))
    return " ".join(SHELL_INPUT.findall(content)) if isinstance(content, str) else ""


def reply_text(record):
    if record.get("type") != "assistant" or record.get("isSidechain"):
        return ""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ""
    return "\n".join(str(i.get("text") or "") for i in content
                     if isinstance(i, dict) and i.get("type") == "text").strip()


def project_name(folder, home):
    """A readable project label: "claude/lab/recap" for ~/claude/lab/recap."""
    if not folder:
        return "unknown"
    try:
        short = str(Path(folder).relative_to(home))
    except ValueError:
        return str(folder)
    return "home folder" if short == "." else short


def read_session(paths, session_id, source, home, span):
    """One session from its transcript files, or None if nothing is in range."""
    session = {"id": session_id, "source": source, "title": "", "folder": "", "project": "",
               "launch": "", "messages": [], "last_reply": "", "first": None, "last": None,
               "months": [], "opening": "", "unreadable": 0}
    given_title = ""
    before = ""
    questions = set()
    for path in paths:
        for record in read_lines(path):
            if record is None:
                session["unreadable"] += 1
                continue
            if record.get("type") == "ai-title":
                session["title"] = str(record.get("aiTitle") or session["title"])
            if record.get("type") == "custom-title":
                given_title = str(record.get("customTitle") or given_title)
            session["folder"] = session["folder"] or str(record.get("cwd") or "")
            session["launch"] = session["launch"] or str(record.get("entrypoint") or "")
            when = local_time(record.get("timestamp"))
            if when is None:
                continue
            questions |= question_ids(record)
            typed = typed_text(record, questions)
            text = hide_lone_password(typed, before)
            # A hidden word leaves the sudo command as the context, for a second try.
            if text == typed:
                before = shell_command(record) or typed or before
            if not in_range(when, span):
                continue
            if text and not session["opening"]:
                session["opening"] = redact(text)[:200]    # secrets out before trimming
            if session["first"] is None or when < session["first"]:
                session["first"] = when
            if session["last"] is None or when > session["last"]:
                session["last"] = when
            month = when.strftime("%Y-%m")
            if month not in session["months"]:
                session["months"].append(month)
            if text:
                session["messages"].append({"time": when, "text": text})
            session["last_reply"] = reply_text(record) or session["last_reply"]
    if session["first"] is None:
        return None
    session["months"].sort()
    session["messages"].sort(key=lambda m: m["time"])
    session["title"] = given_title or session["title"] or session["opening"][:80] or "Untitled session"
    session["project"] = "Cowork" if source == "cowork" else project_name(session["folder"], home)
    return session


def read_sessions(home, span):
    """Every session with something in range, plus how many lines couldn't be read."""
    sessions = []
    broken = 0
    for path in code_files(home):
        session = read_session([path], path.stem, "code", home, span)
        if session:
            sessions.append(session)
            broken += session["unreadable"]
    for folder, paths in cowork_files(home).items():
        session = read_session(paths, folder.name, "cowork", home, span)
        if session:
            sessions.append(session)
            broken += session["unreadable"]
    sessions.sort(key=lambda s: (s["first"], s["id"]))
    return sessions, broken


# --- prompts, totals, settings -----------------------------------------------

def read_prompts(home, span):
    """Typed prompts in range: [{"time", "text", "folder", "session"}]."""
    path = Path(home) / ".claude" / "history.jsonl"
    prompts = []
    broken = 0
    before = {}
    if not path.exists():
        return prompts, broken
    for record in read_lines(path):
        if record is None:
            broken += 1
            continue
        when = local_time(record.get("timestamp"))
        typed = str(record.get("display") or "")
        session = str(record.get("sessionId") or "")
        text = hide_lone_password(typed, before.get(session, ""))
        if text == typed:
            before[session] = typed
        if in_range(when, span) and text.strip() and text.strip() not in BUILT_IN:
            prompts.append({"time": when, "text": text,
                            "folder": str(record.get("project") or ""),
                            "session": session})
    prompts.sort(key=lambda p: p["time"])
    return prompts, broken


def read_totals(home, span):
    """Months the daily totals vouch for, up to the date they were last computed.

    They only support a month's evidence label. They are never added to a
    count, because they overlap the transcripts.
    """
    path = Path(home) / ".claude" / "stats-cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last = date.fromisoformat(str(data.get("lastComputedDate")))
    except (OSError, ValueError, AttributeError):
        return {"found": False, "months": [], "last": None}
    months = set()
    for row in data.get("dailyActivity") or []:
        try:
            day = date.fromisoformat(str(row.get("date")))
        except (ValueError, AttributeError):
            continue
        if span["start"] <= day <= min(span["end"], last) and row.get("messageCount"):
            months.add(day.strftime("%Y-%m"))
    return {"found": True, "months": sorted(months), "last": last}


def retention_days(home):
    """The person's own transcript setting. Read only; this never changes it."""
    try:
        settings = json.loads((Path(home) / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return int(settings.get("cleanupPeriodDays", DEFAULT_RETENTION_DAYS))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_RETENTION_DAYS


# --- git ---------------------------------------------------------------------

# A cloned repository's own settings can name programs for git to run, such as
# a signature checker. These turn that off, and stop git reaching the network.
SAFE_GIT = ["-c", "log.showSignature=false", "-c", "gpg.program=false", "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null", "-c", "core.sshCommand=false", "-c", "protocol.allow=never",
            "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never",
            "-c", "safe.bareRepository=explicit"]


def run_git(home, folder, *arguments):
    """Git's output as text, or None when the command fails."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               GIT_ALLOW_PROTOCOL="none")
    try:
        done = subprocess.run(["git", "-C", str(folder)] + SAFE_GIT + list(arguments), env=env, timeout=60,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def find_repositories(home, folders):
    """Repositories holding the given folders, or up to four levels below them.

    The home folder and anything above it are not searched downward.
    """
    home = Path(home).resolve()
    repositories = set()
    for folder in sorted({str(f) for f in folders if f}):
        start = Path(folder)
        if not start.is_dir():
            continue
        start = start.resolve()
        if start == home or start in home.parents:
            continue
        top = (run_git(home, start, "rev-parse", "--show-toplevel") or "").strip()
        if top:
            repositories.add(str(Path(top).resolve()))
        for current, names, _ in os.walk(str(start)):
            depth = len(Path(current).parts) - len(start.parts)
            if ".git" in names:
                repositories.add(current)
            names[:] = [] if depth >= REPO_SEARCH_DEPTH else sorted(
                n for n in names if n not in SKIP_FOLDERS and not n.startswith("."))
    return sorted(repositories)


def read_all_commits(home, repositories, span, failed=None):
    """Commits by anyone, each once, with author details.

    Repositories git couldn't read are added to `failed`, when it is given.
    """
    commits = {}
    since = (span["start"] - timedelta(days=1)).isoformat()
    for repository in repositories:
        log = run_git(home, repository, "log", "-z", "--all", "--no-merges", "--no-show-signature",
                      "--since=" + since, "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s")
        if log is None:
            # A repository with no commits yet also fails, and has nothing to miss.
            if failed is not None and run_git(home, repository, "rev-list", "-n1", "--all") != "":
                failed.append(repository)
            continue
        for line in log.split("\0"):
            fields = line.split("\x1f", 4)
            if len(fields) != 5 or not re.match(r"^[0-9a-f]{40,64}$", fields[0]):
                continue
            sha, name, email, when, subject = fields
            when = local_time(when)
            if in_range(when, span):
                commits.setdefault(sha, {"sha": sha, "name": name, "email": email.lower(), "time": when,
                                         "subject": subject, "repository": Path(repository).name,
                                         "path": repository})
    return sorted(commits.values(), key=lambda c: (c["time"], c["sha"]))


EMAIL = re.compile(r"^[a-z0-9._%+\[\]-]+@[a-z0-9.-]+$")


def likely_own_emails(home, repositories, commits):
    """Author emails that look like the person's: {email: {"commits", "sure"}}.

    The person's own global git email is sure. Anything else, such as the same
    author name with another email, or an email one repository is set up with,
    is a guess the person has to confirm. A repository's own setting only
    speaks for that repository, because a cloned one could claim any name.
    """
    def setting(place, scope, key):
        return (run_git(home, place, "config", scope, key) or "").strip().lower()

    sure = setting(home, "--global", "user.email")
    name = setting(home, "--global", "user.name")
    local = {r: setting(r, "--local", "user.email") for r in repositories}
    found = {}
    for commit in commits:
        email = commit["email"]
        if not EMAIL.match(email):
            continue
        if (sure and email == sure) or (name and commit["name"].strip().lower() == name) \
                or (email and email == local.get(commit["path"])):
            entry = found.setdefault(email, {"commits": 0, "sure": email == sure})
            entry["commits"] += 1
    return found


# --- notes -------------------------------------------------------------------

DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def _first_date(text):
    for match in DATE.finditer(text):
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
    return None


NOTE_SIZE_LIMIT = 2000000        # bytes


def note_files(paths):
    """The text files at these paths. Links, hidden folders and code folders are left out."""
    for path in (Path(p).expanduser() for p in paths):
        if path.is_file():
            yield path
        for folder, names, files in os.walk(str(path)):
            names[:] = sorted(n for n in names if n not in SKIP_FOLDERS and not n.startswith("."))
            for name in sorted(files):
                found = Path(folder) / name
                if found.suffix in (".md", ".txt") and not found.is_symlink():
                    yield found


def note_text(path):
    """The file's text, or None when it can't be read, isn't text, or is very large."""
    try:
        data = path.read_bytes() if path.stat().st_size <= NOTE_SIZE_LIMIT else b"\0"
    except OSError:
        return None
    return None if b"\0" in data else data.decode("utf-8", "replace")


def read_notes(paths, span):
    """Dated entries from the files the person named, and the names of files skipped.

    A line holding a date starts a new entry. A file with no dates inside is
    one entry, dated by its file name, or else by when it last changed.
    """
    notes = []
    skipped = []
    for path in note_files(paths):
        text = note_text(path)
        if text is None:
            skipped.append(path.name)
            continue
        entries = []
        for line in text.splitlines():
            day = _first_date(line)
            if day:
                entries.append([day, [line]])
            elif entries:
                entries[-1][1].append(line)
        if not entries:
            day = _first_date(path.name) or date.fromtimestamp(path.stat().st_mtime)
            entries = [[day, [text]]]
        for day, lines in entries:
            if span["start"] <= day <= span["end"]:
                body = "\n".join(lines).strip()
                if body:
                    notes.append({"time": datetime.combine(day, time(12)).astimezone(),
                                  "title": path.name, "text": body})
    notes.sort(key=lambda n: (n["time"], n["title"]))
    return notes, skipped


# --- the size estimate -------------------------------------------------------

TOKENS_PER_SESSION = 500
TOKENS_PER_PROMPT = 30


def _peek(path):
    """When a transcript starts and where it ran, without reading any message."""
    for record in read_lines(path):
        if record is None:
            continue
        when = local_time(record.get("timestamp"))
        if when is not None:
            return when, str(record.get("cwd") or "")
    return None, ""


def scan(home, span):
    """What exists and roughly how big the job is. Reads no message text."""
    changed_after = datetime.combine(span["start"], time()).timestamp()

    def count(groups):
        by_month = {}
        sessions = 0
        for paths in groups:
            starts = [w for w in (_peek(p)[0] for p in paths) if w is not None]
            if not starts or min(starts).date() > span["end"]:
                continue
            if max(p.stat().st_mtime for p in paths if p.exists()) < changed_after:
                continue
            sessions += 1
            month = max(min(starts).date(), span["start"]).strftime("%Y-%m")
            by_month[month] = by_month.get(month, 0) + 1
        return {"found": sessions > 0, "sessions": sessions, "months": by_month}

    code = count([[p] for p in code_files(home)])
    cowork = count(list(cowork_files(home).values()))
    prompt_months = {}
    history = Path(home) / ".claude" / "history.jsonl"
    if history.exists():
        for record in read_lines(history):
            when = local_time(record.get("timestamp")) if record else None
            if in_range(when, span):
                key = when.strftime("%Y-%m")
                prompt_months[key] = prompt_months.get(key, 0) + 1
    prompts = sum(prompt_months.values())
    totals = read_totals(home, span)
    tokens = (code["sessions"] + cowork["sessions"]) * TOKENS_PER_SESSION + prompts * TOKENS_PER_PROMPT
    return {"code": code, "cowork": cowork,
            "prompts": {"found": prompts > 0, "prompts": prompts, "months": prompt_months},
            "totals": {"found": totals["found"], "last": totals["last"]},
            "retention_days": retention_days(home), "estimated_tokens": tokens}
