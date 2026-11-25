# The story file

Write the story to `story.json` in the working folder. Code checks it and draws the page. All text is plain text: tags and links are shown as typed.

```json
{
  "title": "The Year I Built a Garden Planner",
  "opening": "One paragraph that says what kind of year it was.",
  "slides": [
    {"stat": "messages", "headline": "You had a lot to say", "note": "Optional extra line."}
  ],
  "chapters": [
    {
      "title": "Learning to ask",
      "months": ["2026-01", "2026-02"],
      "text": ["First paragraph.", "Second paragraph."],
      "changed": "Optional. What was different by the end.",
      "evidence": ["S0001", "C0004"]
    }
  ],
  "lessons": {
    "worked": [{"claim": "Small steps got finished.", "evidence": ["S0012", "C0030"]}],
    "didnt": [{"claim": "The first planner was too big.", "evidence": ["S0003"]}],
    "carry": ["Start with one bed."]
  },
  "relabel": {"S0012": {"title": "Work with a client", "project": "client work"}}
}
```

## Rules the check enforces

- 5 or 6 slides, each with a different `stat`. `stat` must be a name in `stats.json`. Code shows the number and the months it covers, so never type a number from memory.
- Every month in the range is in exactly one chapter, in calendar order.
- Every claim under "worked" and "didn't" has at least one evidence ID, and so does every chapter with any surviving records.
- Evidence IDs must exist in `evidence.json`: `S` for a session, `P` for a day of typed prompts with no transcript, `C` for a commit, `N` for a note.
- Nothing shaped like a credential.

`relabel` is optional. It rewords how a source is shown on the page, for when its title or project names someone. It must still describe the same source.

## The review list

After the page is built, write `review.json`: a list of entries, one for each place to check before sharing.

```json
[{"place": "chapter 1", "category": "people", "what": "Names a neighbor in the second paragraph"}]
```

- `place`: `opening`, `slide 1` to `slide 6`, `chapter 1` and so on, or `lessons`.
- `category`: `people`, `clients`, `employer`, `health`, `money`, `family`, `quote` or `credential`. Use `quote` for a close paraphrase of something they typed, too.
