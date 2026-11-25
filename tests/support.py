"""Builds an invented home folder for tests. Nothing here comes from real history."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "year-recap"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(ROOT / "example"))

COWORK = "Library/Application Support/Claude/local-agent-mode-sessions"


def stamp(text):
    """"2026-03-01 09:00" in local time, as the transcripts write it."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").astimezone().isoformat()


class FakeHome(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name).resolve() / "home"
        (self.home / ".claude" / "projects").mkdir(parents=True)

    def records(self, turns, cwd="", launch="cli", session="s"):
        """turns: [("2026-03-01 09:00", "what they typed", "what Claude replied")]"""
        out = []
        for when, said, reply in turns:
            base = {"timestamp": stamp(when), "cwd": cwd, "entrypoint": launch, "sessionId": session}
            out.append(dict(base, type="user", message={"role": "user", "content": said}))
            out.append(dict(base, type="assistant",
                            message={"role": "assistant", "content": [{"type": "text", "text": reply}]}))
        return out

    def write(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records),
                        encoding="utf-8")
        return path

    def code_session(self, session, turns, folder="-home-garden", cwd=None, launch="cli", extra=()):
        cwd = str(self.home / "garden") if cwd is None else cwd
        return self.write(self.home / ".claude" / "projects" / folder / (session + ".jsonl"),
                          self.records(turns, cwd, launch, session) + list(extra))

    def cowork_session(self, session, turns, audit=False, inside=""):
        base = self.home / COWORK / "account" / "space" / inside / ("local_" + session)
        if audit:
            self.write(base / "audit.jsonl", self.records(turns))
        return self.write(base / ".claude" / "projects" / "-sessions-calm-river" / (session + ".jsonl"),
                          self.records(turns, "/sessions/calm-river", "local-agent", session))

    def history(self, rows):
        """rows: [("2026-03-01 09:00", "prompt", "session id")]"""
        self.write(self.home / ".claude" / "history.jsonl", [
            {"display": text, "timestamp": datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp() * 1000,
             "project": str(self.home / "garden"), "sessionId": session, "pastedContents": {}}
            for when, text, session in rows])

    def git(self, folder, *arguments, **env):
        full = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", **env)
        full.pop("GIT_DIR", None)
        subprocess.run(["git", "-C", str(folder)] + list(arguments), env=full, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def git_identity(self, name="Pat Sample", email="pat@example.com"):
        self.git(self.home, "config", "--global", "user.name", name)
        self.git(self.home, "config", "--global", "user.email", email)

    def commit(self, folder, when, subject, name="Pat Sample", email="pat@example.com"):
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / ".git").exists():
            self.git(folder, "init", "-q")
        self.git(folder, "commit", "-q", "--allow-empty", "-m", subject,
                 GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_AUTHOR_DATE=stamp(when),
                 GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, GIT_COMMITTER_DATE=stamp(when))
