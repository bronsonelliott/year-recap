"""Nothing private may be in the repository, now or in any past commit.

The private terms are not written here. They are read from CLAUDE.md, which
git ignores, so a copy without that file skips that part and says so.
"""
import re
import subprocess
import unittest
from pathlib import Path

from support import ROOT

BRAND = "".join(["Spot", "ify"])
BRAND_FEATURE = "".join(["Wrap", "ped"])


def git(*arguments):
    done = subprocess.run(["git", "-C", str(ROOT)] + list(arguments), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""


def private_terms():
    path = ROOT / "CLAUDE.md"
    if not path.exists():
        return []
    section = path.read_text(encoding="utf-8").split("## Private terms", 1)
    return re.findall(r"^- (.+)$", section[1], re.M) if len(section) == 2 else []


def personal_values():
    """This machine's home path and git email, worked out as the test runs."""
    return [v for v in (str(Path.home()), git("config", "user.email").strip()) if v]


@unittest.skipUnless((ROOT / ".git").exists(), "not a git checkout")
class PublicSafety(unittest.TestCase):
    def tracked(self):
        return [ROOT / name for name in git("ls-files").splitlines() if (ROOT / name).exists()]

    def assertClean(self, terms):
        def holds(text, term):
            return re.search(r"(?<!\w)%s(?!\w)" % re.escape(term), text, re.I)

        for path in self.tracked():
            if path.name == "LICENSE":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, term in enumerate(terms):
                self.assertFalse(holds(text, term), "%s holds private term %d" % (path.name, number + 1))
        history = git("log", "-p", "--all", "--no-color", "--format=%s%n%b", "--", ".", ":(exclude)LICENSE")
        for number, term in enumerate(terms):
            self.assertFalse(holds(history, term), "a past commit holds private term %d" % (number + 1))

    def test_no_home_path_or_email(self):
        self.assertClean(personal_values())

    def test_no_private_terms(self):
        terms = private_terms()
        if not terms:
            self.skipTest("SKIPPED: CLAUDE.md isn't here, so private names were not checked")
        self.assertClean(terms)

    def test_no_borrowed_branding(self):
        self.assertClean([BRAND, BRAND_FEATURE])

    def test_real_recaps_and_history_are_ignored(self):
        for name in ("recap.html", "recap-page.html", "story.json", "session.jsonl", "CLAUDE.md", "MEMORY.md",
                     "skills/recap.html", "example/session.jsonl"):
            self.assertTrue(git("check-ignore", "--no-index", name).strip(), "%s is not ignored" % name)
        self.assertFalse(git("check-ignore", "--no-index", "example/recap.html").strip())

    def test_readme_commands_exist(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in re.findall(r"cp -R (\S+)|\]\(([\w./-]+)\)", text):
            target = name[0] or name[1]
            self.assertTrue((ROOT / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
