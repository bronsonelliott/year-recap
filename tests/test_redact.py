"""Tests for the secret filter. Every credential here is invented, and each is
built in pieces so secret scanners don't flag this file."""
import unittest

import support  # noqa: F401  (adds the scripts to the path)
import redact

LETTERS = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def secret(*parts):
    return "".join(parts)


GITHUB = secret("ghp", "_", LETTERS)
GITHUB_FINE = secret("github", "_pat_", LETTERS, "_", LETTERS)
ANTHROPIC = secret("sk", "-ant-", "api03-", LETTERS)
OPENAI = secret("sk", "-proj-", LETTERS)
AWS = secret("AKIA", "IOSFODNN7INVENTD")
SLACK = secret("xoxb", "-123456789012-", LETTERS)
GOOGLE = secret("AIza", "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q")
JWT = secret("eyJ", "hbGciOiJIUzI1NiJ9", ".", "eyJ", "zdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N")
KEY_BLOCK = secret("-----BEGIN ", "PRIVATE KEY-----\nMIIEvQIBADANBg\nkqhkiG9w0BAQEF\n-----END ", "PRIVATE KEY-----")


class RemovesKnownSecrets(unittest.TestCase):
    def assertGone(self, text, value):
        cleaned = redact.redact(text)
        self.assertNotIn(value, cleaned)
        self.assertIn("[removed", cleaned)
        return cleaned

    def test_provider_keys(self):
        for value in (GITHUB, GITHUB_FINE, ANTHROPIC, OPENAI, AWS, SLACK, GOOGLE, JWT):
            with self.subTest(value[:6]):
                cleaned = self.assertGone("here it is: " + value + " ok?", value)
                self.assertTrue(cleaned.startswith("here it is: "))
                self.assertTrue(cleaned.endswith(" ok?"))

    def test_other_provider_prefixes(self):
        for prefix in ("pplx" + "-", "gsk" + "_", "xai" + "-", "r8" + "_", "lin" + "_api_", "ntn" + "_", "tvly" + "-"):
            with self.subTest(prefix):
                self.assertGone("use this API key instead " + prefix + LETTERS, LETTERS)
        for words in ("the xai-based one", "gsk_notes", "an ntn_ prefix"):
            self.assertEqual(redact.redact(words), words)

    def test_assignment_keeps_the_name_and_drops_the_value(self):
        for line in ('API_KEY="correct-horse-42"', "password: hunter2hunter2",
                     "export DB_SECRET=s3cr3tvalue99", '"access_token": "abc123def456"',
                     "client_secret = 'zxcvbnm12345'"):
            with self.subTest(line):
                name = line.replace("export ", "").split("=")[0].split(":")[0].strip(' "')
                cleaned = redact.redact(line)
                self.assertIn(name, cleaned)
                self.assertIn("[removed", cleaned)
        self.assertNotIn("hunter2", redact.redact("password: hunter2hunter2"))
        self.assertNotIn("correct-horse", redact.redact('API_KEY="correct-horse-42"'))

    def test_whole_key_block_is_removed(self):
        cleaned = self.assertGone("before\n" + KEY_BLOCK + "\nafter", "MIIEvQIBADANBg")
        self.assertNotIn("kqhkiG9w0BAQEF", cleaned)
        self.assertIn("before", cleaned)
        self.assertIn("after", cleaned)

    def test_key_block_with_no_end_line_is_removed_to_the_end(self):
        cut = KEY_BLOCK[:60]
        cleaned = redact.redact("before\n" + cut)
        self.assertNotIn("MIIEvQ", cleaned)
        self.assertIn("before", cleaned)

    def test_pgp_key_block(self):
        block = secret("-----BEGIN PGP ", "PRIVATE KEY BLOCK-----\nlQOYBGabc\n-----END PGP ", "PRIVATE KEY BLOCK-----")
        self.assertNotIn("lQOYBG", redact.redact("a\n" + block + "\nb"))
        self.assertIn("b", redact.redact("a\n" + block + "\nb"))

    def test_no_piece_of_an_awkward_value_survives(self):
        for text, pieces in (
            ("password: ab$cd,ef;gh(ij{kl9", ["ab$cd", "ef;gh", "ij{kl9"]),
            ("API_TOKEN=Ab9,Cd8;Ef7$Gh6", ["Ab9", "Cd8", "Ef7", "Gh6"]),
            ('password = "four words with spaces"', ["four", "spaces"]),
            ('secret: "[starts with a bracket 9"', ["starts", "bracket"]),
            ('{\\"password\\": \\"nested-value-1\\"}', ["nested-value-1"]),
            ('api_key := "go-style-value"', ["go-style"]),
            ("'token' => 'ruby-style-value'", ["ruby-style"]),
            ('password="never closed 123', ["never", "closed"]),
            ("password: 83920174", ["83920174"]),
            ('password="open on line one 1\nnext line', ["open on line one"]),
            ('{\\"secret\\": \\"back\\\\slash-inside-1\\"}', ["back", "slash-inside"]),
        ):
            with self.subTest(text):
                cleaned = redact.redact(text)
                for piece in pieces:
                    self.assertNotIn(piece, cleaned)

    def test_more_services_and_command_lines(self):
        for value in (secret("glpat", "-", LETTERS), secret("npm", "_", LETTERS), secret("hf", "_", LETTERS),
                      secret("SG", ".", LETTERS, ".", LETTERS), secret("xapp", "-1-", LETTERS),
                      secret("https://hooks.slack", ".com/services/", "T000/B000/", LETTERS)):
            with self.subTest(value[:8]):
                self.assertGone("use " + value, value)
        for text, piece in (("Authorization: Basic cGF0OnNlY3JldDEyMw==", "cGF0OnNl"),
                            ("curl -s -u pat:hunter22 https://example.com", "hunter22"),
                            ("deploy --password hunter2abc --now", "hunter2abc"),
                            ("PASSWORD=$tr0ng(pass", "tr0ng"), ("TOKEN={ab12cd34ef", "ab12cd34ef"),
                            ("passphrase: correct-horse-9", "correct-horse"),
                            ("authorization: basic cGF0OnNlY3JldDEyMw==", "cGF0OnNl"),
                            ('deploy --password "two words 7" --now', "words"),
                            ("curl -su pat:hunter22 https://example.com", "hunter22"),
                            ("curl -upat:hunter22 https://example.com", "hunter22"),
                            ("curl -s \\\n  -u 'pat:pass phrase' https://example.com", "phrase'"),
                            ('curl -u ":hunter22" https://example.com', "hunter22"),
                            ('curl -u "$U":hunter22 https://example.com', "hunter22"),
                            ("curl -u 'pat':'hunter22' https://example.com", "hunter22"),
                            ('curl -u \\"pat:hunter 22\\" x', "hunter"),
                            ('curl -u "pat:hunter 22', "22"),
                            ('curl -u "pat:hun\\"ter22" x', "ter22"),
                            ('curl -u "a:b1";curl -u "c:hunter22"', "hunter22"),
                            ("deploy --token abc;def&gh123 x", "gh123"),
                            ("curl -u pat:hun;ter&22 x", "ter&22"),
                            ("sh -c 'curl -u \\'pat:hunter 22\\' x'", "hunter"),
                            ('deploy --token "line one 77\\' + "\n next", "77"),
                            ('curl --user-agent="bot:1.0" -u pat:hunter22 x', "hunter22"),
                            ("deploy --token 'abcd1'\\''efgh2'", "efgh2"),
                            ('deploy --token "$PRE"tail9876', "tail9876"),
                            ('deploy --token "a"bcdef99', "bcdef99"),
                            ("password" + " " * 60 + "= hunter2abc", "hunter2abc"),
                            ("x" * 90 + "_token = ab12cd34ef56", "ab12cd34ef56"),
                            ("${DATABASE_URL:-postgres://pat:pa55word@h.example.com/x}", "pa55word"),
                            ("see...https://pat:pa55word@h.example.com", "pa55word")):
            with self.subTest(text):
                self.assertNotIn(piece, redact.redact(text))

    def test_names_with_spaces_and_awkward_values(self):
        for text, piece in (("API key: ab12cd34ef56", "ab12cd34ef56"), ("Secret Access Key: ab12cd34ef56", "ab12cd34ef56"),
                            ('PASSWORD="abcd"efgh99', "efgh99"), ('TOKEN="ab"cdef99gh', "cdef99gh"),
                            ("PASSWORD=\\$hunter22", "hunter22"),
                            ("x --db-password hunter22", "hunter22"), ("x --aws-secret-access-key ab12cd34", "ab12cd34"),
                            ("x --github-token=ab12cd34", "ab12cd34"),
                            ("postgres://pat:hunter22@${HOST}/x", "hunter22"), ("postgres://pat:hunter22@<host>/x", "hunter22"),
                            ("https://pat@example.com:hunter22@h.example.com", "hunter22")):
            with self.subTest(text):
                self.assertNotIn(piece, redact.redact(text))
        self.assertIn('"name":"beans"', redact.redact('{"token":"abcd1234","name":"beans"}'))

    def test_an_option_cannot_hide_the_next_one(self):
        for text, pieces in (("curl -u|--user bob:hunter22 x", ["hunter22"]),
                             ("--password foo1234|--token bar12345", ["foo1234", "bar12345"]),
                             ("run \\'-u a:bone11\\' \\'-u c:hunter22\\'", ["bone11", "hunter22"]),
                             ("sh -c '-u a:bone11' -u 'c:hunter 22'", ["bone11", "hunter", "22"]),
                             ("x --token abcdef1 --secret ghijkl2", ["abcdef1", "ghijkl2"])):
            with self.subTest(text):
                cleaned = redact.redact(text)
                for piece in pieces:
                    self.assertNotIn(piece, cleaned)

    def test_long_unbroken_text_is_fast(self):
        import time
        for text in ("a" * 200000, "a-" * 100000, "token" * 40000, "\\\\" * 50000, "a.b+" * 50000, "curl " * 40000,
                     "password " * 20000, "://" * 60000, "--token " * 20000, "-u " * 60000, "-u \\\"" * 30000, "-u a:" * 30000,
                     "-ua:b;" * 30000, "--token a|" * 20000, "--a-" * 50000, "api key " * 25000, "token=\"a\"" * 20000, "-u 'a:" * 30000):
            started = time.time()
            redact.redact(text)
            self.assertLess(time.time() - started, 2, text[:8])

    def test_urls_of_every_shape(self):
        for url, piece in (("redis://:pa55word@cache.example.com", "pa55word"),
                           ("https://pat:p@ss/w0rd@host.example.com/x", "ss/w0rd"),
                           ("https://abcdef123456@git.example.com/r", "abcdef123456")):
            with self.subTest(url):
                cleaned = redact.redact(url)
                self.assertNotIn(piece, cleaned)
                self.assertIn("example.com", cleaned)

    def test_bearer_header(self):
        cleaned = self.assertGone("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef")
        self.assertIn("Bearer", cleaned)

    def test_url_keeps_host_and_loses_credentials(self):
        cleaned = self.assertGone("postgres://pat:pa55word@db.example.com/x", "pa55word")
        self.assertIn("db.example.com/x", cleaned)


class LeavesOrdinaryTextAlone(unittest.TestCase):
    def test_prose_about_secrets(self):
        for text in (
            "I forgot my password again, how do I reset the token?",
            "The session used 4,000 tokens and a secret recipe.",
            "Token: what does that word mean?",
            "Write to pat@example.com about the key findings.",
            "Use the API key page in settings.",
            "docker login --password-stdin registry.example.com",
            "We set --max-tokens 4096 for the run.",
            "commit 3f2a9c1 fixed the sk-learn import",
            "Usage was tokens: 4,000-5,000 in total.",
            "See http://localhost:4873/@types/node and https://medium.com/@someone.",
            "My password: forgotten.",
            "Set API_TOKEN=$TOKEN_FROM_VAULT_1 first, or PASSWORD=${DB_PASS1}",
            "Run python3 -u script.py and check the menu-u option.",
            "We cover Basic information about gardening.",
        ):
            with self.subTest(text):
                self.assertEqual(redact.redact(text), text)

    def test_empty(self):
        self.assertEqual(redact.redact(""), "")


class PasswordOnItsOwn(unittest.TestCase):
    def test_mixed_word_alone_is_hidden(self):
        self.assertEqual(redact.hide_lone_password("Gr33n!Bean"), redact.LONE_PASSWORD)

    def test_after_sudo_almost_any_odd_word_is_hidden(self):
        self.assertEqual(redact.hide_lone_password("beanpole7", "! sudo softwareupdate -i"), redact.LONE_PASSWORD)
        self.assertEqual(redact.hide_lone_password("Beanpole", "sudo ls"), redact.LONE_PASSWORD)
        self.assertEqual(redact.hide_lone_password("continue", "sudo ls"), "continue")
        self.assertEqual(redact.hide_lone_password("beanpole7", "ls"), "beanpole7")

    def test_ordinary_messages_are_left_alone(self):
        for text in ("yes", "continue", "/plan-Beds2", "~/Notes/2026.md", "README.md", "claude-opus-5-5", "Gr33n!Bean is ours",
                     "https://Example.com/a1", "!ls", "@Garden2", "PR#12", "S0012", "[pasted text]"):
            with self.subTest(text):
                self.assertEqual(redact.hide_lone_password(text), text)


class FindsWithoutRevealing(unittest.TestCase):
    def test_reports_kind_and_position_only(self):
        text = "x " + GITHUB + " y"
        found = redact.find(text)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "GitHub token")
        self.assertEqual(found[0]["start"], 2)
        self.assertNotIn(GITHUB, repr(found))

    def test_cleaned_text_has_nothing_left_to_find(self):
        text = " ".join([GITHUB, ANTHROPIC, AWS, SLACK, GOOGLE, JWT, KEY_BLOCK,
                         'API_KEY="correct-horse-42"', "Bearer abcdef0123456789abcdef",
                         "postgres://pat:pa55word@db.example.com/x"])
        self.assertEqual(redact.find(redact.redact(text)), [])


if __name__ == "__main__":
    unittest.main()
