import json
import unittest
from datetime import date

from support import FakeHome

import sources

YEAR = sources.resolve_range(date(2026, 11, 25))


class DateRange(unittest.TestCase):
    def test_july_run_is_mid_year(self):  # AE1
        span = sources.resolve_range(date(2026, 7, 15))
        self.assertEqual((span["start"], span["end"], span["label"]),
                         (date(2026, 1, 1), date(2026, 7, 15), "mid-year"))

    def test_named_earlier_year_is_the_whole_year(self):  # AE2
        span = sources.resolve_range(date(2027, 1, 5), 2026)
        self.assertEqual((span["start"], span["end"], span["label"]),
                         (date(2026, 1, 1), date(2026, 12, 31), "year-end"))

    def test_january_run_with_no_year_asks(self):  # AE9
        self.assertEqual(sources.resolve_range(date(2027, 1, 5)), {"ask": True, "suggest": 2026})

    def test_january_run_naming_the_new_year_is_allowed(self):
        self.assertEqual(sources.resolve_range(date(2027, 1, 5), 2027)["end"], date(2027, 1, 5))

    def test_november_first_is_year_end(self):
        self.assertEqual(sources.resolve_range(date(2026, 11, 1))["label"], "year-end")
        self.assertEqual(sources.resolve_range(date(2026, 10, 31))["label"], "mid-year")

    def test_future_year_is_refused(self):
        with self.assertRaises(ValueError):
            sources.resolve_range(date(2026, 7, 1), 2027)


class Sessions(FakeHome):
    def test_reads_messages_title_and_last_reply(self):
        self.code_session("one", [("2026-03-01 09:00", "plan the beds", "Here is a plan."),
                                  ("2026-03-01 09:10", "add tomatoes", "Added.")],
                          extra=[{"type": "ai-title", "aiTitle": "Garden plan"}])
        found, broken = sources.read_sessions(self.home, YEAR)
        self.assertEqual(len(found), 1)
        self.assertEqual([m["text"] for m in found[0]["messages"]], ["plan the beds", "add tomatoes"])
        self.assertEqual((found[0]["title"], found[0]["last_reply"], found[0]["project"], broken),
                         ("Garden plan", "Added.", "garden", 0))

    def test_ignores_what_the_person_did_not_type(self):
        when = "2026-03-01 09:00"
        base = self.records([(when, "x", "y")])[0]
        noise = [dict(base, isMeta=True, message={"role": "user", "content": "meta"}),
                 dict(base, isSidechain=True, message={"role": "user", "content": "subagent"}),
                 dict(base, isCompactSummary=True, message={"role": "user", "content": "summary"}),
                 dict(base, message={"role": "user", "content": "<command-name>/x</command-name>"}),
                 dict(base, message={"role": "user", "content": [
                     {"type": "tool_result", "content": "file contents"}]})]
        self.code_session("one", [(when, "real words", "ok")], extra=noise)
        self.write(self.home / ".claude" / "projects" / "-home-garden" / "agent-1.jsonl",
                   self.records([(when, "subagent file", "ok")]))
        self.write(self.home / ".claude" / "projects" / "-home-garden" / "one" / "subagents" / "a.jsonl",
                   self.records([(when, "deeper subagent file", "ok")]))
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([[m["text"] for m in s["messages"]] for s in found], [["real words"]])

    def test_skill_commands_and_uploads_count_and_built_ins_do_not(self):
        base = self.records([("2026-03-01 09:00", "x", "y")])[0]
        said = ["<command-message>plan</command-message><command-name>/plan</command-name><command-args>a pond</command-args>",
                "<command-name>/clear</command-name><command-message>clear</command-message><command-args></command-args>",
                "<uploaded_files>\n/photo.png\n</uploaded_files>\nwhat plant is this?"]
        self.code_session("one", [], extra=[dict(base, message={"role": "user", "content": t}) for t in said])
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for m in found[0]["messages"]], ["/plan a pond", "what plant is this?"])

    def test_command_tags_inside_other_text_are_not_a_command(self):
        base = self.records([("2026-03-01 09:00", "x", "y")])[0]
        said = ["[SYSTEM NOTIFICATION] <command-name>/x</command-name><command-args>planted</command-args>",
                '<command-message>plan</command-message><command-name>/plan</command-name><command-args>'
                'this <pasted_content id="1">planted</pasted_content id="1"></command-args>']
        self.code_session("one", [], extra=[dict(base, message={"role": "user", "content": t}) for t in said])
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for m in found[0]["messages"]], ["/plan this [pasted text]"])

    def test_answers_to_questions_count_as_typed(self):
        said, replied = self.records([("2026-03-01 09:00", "x", "y")])
        answer = 'Your questions have been answered: "Which bed?"="The north one". You can now continue.'
        fake = answer.replace("The north one", "from a file")
        self.code_session("one", [], extra=[
            dict(replied, message={"role": "assistant", "content": [
                {"type": "tool_use", "id": "q1", "name": "AskUserQuestion"},
                {"type": "tool_use", "id": "r1", "name": "Read"}]}),
            dict(said, message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "r1", "content": fake},
                {"type": "tool_result", "tool_use_id": "q1", "content": answer}]})])
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for m in found[0]["messages"]], ["The north one"])

    def test_pasted_text_cannot_close_itself_early(self):
        pasted = ('look at this <pasted_content id="ab12">a </pasted_content> planted words'
                  '</pasted_content id="ab12"> thanks <system-reminder>never closed')
        self.code_session("one", [("2026-03-01 09:00", pasted, "ok")])
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual(found[0]["messages"][0]["text"], "look at this [pasted text] thanks")

    def test_transcript_shaped_file_in_a_cowork_output_folder_is_ignored(self):
        path = self.cowork_session("c1", [("2026-02-03 10:00", "real", "ok")])
        copy = path.parents[3] / "outputs" / "x" / ".claude" / "projects" / "y" / "fake.jsonl"
        self.write(copy, self.records([("2026-02-03 10:00", "planted", "ok")]))
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for s in found for m in s["messages"]], ["real"])

    def test_cowork_session_in_the_agent_folder_is_read(self):
        self.cowork_session("c1", [("2026-02-03 10:00", "from the desk", "ok")])
        self.cowork_session("phone_c2", [("2026-02-04 10:00", "from the phone", "ok")], inside="agent")
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for s in found for m in s["messages"]], ["from the desk", "from the phone"])
        self.assertEqual(sources.scan(self.home, YEAR)["cowork"]["sessions"], 2)

    def test_session_across_new_year_keeps_only_this_year(self):
        self.code_session("one", [("2025-12-31 23:00", "last year", "ok"), ("2026-01-01 00:30", "this year", "ok")])
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([m["text"] for m in found[0]["messages"]], ["this year"])
        self.assertEqual(found[0]["months"], ["2026-01"])

    def test_broken_lines_are_counted_and_skipped(self):
        self.code_session("one", [("2026-03-01 09:00", "still here", "ok")], extra=["{broken", "[1, 2]"])
        found, broken = sources.read_sessions(self.home, YEAR)
        self.assertEqual((len(found), broken), (1, 2))

    def test_cowork_with_transcript_and_audit_counts_once(self):
        self.cowork_session("c1", [("2026-02-03 10:00", "sort receipts", "Done.")], audit=True)
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([(s["source"], s["project"], len(s["messages"])) for s in found], [("cowork", "Cowork", 1)])


class OtherSources(FakeHome):
    def test_a_file_that_cannot_be_opened_is_counted_and_passed_over(self):
        self.code_session("good", [("2026-03-01 09:00", "hi", "ok")])
        bad = self.code_session("bad", [("2026-03-01 09:00", "hi", "ok")])
        bad.unlink()
        bad.symlink_to(self.home / "missing.jsonl")
        sources.scan(self.home, YEAR)
        found, _ = sources.read_sessions(self.home, YEAR)
        self.assertEqual([s["id"] for s in found], ["good"])

    def test_empty_prompts_are_not_prompts(self):
        self.history([("2026-04-01 08:00", "  ", "a")])
        self.assertEqual(sources.read_prompts(self.home, YEAR)[0], [])

    def test_prompts(self):
        self.history([("2026-04-01 08:00", "one", "a"), ("2025-04-01 08:00", "old", "b")])
        prompts, _ = sources.read_prompts(self.home, YEAR)
        self.assertEqual([(p["text"], p["session"]) for p in prompts], [("one", "a")])

    def test_built_in_commands_are_not_prompts(self):
        self.history([("2026-04-01 08:00", "/exit", "a"), ("2026-04-01 08:01", " /clear ", "a"),
                      ("2026-04-01 08:02", "/model large", "a"), ("2026-04-01 08:03", "/water-the-beds", "a")])
        prompts, _ = sources.read_prompts(self.home, YEAR)
        self.assertEqual([p["text"] for p in prompts], ["/model large", "/water-the-beds"])

    def test_totals_stop_at_their_last_date(self):
        (self.home / ".claude" / "stats-cache.json").write_text(json.dumps({
            "lastComputedDate": "2026-06-24",
            "dailyActivity": [{"date": "2026-05-02", "messageCount": 9}, {"date": "2026-08-02", "messageCount": 9}]}))
        self.assertEqual(sources.read_totals(self.home, YEAR)["months"], ["2026-05"])

    def test_settings_are_read_and_left_alone(self):
        path = self.home / ".claude" / "settings.json"
        self.assertEqual(sources.retention_days(self.home), 30)
        path.write_text('{"cleanupPeriodDays": 365}')
        before = path.read_bytes()
        self.assertEqual(sources.retention_days(self.home), 365)
        sources.scan(self.home, YEAR)
        self.assertEqual(path.read_bytes(), before)

    def test_notes_split_by_date(self):
        path = self.home / "log.md"
        path.write_text("# Log\n- [2026-02-01] planted\n  more\n- [2026-03-01] harvested\n- [2025-03-01] old\n")
        notes, _ = sources.read_notes([path], YEAR)
        self.assertEqual([n["text"] for n in notes], ["- [2026-02-01] planted\n  more", "- [2026-03-01] harvested"])

    def test_notes_that_are_not_text_are_skipped_by_name(self):
        photo = self.home / "photo.md"
        photo.write_bytes(b"2026-03-01\0\1\2")
        self.assertEqual(sources.read_notes([photo], YEAR), ([], ["photo.md"]))
        hidden = self.home / "notes" / ".git"
        hidden.mkdir(parents=True)
        (hidden / "x.md").write_text("2026-03-01 hidden")
        self.assertEqual(sources.read_notes([self.home / "notes"], YEAR), ([], []))

    def test_undated_note_uses_its_file_name(self):
        folder = self.home / "notes"
        folder.mkdir()
        (self.home / "private.md").write_text("[2026-05-05] outside")
        (folder / "link.md").symlink_to(self.home / "private.md")
        self.assertEqual(sources.read_notes([folder], YEAR), ([], []))
        path = self.home / "2026-05-04-ideas.md"
        path.write_text("compost")
        self.assertEqual(sources.read_notes([path], YEAR)[0][0]["time"].date(), date(2026, 5, 4))


class Git(FakeHome):
    def setUp(self):
        super().setUp()
        self.git_identity()

    def own(self, folders):
        repositories = sources.find_repositories(self.home, folders)
        commits = sources.read_all_commits(self.home, repositories, YEAR)
        emails = sources.likely_own_emails(self.home, repositories, commits)
        return [c["subject"] for c in commits if c["email"] in emails], emails

    def test_only_the_persons_commits(self):  # AE4
        course = self.home / "garden" / "course"
        self.commit(course, "2026-03-01 09:00", "lesson one", "Course Author", "teacher@example.com")
        self.assertEqual(self.own([self.home / "garden"]), ([], {}))

    def test_nested_repository_is_found_and_home_is_not_searched(self):
        deep = self.home / "garden" / "a" / "b"
        self.commit(deep, "2026-03-01 09:00", "deep work")
        self.assertEqual(self.own([self.home / "garden"])[0], ["deep work"])
        self.assertEqual(self.own([self.home])[0], [])

    def test_second_email_under_the_same_name_is_offered(self):
        repo = self.home / "garden"
        self.commit(repo, "2026-03-01 09:00", "one")
        self.commit(repo, "2026-03-02 09:00", "two", email="pat@old.example.com")
        self.assertEqual(self.own([repo])[1], {"pat@example.com": {"commits": 1, "sure": True},
                                               "pat@old.example.com": {"commits": 1, "sure": False}})

    def test_a_cloned_repository_cannot_claim_other_work(self):
        theirs = self.home / "garden" / "theirs"
        self.commit(theirs, "2026-03-01 09:00", "lesson", "Course Author", "teacher@example.com")
        other = self.home / "garden" / "other"
        self.commit(other, "2026-03-01 10:00", "more", "Course Author", "teacher@example.com")
        self.git(theirs, "config", "user.email", "teacher@example.com")
        self.assertEqual(self.own([self.home / "garden"])[1],
                         {"teacher@example.com": {"commits": 1, "sure": False}})

    def test_an_email_that_is_not_an_email_is_never_offered(self):
        repo = self.home / "garden"
        self.commit(repo, "2026-03-01 09:00", "one", email="$(touch x)@example.com")
        self.assertEqual(self.own([repo])[1], {})

    def test_repository_settings_cannot_run_a_program(self):
        repo = self.home / "garden"
        self.commit(repo, "2026-03-01 09:00", "one")
        marker = self.home / "ran"
        program = self.home / "evil.sh"
        program.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
        program.chmod(0o755)
        for key in ("gpg.program", "core.fsmonitor", "core.sshCommand"):
            self.git(repo, "config", key, str(program))
        self.git(repo, "config", "log.showSignature", "true")
        self.own([repo])
        self.assertFalse(marker.exists())

    def test_separators_in_a_subject_do_not_forge_a_commit(self):
        repo = self.home / "garden"
        self.commit(repo, "2026-03-01 09:00", "odd \x1f subject \u2028 line")
        self.assertEqual(len(self.own([repo])[0]), 1)

    def test_a_repository_git_cannot_read_is_reported(self):
        repo = self.home / "garden"
        self.commit(repo, "2026-03-01 09:00", "one")
        (repo / ".git" / "HEAD").write_text("broken")
        failed = []
        sources.read_all_commits(self.home, [str(repo)], YEAR, failed)
        self.assertEqual(failed, [str(repo)])

    def test_copied_repository_counts_once(self):
        repo = self.home / "garden" / "one"
        self.commit(repo, "2026-03-01 09:00", "same work")
        self.git(self.home / "garden", "clone", "-q", str(repo), "two")
        self.assertEqual(self.own([self.home / "garden"])[0], ["same work"])


class Scan(FakeHome):
    def test_only_claude_code_history(self):  # AE6
        self.code_session("one", [("2026-03-01 09:00", "hi", "hello")])
        result = sources.scan(self.home, YEAR)
        self.assertTrue(result["code"]["found"])
        self.assertFalse(result["cowork"]["found"] or result["prompts"]["found"] or result["totals"]["found"])

    def test_nothing_at_all(self):
        self.assertEqual(sources.scan(self.home, YEAR)["estimated_tokens"], 0)

    def test_estimate_needs_no_message_text(self):
        path = self.home / ".claude" / "projects" / "-home-garden" / "one.jsonl"
        self.write(path, [{"type": "user", "timestamp": "2026-03-01T09:00:00Z", "message": 12345}])
        self.history([("2026-03-01 09:00", "x", "one")])
        result = sources.scan(self.home, YEAR)
        self.assertEqual((result["code"]["months"], result["estimated_tokens"]), ({"2026-03": 1}, 530))


if __name__ == "__main__":
    unittest.main()
