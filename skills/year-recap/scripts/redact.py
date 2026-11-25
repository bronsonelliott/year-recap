"""Remove credentials from text.

Recognized secrets are replaced with a label such as "[removed: GitHub token]".
The filter knows common formats only, so a secret in an unusual shape can get
through. That is why the finished page is checked again and why the person
gets a review list before sharing.
"""
import re

# Each rule: (kind, pattern, group). `group` is the part to remove: 0 means the
# whole match, 1 means only the first bracketed part, so "API_KEY=" survives
# and its value does not.
# A value in quotes. The second and fourth lines cover text that is already
# escaped, such as JSON inside JSON: \"password\": \"...\".
QUOTED = r"""(?: "(?!\[removed:\ )(?:\\.|[^"\\\n]){4,}(?:"|$)
              | \\{1,8}"(?!\[removed:\ )(?:(?!\\{1,8}")[^"\n]){4,}
              | '(?!\[removed:\ )(?:\\.|[^'\\\n]){4,}(?:'|$)
              | \\{1,8}'(?!\[removed:\ )(?:(?!\\{1,8}')[^'\n]){4,} )"""
SEPARATOR = r"""\\{0,64}["']?\s{0,200}(?::=|=>|[:=])\s{0,200}"""
# A quoted value and anything stuck to it, or a value that starts with a short quoted
# piece or a backslash.
VALUE = "(?:" + QUOTED + r"""[^\s"'\\,;}\]):]*
            | (?:"[^"\n]{0,3}"|'[^'\n]{0,3}')(?=[^\s"',;}\]):])\S{4,}
            | \\[^\s"'\\]\S{5,})"""
# One command-line word: any mix of quoted and unquoted pieces with no space between.
WORD = r"""(?: \\{1,8}"(?:(?!\\{1,8}")[^"\n])*(?:\\{1,8}")?
            | \\{1,8}'(?:(?!\\{1,8}')[^'\n])*(?:\\{1,8}')?
            | "(?:\\.|[^"\\\n])*(?:"|\\?$)
            | '[^'\n]*(?:'|$)
            | \\[^\s"']
            | [^\s"'\\] )+"""
# The start of a value with no quotes. A label, $VAR, ${VAR} and $(command) are not values.
START = r"""(?!\[removed:\ |\$\w+(?:\s|\Z)|\$\{[^}\s]*\}(?:\s|\Z)|\$\()[^\s"'\\]"""
# The rest of a name after its telling word, as in token_for_staging. Bounded, so that
# a very long run of letters stays fast.
AFTER = r"[\w-]{0,200}"

# Command-line options that carry a secret. Every option is found first, and then the
# word after each one, so an option written against another can't hide it.
OPTIONS = [
    ("command-line login",
     re.compile(r"(?<![\w-])(?:--user[ \t=]{1,20}|-[A-Za-z]{0,8}u[ \t=]{0,20})(?=[^\n]{0,400}:)(?!\[removed: )")),
    ("command-line secret",
     re.compile(r"--(?:\w{1,40}-){0,5}(?:password|passwd|passphrase|token|secret|(?:api|access|private|secret)-key)"
                r"(?!-stdin)(?:-\w{1,40}){0,3}[ \t=]{1,20}(?!\[removed: )(?=\S{6}|\S*[\"'])")),
]
ONE_WORD = re.compile(WORD, re.X | re.M)

RULES = [
    ("private key",
     re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
                r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.DOTALL), 0),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), 0),
    ("API key", re.compile(r"\b(?:sk|pk|rk)[-_](?:ant-|proj-|live_|test_)?[A-Za-z0-9_-]{20,}"), 0),
    ("AWS key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"), 0),
    ("Slack token", re.compile(r"\b(?:xox[abeprs]|xapp)-[A-Za-z0-9-]{10,}"), 0),
    ("webhook address", re.compile(r"https://(?:hooks\.slack\.com/services|discord(?:app)?\.com/api/webhooks)/[^\s\"'<>]+"), 0),
    ("access token", re.compile(r"\b(?:glpat-[A-Za-z0-9_-]{16,}|npm_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}"
                                r"|(?:pplx-|gsk_|xai-|r8_|lin_api_|ntn_|tvly-)[A-Za-z0-9_-]{20,}"
                                r"|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})"), 0),
    ("basic login", re.compile(r"\bBasic\s+([A-Za-z0-9+/]{12,}={0,2})(?![\w+/])"), 1),
    ("basic login", re.compile(r"authorization\W{1,10}basic\s+([A-Za-z0-9+/=]{8,})", re.I), 1),
    ("Google key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), 0),
    ("web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?"), 0),
    ("bearer token", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{16,})", re.I), 1),
    # user:password@ , :password@ or token@ in a URL. "localhost:3000/@x" is a port, not a password.
    ("URL password", re.compile(r"""(?<![a-z0-9])[a-z][a-z0-9+.-]{0,30}://
                                    ([^\s/?\#:"'<>]*:(?!\d+/)[^\s"'<>]+|[^\s/?\#@:"'<>]{8,})@(?=[\w\[$<{%])""",
                                re.I | re.X), 1),
    # A name like DB_SECRET or access_token, a separator, then a value. A value with
    # no quotes must hold a letter and a digit, so "token: what is that" is left alone.
    ("assigned secret",
     re.compile("(?:secret|token|(?:api|access|private|secret)[ _-]?key|credential)" + AFTER + SEPARATOR +
                "(" + VALUE + r"|(?=\S*\d)(?=\S*[A-Za-z])" + START + r"\S{7,})", re.I | re.X | re.M), 1),
    # Passwords may be all digits, or words joined by symbols.
    ("password",
     re.compile("(?:password|passphrase|passwd|pwd)" + AFTER + SEPARATOR +
                "(" + VALUE + r"|(?=\S*(?:\d|\W\w))" + START + r"\S{5,})", re.I | re.X | re.M), 1),
]


def _matches(text):
    """Every secret as (start, end, kind), in order and without overlaps."""
    found = []
    for kind, pattern, group in RULES:
        for m in pattern.finditer(text):
            found.append((m.start(group), m.end(group), kind))
    for kind, pattern in OPTIONS:
        reached = 0
        for m in pattern.finditer(text):
            # Inside an earlier word, look only a short way on, so long text stays fast.
            inside = m.end() <= reached
            word = ONE_WORD.match(text, m.end(), min(len(text), m.end() + 400) if inside else len(text))
            if word:
                found.append((word.start(), word.end(), kind))
                reached = max(reached, word.end())
    # Earliest first; when two start together, keep the longer one.
    found.sort(key=lambda f: (f[0], -f[1]))
    kept = []
    for start, end, kind in found:
        if kept and start < kept[-1][1]:
            # Overlap: stretch the earlier one so no part of either survives.
            earlier = kept[-1]
            kept[-1] = (earlier[0], max(earlier[1], end), earlier[2])
        else:
            kept.append((start, end, kind))
    return kept


def find(text):
    """Where secrets are and what kind, without the secret itself."""
    return [{"kind": kind, "start": start, "end": end} for start, end, kind in _matches(text)]


def redact(text):
    """The same text with each recognized secret replaced by a label."""
    pieces = []
    position = 0
    for start, end, kind in _matches(text):
        pieces.append(text[position:start])
        pieces.append("[removed: %s]" % kind)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


# --- a password typed on its own -----------------------------------------------

NOT_A_PASSWORD = re.compile(r"^(?:[a-z]+|[/@!#].*|.*[/\\.:].*|\[.*)$")
LONE_PASSWORD = "[removed: possible password]"


def hide_lone_password(text, text_before=""):
    """Hide a message that is nothing but something shaped like a password.

    A password with no label can't be recognized in a sentence. Alone it often
    can: one unbroken word that mixes kinds of characters, or almost any odd
    word typed straight after a sudo command.
    """
    word = text.strip()
    if not 6 <= len(word) <= 64 or re.search(r"\s", word):
        return text
    if re.search(r"\bsudo\b", text_before) and not re.match(r"^[a-z]+$", word):
        return LONE_PASSWORD
    kinds = sum(bool(re.search(p, word)) for p in ("[a-z]", "[A-Z]", r"\d", r"[!@#$%^&*+=?~]"))
    if len(word) >= 8 and kinds >= 3 and not NOT_A_PASSWORD.match(word):
        return LONE_PASSWORD
    return text
