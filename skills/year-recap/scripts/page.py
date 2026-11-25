"""Check Claude's story and draw the page.

Claude supplies text and evidence IDs. Code supplies every number, escapes all
text, and refuses claims with no evidence. See references/story-format.md.
"""
import hashlib
import json
import re
from datetime import date
from html import escape
from pathlib import Path

import redact
from condense import load, month_name, save, unread

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "page-template.html"
LABELS = {"full": "full detail", "partial": "partial detail", "none": "no records found"}
CATEGORIES = ["people", "clients", "employer", "health", "money", "family", "quote", "credential"]


# --- checking ----------------------------------------------------------------

def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_text_list(value):
    return isinstance(value, list) and bool(value) and all(map(_is_text, value))


def _check_evidence(where, ids, evidence):
    if not isinstance(ids, list) or not ids:
        return ["%s has no evidence." % where]
    return ["%s cites %s, which doesn't exist." % (where, i) for i in ids if i not in evidence]


def check_shape(story):
    """Problems with how the story is laid out, before anything in it is read."""
    if not isinstance(story, dict):
        return ["The story must be one object, as story-format.md shows."]
    problems = []
    for name in ("slides", "chapters"):
        items = story.get(name)
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            problems.append("\"%s\" must be a list of objects." % name)
    lessons = story.get("lessons")
    if not isinstance(lessons, dict):
        problems.append("\"lessons\" must be an object.")
    else:
        for name in ("worked", "didnt"):
            items = lessons.get(name)
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                problems.append("\"%s\" must be a list of objects." % name)
    if not isinstance(story.get("relabel") or {}, dict):
        problems.append("\"relabel\" must be an object.")
    if problems:
        return problems
    for number, chapter in enumerate(story["chapters"], 1):
        if not _is_text_list(chapter.get("months")):
            problems.append("Chapter %d needs a list of months." % number)
    return problems


def check_story(story, stats, labels, evidence):
    """Everything wrong with the story, as plain sentences. Empty means it can be drawn."""
    problems = check_shape(story)
    if problems:
        return problems
    for field in ("title", "opening"):
        if not _is_text(story.get(field)):
            problems.append("The story has no %s." % field)

    slides = story.get("slides") or []
    if len(slides) not in (5, 6):
        problems.append("There must be 5 or 6 slides, and there are %d." % len(slides))
    used = [str(slide.get("stat")) for slide in slides]
    for name in sorted({n for n in used if used.count(n) > 1}):
        problems.append("%s is on more than one slide." % name)
    for number, slide in enumerate(slides, 1):
        if str(slide.get("stat")) not in stats:
            problems.append("Slide %d uses a number that doesn't exist: %s." % (number, slide.get("stat")))
        if not _is_text(slide.get("headline")):
            problems.append("Slide %d has no headline." % number)

    chapters = story.get("chapters") or []
    told = [m for c in chapters for m in c.get("months") or []]
    for month in labels:
        if told.count(month) != 1:
            problems.append("%s is in %d chapters and must be in exactly one." % (month_name(month), told.count(month)))
    for month in told:
        if month not in labels:
            problems.append("%s is outside the dates this recap covers." % month)
    if told != sorted(told):
        problems.append("The chapters aren't in calendar order.")
    for number, chapter in enumerate(chapters, 1):
        where = "Chapter %d" % number
        if not _is_text(chapter.get("title")) or not _is_text_list(chapter.get("text")):
            problems.append("%s needs a title, and text as a list of paragraphs." % where)
        if chapter.get("evidence") or any(labels.get(m) != "none" for m in chapter.get("months") or []):
            problems += _check_evidence(where, chapter.get("evidence"), evidence)

    lessons = story.get("lessons") or {}
    for group, name in (("worked", "What worked"), ("didnt", "What didn't")):
        claims = lessons.get(group) or []
        if not claims:
            problems.append("\"%s\" is empty." % name)
        for number, claim in enumerate(claims, 1):
            where = "\"%s\" claim %d (%s)" % (name, number, str(claim.get("claim"))[:60])
            if not _is_text(claim.get("claim")):
                problems.append("%s has no text." % where)
            problems += _check_evidence(where, claim.get("evidence"), evidence)
    if not _is_text_list(lessons.get("carry")):
        problems.append("\"What to carry forward\" must be a list with something in it.")

    for key, words in (story.get("relabel") or {}).items():
        if key not in evidence:
            problems.append("A source label is given for %s, which doesn't exist." % key)
        elif not isinstance(words, dict) or not words or not set(words) <= {"title", "project"} \
                or not all(map(_is_text, words.values())):
            problems.append("The source label for %s must give a title, a project, or both." % key)

    for found in redact.find("\n".join(_strings(story))):
        problems.append("The story contains something shaped like a secret (%s)." % found["kind"])
    return problems


def relabeled(evidence, story):
    """The evidence with the story's own wording for any source titles or projects."""
    changed = dict(evidence)
    for key, words in (story.get("relabel") or {}).items():
        changed[key] = dict(evidence[key], **words)
    return changed


def _strings(value):
    """Every piece of text anywhere in the story."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


# --- drawing -------------------------------------------------------------------

def month_spans(months):
    """["2026-01", "2026-02", "2026-04"] becomes "January to February, April"."""
    numbers = sorted(int(m[5:]) for m in months)
    year = months[0][:4]
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    name = lambda n: month_name("%s-%02d" % (year, n))
    return ", ".join(name(a) if a == b else "%s to %s" % (name(a), name(b)) for a, b in runs)


def long_date(text):
    day = date.fromisoformat(text)
    return "%s %d, %d" % (day.strftime("%B"), day.day, day.year)


def draw_slide(slide, stat):
    value = stat["value"]
    if isinstance(value, list):
        rows = "".join("<li><span>%s</span><b>%s</b></li>" % (escape(a), escape(b)) for a, b in value)
        shown = "<ol>%s</ol>" % rows
    else:
        shown = '<p class="value%s">%s</p>' % (" long" if len(value) > 7 else "", escape(value))
    note = "<p>%s</p>" % escape(slide["note"]) if _is_text(slide.get("note")) else ""
    return ('<article class="slide"><p class="headline">%s</p><div>%s<p>%s</p></div><div>%s<p class="covers">%s</p></div></article>'
            % (escape(slide["headline"]), shown, escape(stat["caption"]), note, escape(month_spans(stat["months"]))))


def draw_sources(ids, evidence):
    rows = []
    for key in ids:
        item = evidence[key]
        rows.append('<li class="data">%s | %s | %s | %s</li>' % (
            escape(long_date(item["date"])), escape(item["kind"]), escape(item["project"]), escape(item["title"])))
    return "<ul>%s</ul>" % "".join(rows)


def draw_claim(claim, evidence):
    alone = all(key.startswith("P") for key in claim["evidence"])
    count = len(claim["evidence"])
    note = "from prompts alone, " if alone else ""
    return ('<details><summary>%s <span class="data">%s%d source%s</span></summary>%s</details>'
            % (escape(claim["claim"]), note, count, "" if count == 1 else "s", draw_sources(claim["evidence"], evidence)))


def draw_chapter(chapter, labels, evidence):
    tape = "".join('<li class="%s"><i></i>%s: %s</li>' % (labels[m], month_name(m), LABELS[labels[m]])
                   for m in chapter["months"])
    text = "".join("<p>%s</p>" % escape(p) for p in chapter["text"])
    changed = ('<div class="changed"><h3>What changed</h3><p>%s</p></div>' % escape(chapter["changed"])
               if _is_text(chapter.get("changed")) else "")
    sources = ""
    if chapter.get("evidence"):
        alone = all(key.startswith("P") for key in chapter["evidence"])
        sources = "<details><summary>Sources for this chapter%s</summary>%s</details>" % (
            ' <span class="data">from prompts alone</span>' if alone else "", draw_sources(chapter["evidence"], evidence))
    return '<article class="chapter"><ul class="tape">%s</ul><h2>%s</h2>%s%s%s</article>' % (
        tape, escape(chapter["title"]), text, changed, sources)


def draw(story, stats, labels, evidence, run):
    """The page content, without the outer document."""
    evidence = relabeled(evidence, story)
    left = unread(run)
    notice = ""
    if left:
        notice = '<p class="notice">This recap isn\'t finished. %d of %d parts of the year haven\'t been read yet: %s.</p>' % (
            len(left), len(run["batches"]), escape(", ".join(left)))
    footer = "Built from your own history on this computer. Tap a lesson to see what it rests on."
    if run.get("unreadable_lines"):
        footer += " %d lines of history couldn't be read." % run["unreadable_lines"]
    fill = {
        "TITLE": escape(story["title"]), "OPENING": escape(story["opening"]),
        "LABEL": "Mid-year recap" if run["label"] == "mid-year" else "Year-end recap",
        "RANGE": "%s to %s" % (long_date(run["start"]), long_date(run["end"])),
        "SLIDES": "\n".join(draw_slide(s, stats[s["stat"]]) for s in story["slides"]),
        "NOTICE": notice,
        "CHAPTERS": "\n".join(draw_chapter(c, labels, evidence) for c in story["chapters"]),
        "WORKED": "\n".join(draw_claim(c, evidence) for c in story["lessons"]["worked"]),
        "DIDNT": "\n".join(draw_claim(c, evidence) for c in story["lessons"]["didnt"]),
        "CARRY": "\n".join("<li>%s</li>" % escape(t) for t in story["lessons"]["carry"]),
        "FOOTER": escape(footer),
    }
    # One pass over the template, so text that happens to contain a marker is left alone.
    return re.sub(r"\{\{([A-Z]+)\}\}", lambda m: fill[m.group(1)], TEMPLATE.read_text(encoding="utf-8"))


def whole_document(content):
    """The page as a file that opens on its own."""
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "</head>\n<body>\n%s\n</body>\n</html>\n" % content)


def fingerprint(story):
    return hashlib.sha256(json.dumps(story, sort_keys=True).encode("utf-8")).hexdigest()


def build(folder):
    """Check the story in a working folder and write the page. Returns the problems found.

    Writes recap.html, which opens on its own, and recap-page.html, the same
    page without the outer document, for publishing as an artifact.
    """
    run = load(folder / "run.json")
    numbers = load(folder / "stats.json")
    evidence = load(folder / "evidence.json")
    story = load(folder / "story.json")
    if story is None:
        return ["There is no story.json in %s yet." % folder]
    problems = check_story(story, numbers["stats"], numbers["labels"], evidence)
    if problems:
        return problems
    content = draw(story, numbers["stats"], numbers["labels"], evidence, run)
    (folder / "recap-page.html").write_text(content, encoding="utf-8")
    (folder / "recap.html").write_text(whole_document(content), encoding="utf-8")
    run["state"] = "Built"
    run["built_story"] = fingerprint(story)
    save(folder / "run.json", run)
    return []


# --- the review list -------------------------------------------------------------

def check_review(entries, story):
    """Problems with Claude's review list: unknown categories or places that don't exist."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return ["The review list must be a list of objects, as story-format.md shows."]
    places = {"opening", "lessons"}
    places |= {"slide %d" % n for n in range(1, len(story["slides"]) + 1)}
    places |= {"chapter %d" % n for n in range(1, len(story["chapters"]) + 1)}
    problems = []
    for number, entry in enumerate(entries, 1):
        if entry.get("category") not in CATEGORIES:
            problems.append("Entry %d has an unknown category: %s." % (number, entry.get("category")))
        if entry.get("place") not in places:
            problems.append("Entry %d names a place that isn't on the page: %s." % (number, entry.get("place")))
        if not _is_text(entry.get("what")):
            problems.append("Entry %d doesn't say what to look at." % number)
    return problems


def credential_entries(story, evidence):
    """Review entries for anything on the page shaped like a credential."""
    parts = [("opening", [story["title"], story["opening"]])]
    parts += [("slide %d" % n, _strings(s)) for n, s in enumerate(story["slides"], 1)]
    for number, chapter in enumerate(story["chapters"], 1):
        cited = [evidence[i] for i in chapter.get("evidence") or []]
        parts.append(("chapter %d" % number, _strings(chapter) + _strings(cited)))
    cited = [evidence[i] for group in ("worked", "didnt") for c in story["lessons"][group] for i in c["evidence"]]
    parts.append(("lessons", _strings(story["lessons"]) + _strings(cited)))
    return [{"place": place, "category": "credential", "what": "Something shaped like a %s" % found["kind"]}
            for place, texts in parts for found in redact.find("\n".join(texts))]
