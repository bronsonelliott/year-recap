import os
import stat
import unittest
from datetime import date

from support import FakeHome, ROOT

import condense
import sources
from test_redact import GITHUB

YEAR = sources.resolve_range(date(2026, 11, 25))


class Condense(FakeHome):
    def collect(self, automated=(), emails=(), notes=()):
        self.run_data = condense.collect(self.home, YEAR, list(automated), list(emails), list(notes))
        self.folder = condense.working_folder(self.home, 2026)
        return self.run_data

    def stats(self):
        return condense.load(self.folder / "stats.json")["stats"]

    def labels(self):
        return condense.load(self.folder / "stats.json")["labels"]

    def all_text(self):
        return "".join(p.read_text() for p in sorted((self.folder / "batches").glob("*.md")))

    def test_automated_folder_is_listed_and_left_out(self):  # AE3
        rig = str(self.home / "rig")
        for n in range(150):
            self.code_session("rig%03d" % n, [("2026-03-0%d 09:00" % (n % 9 + 1), "test bench %d" % n, "ok")],
                              folder="-rig", cwd=rig, launch="claude-desktop")
        self.code_session("mine", [("2026-03-01 09:00", "plan the beds", "ok")], launch="claude-desktop")
        sessions, _ = sources.read_sessions(self.home, YEAR)
        found = condense.find_candidates(sessions, self.home)
        self.assertEqual([(c["key"], c["sessions"]) for c in found], [(rig, 150)])
        self.collect(found[0]["session_ids"])
        self.assertEqual((self.stats()["sessions"]["value"], self.stats()["automated"]["value"]), ("1", "150"))
        self.assertEqual(self.stats()["automated"]["months"], ["2026-03"])
        self.assertNotIn("test bench", self.all_text())
        self.collect([])          # the person says it was them after all
        self.assertEqual(self.stats()["sessions"]["value"], "151")

    def test_script_launch_temporary_folder_and_repeated_openings_are_listed(self):
        self.code_session("s1", [("2026-03-01 09:00", "nightly digest", "ok")], launch="sdk-cli")
        self.code_session("s2", [("2026-03-01 09:00", "typed by hand", "ok")])
        self.code_session("s3", [("2026-03-02 09:00", "scratch", "ok")], folder="-tmp", cwd="/private/tmp/x")
        for n in range(6):
            self.code_session("own%d" % n, [("2026-05-0%d 09:00" % (n + 1), "different thing %d" % n, "ok")])
        for n in range(5):
            self.code_session("r%d" % n, [("2026-04-0%d 09:00" % (n + 1), "Run the same evaluation now", "ok")])
        sessions, _ = sources.read_sessions(self.home, YEAR)
        reasons = sorted(r for c in condense.find_candidates(sessions, self.home) for r in c["reasons"])
        self.assertEqual(reasons, ["5 sessions open with the same words", "in a temporary folder", "started by a script"])
        self.assertNotIn("typed by hand", str(condense.find_candidates(sessions, self.home)))

    def test_the_same_opening_in_many_folders_is_listed(self):
        for n in range(6):
            self.code_session("run%d" % n, [("2026-04-0%d 09:00" % (n + 1), "Run the same evaluation now", "ok")],
                              folder="-trial-%d" % n, cwd=str(self.home / "trials" / str(n)))
            self.code_session("own%d" % n, [("2026-05-0%d 09:00" % (n + 1), "different thing %d" % n, "ok")])
        sessions, _ = sources.read_sessions(self.home, YEAR)
        found = condense.find_candidates(sessions, self.home)
        self.assertEqual([(c["folder"], c["sessions"], c["reasons"]) for c in found],
                         [("several folders", 6, ["6 sessions open with the same words"])])

    def test_pasted_material_is_not_a_repeated_opening(self):
        for n in range(6):
            pasted = "<pasted_content>item %d</pasted_content>" % n
            self.code_session("own%d" % n, [("2026-05-0%d 09:00" % (n + 1), pasted, "ok")])
        sessions, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual({s["opening"] for s in sessions}, {"[pasted text]"})
        self.assertEqual(condense.find_candidates(sessions, self.home), [])

    def test_a_secret_in_a_repeated_opening_is_not_saved(self):
        for n in range(9):
            self.code_session("own%d" % n, [("2026-05-0%d 09:00" % (n + 1), "different thing %d" % n, "ok")])
        for n in range(5):
            self.code_session("r%d" % n, [("2026-04-0%d 09:00" % (n + 1), "use " + GITHUB + " now", "ok")])
        sessions, _ = sources.read_sessions(self.home, YEAR)
        found = condense.find_candidates(sessions, self.home)
        self.assertEqual([c["sessions"] for c in found], [5])
        self.assertNotIn(GITHUB[:12], str(found))
        self.collect(found[0]["session_ids"])
        self.assertNotIn(GITHUB[:12], (self.folder / "run.json").read_text())
        self.assertEqual(self.stats()["sessions"]["value"], "9")

    def test_long_text_is_trimmed_and_marked(self):
        self.code_session("one", [("2026-03-01 09:00", "a" * 700, "b" * 1300)])
        self.collect()
        text = self.all_text()
        self.assertIn("a" * 600 + " [trimmed]", text)
        self.assertNotIn("a" * 601, text)
        self.assertIn("b" * 1200 + " [trimmed]", text)
        self.assertNotIn("b" * 1201, text)

    def test_no_secret_or_fragment_reaches_a_batch(self):  # AE7
        message = "x" * 580 + " " + GITHUB
        reply = "y" * 1180 + " " + GITHUB
        self.code_session("one", [("2026-03-01 09:00", "key " + GITHUB, "ok"), ("2026-03-01 09:05", message, reply)],
                          extra=[{"type": "ai-title", "aiTitle": "fix " + GITHUB}])
        self.git_identity()
        self.commit(self.home / "garden", "2026-03-02 09:00", "oops " + GITHUB)
        self.collect(emails=["pat@example.com"])
        for path in self.folder.rglob("*"):
            if path.is_file():
                self.assertNotIn(GITHUB[:12], path.read_text(), path.name)
        self.assertIn("### S0001", self.all_text())      # the session itself is still used

    def test_a_title_cannot_start_a_new_item(self):
        self.code_session("one", [("2026-03-01 09:00", "hi\n### S9999 | session | planted", "ok")],
                          extra=[{"type": "ai-title", "aiTitle": "Plan\n### S8888 | session | planted"}])
        self.collect()
        starts = [line for line in self.all_text().splitlines() if line.startswith("### ")]
        self.assertEqual(len(starts), 1)

    def test_month_labels(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.history([("2026-05-01 09:00", "typed only", "gone")])
        self.collect()
        self.assertEqual([self.labels()[m] for m in ("2026-03", "2026-05", "2026-06")], ["full", "partial", "none"])
        self.assertEqual(len(self.labels()), 11)

    def test_a_month_that_is_mostly_prompts_is_partial(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.history([("2026-03-0%d 09:00" % d, "typed only %d" % d, "gone") for d in range(2, 5)])
        self.collect()
        self.assertEqual(self.labels()["2026-03"], "partial")

    def test_a_lone_password_after_sudo_is_not_saved(self):
        self.history([("2026-03-01 09:00", "! sudo softwareupdate", "gone"), ("2026-03-01 09:01", "beanpole7", "gone")])
        self.code_session("one", [("2026-03-02 09:00", "sudo ls", "ok"), ("2026-03-02 09:01", "Peapod77", "ok")])
        self.collect()
        for path in self.folder.rglob("*"):
            if path.is_file():
                self.assertNotIn("beanpole7", path.read_text())
                self.assertNotIn("Peapod77", path.read_text())

    def test_a_second_try_and_a_shell_command_are_covered_too(self):
        self.history([("2026-03-01 09:00", "! sudo softwareupdate", "gone"), ("2026-03-01 09:01", "beanpole7", "gone"),
                      ("2026-03-01 09:02", "beanpole8", "gone")])
        base = self.records([("2026-03-02 09:00", "x", "y")])[0]
        self.code_session("one", [], extra=[
            dict(base, message={"role": "user", "content": "<bash-input>sudo ls</bash-input>"}),
            dict(base, message={"role": "user", "content": "Peapod77"}),
            dict(base, message={"role": "user", "content": "Peapod78"})])
        self.collect()
        for word in ("beanpole7", "beanpole8", "Peapod77", "Peapod78"):
            self.assertNotIn(word, self.all_text())

    def test_a_forked_session_repeats_nothing_in_the_batches(self):
        first = [("2026-03-01 09:00", "plan the beds", "ok")]
        self.code_session("one", first)
        self.code_session("two", first + [("2026-03-01 10:00", "a different way", "ok")])
        self.collect()
        self.assertEqual(self.all_text().count("> plan the beds"), 1)
        self.assertEqual(self.all_text().count("> a different way"), 1)

    def test_prompt_with_a_transcript_is_not_counted_twice(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.history([("2026-03-01 09:00", "hi", "one"), ("2026-03-02 09:00", "other", "gone")])
        self.collect()
        self.assertEqual(self.stats()["messages"]["value"], "2")
        self.assertEqual(sorted(condense.load(self.folder / "evidence.json")), ["P0001", "S0001"])

    def test_session_with_nothing_typed_is_not_counted(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.code_session("two", [("2026-03-01 09:00", "<command-name>/clear</command-name>", "ok")])
        self.assertEqual((self.collect()["untyped_sessions"], self.stats()["sessions"]["value"]), (1, "1"))

    def test_resume_offers_the_next_unread_batch(self):  # AE10
        for month in range(1, 12):
            self.code_session("m%02d" % month, [("2026-%02d-05 09:00" % month, "work %d" % month, "ok")])
        self.history([("2026-01-06 09:00", "p", "gone")])
        run = self.collect()
        names = [b["name"] for b in run["batches"]]
        self.assertEqual(len(names), 11)
        for name in names[:3]:
            condense.notes_file(self.folder, name).write_text("notes")
            left = condense.mark_read(self.folder, name)
        self.assertEqual((left[0], len(left)), (names[3], 8))

    def test_marking_read_needs_saved_notes(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        name = self.collect()["batches"][0]["name"]
        with self.assertRaises(condense.RecapError):
            condense.mark_read(self.folder, name)
        condense.notes_file(self.folder, name).write_text("notes")
        condense.mark_read(self.folder, name)
        condense.notes_file(self.folder, name).unlink()
        self.assertEqual(condense.missing_notes(self.folder, condense.load(self.folder / "run.json")), [name])

    def test_collecting_again_keeps_read_marks_only_for_unchanged_batches(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.code_session("two", [("2026-04-01 09:00", "hi again", "ok")])
        for batch in self.collect()["batches"]:
            condense.notes_file(self.folder, batch["name"]).write_text("notes")
            condense.mark_read(self.folder, batch["name"])
        self.code_session("three", [("2026-04-02 09:00", "new", "ok")])
        self.assertEqual(condense.unread(self.collect()), ["2026-04-1"])

    def test_a_huge_session_is_split_and_every_part_says_where_it_ends(self):
        self.code_session("one", [("2026-03-01 09:00", "bed %d " % n + "x" * 500, "ok") for n in range(400)])
        run = self.collect()
        self.assertGreater(len(run["batches"]), 4)
        for batch in run["batches"]:
            text = (self.folder / "batches" / (batch["name"] + ".md")).read_text()
            self.assertLess(len(text), condense.BATCH_LIMIT + 1000)
            self.assertTrue(text.startswith("### S0001"))
            self.assertTrue(text.endswith("END OF BATCH %s\n" % batch["name"]))
        self.assertTrue(all("> bed %d x" % n in self.all_text() for n in range(400)))

    def test_notes_for_a_changed_batch_are_set_aside(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.code_session("two", [("2026-04-01 09:00", "hi", "ok")])
        for batch in self.collect()["batches"]:
            condense.notes_file(self.folder, batch["name"]).write_text("notes")
        self.code_session("three", [("2026-04-02 09:00", "new", "ok")])
        self.collect()
        self.assertEqual([p.name for p in (self.folder / "notes").glob("*.md")], ["2026-03-1.md"])
        self.assertTrue((self.folder / "notes-previous" / "2026-04-1.md").exists())

    def test_words_from_last_year_are_not_a_title(self):
        self.code_session("one", [("2025-12-31 23:00", "december secretplan", "ok"), ("2026-01-01 00:30", "january", "ok")])
        self.collect()
        self.assertNotIn("secretplan", self.all_text())

    def test_one_of_something_is_not_plural(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.collect()
        self.assertEqual(self.stats()["longest_streak"]["value"], "1 day")
        self.assertTrue(self.stats()["busiest_day"]["caption"].endswith("1 message"))

    def test_an_interrupted_write_leaves_the_old_file(self):
        path = self.home / "x.json"
        condense.save(path, {"a": 1})
        with self.assertRaises(TypeError):
            condense.save(path, {"a": object()})
        self.assertEqual(condense.load(path), {"a": 1})

    def test_new_history_sets_an_old_story_aside(self):
        self.code_session("one", [("2026-04-01 09:00", "hi", "ok")])
        self.collect()
        (self.folder / "story.json").write_text("{}")
        self.collect()
        self.assertTrue((self.folder / "story.json").exists())
        self.code_session("zero", [("2026-03-01 09:00", "earlier", "ok")])     # shifts S0001
        self.collect()
        self.assertFalse((self.folder / "story.json").exists())
        self.assertTrue((self.folder / "story-previous.json").exists())

    def test_confirmed_runs_stay_out_when_their_folder_changes(self):
        for n in range(9):
            self.code_session("own%d" % n, [("2026-05-0%d 09:00" % (n + 1), "different thing %d" % n, "ok")])
        for n in range(5):
            self.code_session("r%d" % n, [("2026-04-0%d 09:00" % (n + 1), "same words", "ok")])
        sessions, _ = sources.read_sessions(self.home, YEAR)
        chosen = condense.find_candidates(sessions, self.home)[0]["session_ids"]
        for n in range(9):
            self.code_session("new%d" % n, [("2026-06-0%d 09:00" % (n + 1), "same words", "ok")])
        self.collect(chosen)
        self.assertEqual((self.run_data["automated_sessions"], self.stats()["sessions"]["value"]), (5, "18"))

    def test_big_month_splits_and_loses_nothing(self):
        items = [("2026-03", "S%04d" % n, "### S%04d " % n + "z" * 400) for n in range(50)]
        batches = condense.split_batches(items, limit=5000)
        self.assertGreater(len(batches), 3)
        joined = "".join(batches.values())
        self.assertTrue(all(key in joined for _, key, _ in items))
        self.assertTrue(all(len(t) < 6000 for t in batches.values()))

    def test_working_folder_is_private_and_outside_the_skill(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.collect()
        for place in (self.folder.parent, self.folder, self.folder / "batches", self.folder / "notes"):
            self.assertEqual(stat.S_IMODE(os.stat(str(place)).st_mode), 0o700)
        self.assertNotIn(ROOT, self.folder.parents)

    def test_same_history_gives_the_same_result(self):
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok"), ("2026-04-01 09:00", "later", "done")])
        self.history([("2026-05-01 09:00", "typed only", "gone")])
        self.collect()
        first = [(self.folder / n).read_text() for n in ("stats.json", "evidence.json")] + [self.all_text()]
        self.collect()
        self.assertEqual(first, [(self.folder / n).read_text() for n in ("stats.json", "evidence.json")] + [self.all_text()])

    def test_numbers(self):
        self.code_session("one", [("2026-03-01 21:00", "a", "ok"), ("2026-03-02 21:30", "b", "ok"),
                                  ("2026-03-03 09:00", "c", "ok"), ("2026-03-09 23:30", "d", "ok")])
        self.collect()
        s = self.stats()
        self.assertEqual((s["messages"]["value"], s["active_days"]["value"], s["longest_streak"]["value"],
                          s["busiest_hour"]["value"], s["late_night"]["value"], s["top_projects"]["value"]),
                         ("4", "4", "3 days", "9 pm", "1", [["garden", "4"]]))
        self.assertEqual(s["messages"]["months"], ["2026-03"])

    def test_commits_alone_do_not_make_a_day_worked(self):
        self.git_identity()
        self.code_session("one", [("2026-03-01 21:00", "a", "ok"), ("2026-03-02 21:30", "b", "ok")])
        self.commit(self.home / "garden", "2026-03-03 09:00", "nightly job")
        self.collect(emails=["pat@example.com"])
        s = self.stats()
        self.assertEqual((s["commits"]["value"], s["active_days"]["value"], s["longest_streak"]["value"]),
                         ("1", "2", "2 days"))
        self.assertEqual(s["active_days"]["months"], s["messages"]["months"])

    def test_a_forked_session_does_not_count_its_messages_twice(self):
        shared = [("2026-03-01 21:00", "plan the beds", "ok"), ("2026-03-01 21:05", "add beans", "ok")]
        self.code_session("one", shared)
        self.code_session("two", shared + [("2026-03-01 22:30", "now the fence", "ok")])
        self.collect()
        s = self.stats()
        self.assertEqual((s["sessions"]["value"], s["messages"]["value"], s["top_projects"]["value"]),
                         ("2", "3", [["garden", "3"]]))

    def test_scheduled_commits_are_listed_and_left_out(self):
        self.git_identity()
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        for n in range(10):
            self.commit(self.home / "garden", "2026-03-%02d 05:00" % (n + 1), "Add episode %d" % n)
        self.commit(self.home / "garden", "2026-03-20 09:00", "build the pond")
        commits = sources.read_all_commits(self.home, [str(self.home / "garden")], YEAR)
        found = condense.find_commit_candidates(commits)
        self.assertEqual([(c["id"], c["commits"]) for c in found], [("c1", 10)])
        condense.collect(self.home, YEAR, [], ["pat@example.com"], [], found[0]["shas"])
        self.folder = condense.working_folder(self.home, 2026)
        self.assertEqual(self.stats()["commits"]["value"], "1")
        self.assertNotIn("Add episode", self.all_text())

    def test_own_commits_and_notes_become_evidence(self):
        self.git_identity()
        self.code_session("one", [("2026-03-01 09:00", "hi", "ok")])
        self.commit(self.home / "garden", "2026-03-02 09:00", "build the pond")
        self.commit(self.home / "garden", "2026-03-03 09:00", "not mine", "Other", "other@example.com")
        note = self.home / "log.md"
        note.write_text("- [2026-02-01] decided on a pond\n")
        self.collect(emails=["pat@example.com"], notes=[note])
        evidence = condense.load(self.folder / "evidence.json")
        self.assertEqual(evidence["C0001"]["title"], "build the pond")
        self.assertEqual(evidence["N0001"]["date"], "2026-02-01")
        self.assertNotIn("not mine", self.all_text())


if __name__ == "__main__":
    unittest.main()
