import copy
import json
import re
import unittest
from html.parser import HTMLParser

from support import FakeHome

import make_example
import page
from condense import load, save
from test_redact import GITHUB


class Page(FakeHome):
    def setUp(self):
        super().setUp()
        self.folder = self.home / "work"
        self.folder.mkdir()
        self.assertEqual(make_example.make(self.folder), [])
        self.story = load(self.folder / "story.json")

    def problems(self):
        return page.check_story(self.story, make_example.STATS, make_example.LABELS, make_example.EVIDENCE)

    def drawn(self, run=None):
        save(self.folder / "story.json", self.story)
        if run:
            save(self.folder / "run.json", dict(make_example.RUN, **run))
        self.assertEqual(page.build(self.folder), [])
        return (self.folder / "recap.html").read_text()

    def test_a_story_of_the_wrong_shape_gets_a_plain_message(self):
        good = self.story
        for bad in ([], {}, dict(good, slides="x"), dict(good, slides=["x"]), dict(good, chapters=[{"title": "t"}]),
                    dict(good, lessons=[]), dict(good, relabel=["x"]), dict(good, slides=[{"stat": ["x"]}] * 5)):
            self.story = bad
            self.assertTrue(self.problems(), bad)

    def test_the_same_number_cannot_fill_two_slides(self):
        self.story["slides"][1]["stat"] = "messages"
        self.assertIn("more than one slide", self.problems()[0])

    def test_review_list_of_the_wrong_shape_gets_a_plain_message(self):
        self.assertTrue(page.check_review({"place": "lessons"}, self.story))

    def test_claim_without_evidence_is_refused_by_name(self):
        self.story["lessons"]["worked"][0]["evidence"] = []
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("Building one bed first", self.problems()[0])

    def test_unknown_evidence_is_refused(self):
        self.story["lessons"]["didnt"][0]["evidence"] = ["S9999"]
        self.assertIn("S9999", self.problems()[0])

    def test_refused_story_leaves_no_new_page(self):
        (self.folder / "recap.html").unlink()
        self.story["lessons"]["worked"] = []
        save(self.folder / "story.json", self.story)
        self.assertTrue(page.build(self.folder))
        self.assertFalse((self.folder / "recap.html").exists())

    def test_chapter_with_records_needs_evidence_and_one_without_does_not(self):
        self.story["chapters"][2]["evidence"] = []
        self.assertTrue(self.problems())
        self.story["chapters"][2]["months"] = ["2026-07"]
        self.story["chapters"][1]["months"] = ["2026-04", "2026-05", "2026-06"]
        self.assertEqual(self.problems(), [])

    def test_prompts_alone_is_marked(self):  # AE5
        text = self.drawn()
        claim = text[text.index("Winter questions"):][:140]
        self.assertIn("from prompts alone", claim)
        self.assertNotIn("from prompts alone", text[text.index("Building one bed first"):][:80])
        self.assertIn("January: partial detail", text)
        self.assertIn("July: no records found", text)

    def test_numbers_come_from_the_statistics(self):
        text = self.drawn()
        self.assertIn("2,316", text)
        self.assertIn("January to June, August to November", text)
        self.story["slides"][0]["stat"] = "invented"
        self.assertIn("invented", self.problems()[0])

    def test_slide_count(self):
        self.story["slides"].pop()
        self.assertIn("5 or 6 slides", self.problems()[0])
        self.story["slides"] = self.story["slides"] * 2
        self.assertIn("there are 8", " ".join(self.problems()))

    def test_every_month_is_in_exactly_one_chapter_in_order(self):
        missing = copy.deepcopy(self.story)
        self.story["chapters"][0]["months"].pop()
        self.assertIn("March is in 0 chapters", self.problems()[0])
        self.story = copy.deepcopy(missing)
        self.story["chapters"][1]["months"].insert(0, "2026-03")
        self.assertIn("March is in 2 chapters", self.problems()[0])
        self.story = missing
        self.story["chapters"].reverse()
        self.assertIn("The chapters aren't in calendar order.", self.problems())

    def test_tags_links_and_markers_are_shown_as_text(self):  # AE11
        self.story["opening"] = '<script>alert(1)</script> https://example.com {{FOOTER}} <img src=x onerror=y>'
        self.story["chapters"][0]["title"] = '"><a href="https://example.com">x</a>'
        text = self.drawn()
        self.assertIn("&lt;script&gt;alert(1)", text)
        self.assertIn("{{FOOTER}}", text)
        tags = Tags()
        tags.feed(text)
        self.assertEqual(tags.names.count("script"), 1)
        self.assertFalse({"a", "img", "link", "iframe", "object", "form"} & set(tags.names))

    def test_nothing_is_loaded_from_outside(self):
        text = self.drawn()
        self.assertEqual(re.findall(r"https?://|url\(|@import|\ssrc=|\shref=", text), [])

    def test_text_must_be_a_list_of_paragraphs(self):
        self.story["chapters"][0]["text"] = "One paragraph."
        self.story["lessons"]["carry"] = "One thing."
        self.assertEqual(len(self.problems()), 2)

    def test_a_source_can_be_reworded(self):
        self.story["relabel"] = {"S0019": {"title": "Work on the pond", "project": "outdoors"}}
        text = self.drawn()
        self.assertIn("session | outdoors | Work on the pond", text)
        self.assertNotIn("Pond liner arithmetic", text)
        self.story["relabel"] = {"S9999": {"title": "x"}, "S0019": {"date": "2026-01-01"}}
        self.assertEqual(len(self.problems()), 2)

    def test_credential_in_the_story_is_refused(self):
        self.story["chapters"][0]["text"] = ["the key is " + GITHUB]
        self.assertIn("shaped like a secret", self.problems()[0])
        self.assertNotIn(GITHUB, self.problems()[0])

    def test_range_and_label(self):  # AE1
        self.assertIn("Year-end recap | January 1, 2026 to November 20, 2026", self.drawn())
        text = self.drawn({"label": "mid-year", "end": "2026-07-15"})
        self.assertIn("Mid-year recap | January 1, 2026 to July 15, 2026", text)

    def test_unread_parts_are_reported(self):  # AE10
        self.assertNotIn("isn't finished", self.drawn())
        batches = copy.deepcopy(make_example.RUN["batches"])
        batches[-1]["read"] = False
        text = self.drawn({"batches": batches})
        self.assertIn("isn't finished. 1 of 10 parts", text)
        self.assertIn("2026-11-1", text)

    def test_review_entries_must_name_real_places(self):  # AE8
        good = [{"place": "chapter 4", "category": "employer", "what": "Names Bluebell Nurseries"},
                {"place": "lessons", "category": "people", "what": "Names a neighbor"}]
        self.assertEqual(page.check_review(good, self.story), [])
        bad = [{"place": "chapter 9", "category": "employer", "what": "x"},
               {"place": "slide 1", "category": "gossip", "what": "x"}]
        self.assertEqual(len(page.check_review(bad, self.story)), 2)

    def test_credentials_join_the_review_list_by_place(self):
        self.story["lessons"]["carry"].append("token " + GITHUB)
        found = page.credential_entries(self.story, make_example.EVIDENCE)
        self.assertEqual([(e["place"], e["category"]) for e in found], [("lessons", "credential")])
        self.assertNotIn(GITHUB, json.dumps(found))


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        self.names.append(tag)


if __name__ == "__main__":
    unittest.main()
