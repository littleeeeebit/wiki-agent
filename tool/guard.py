"""PreToolUse: reduce a tool call to its effects and deny what leaves the task's grant.

A shell command is lexed and parsed into commands joined by `;`, `&&`, `||`,
`|` and newlines, with `$(...)`, backticks, groups, redirections and here
documents as children, and the strings `bash -c`, `sh -c`, `pwsh -Command`,
`powershell -Command` and `cmd /c` hand on parsed again one level down. Every
node reduces to effects: `read`, `write(path)`, `delete(path)`,
`spawn(executable)`, `network(host)`, and `top(reason)` for what cannot be
decided before it runs. The call passes when every effect is inside the grant
(`grant`); there is no list of command patterns. What an executable does is
read off a closed table of programs and the flags each understands
(`guard_models.TABLE`, `guard_windows.PS_ROWS`/`CMD_ROWS`): a flag outside its
row is `top`, and a name with no row is a `spawn`, which is never judged.

Soundness over completeness: anything the models cannot decide becomes `top`,
and `top` is never granted. The refusal is read by the person and stays Korean.
"""

# First, so the wait on the other imports and on stdin is already being watched.
import hook_diagnostics  # noqa: F401
from collections import ChainMap
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import json
import ntpath
import os
from pathlib import Path
import posixpath
import re
import sys

HERE = Path(__file__).resolve().parent
DEPTH = 8
# The most directories a command may be in at once; past it the call is `top`, so work stays linear.
STATES = 32
READ = ("read", "")


@dataclass
class Word:
    text: str = ""
    why: str = ""                       # set when the value is only known at run time
    subs: list = field(default_factory=list)   # (dialect, code) run while it expands
    quoted: bool = False
    method: str = ""                    # PowerShell `.Name(` directly after this word
    recv: str = ""                      # the `[Type]` a PowerShell `[Type]::method(` calls on
    raw: str = ""                       # the source as written, for `NAME=` and keywords
    vars: bool = False                  # holds `MARK`s for plain variables, bound in `simple`

    def taint(self, why: str) -> None:
        self.why = self.why or why


# A plain `$NAME` inside a word, `\0` + `q`/`u` (quoted or not) + name + `\1`, until
# `bind` gives it the literal the same script assigned or marks the word unknown.
MARK = re.compile("\0([qu])([A-Za-z_]\\w*)\1")


@dataclass(frozen=True)
class Ctx:
    d: str                              # "posix", "ps" or "cmd"
    cwds: frozenset                     # every directory the command may run in; None = unknown
    home: str
    temp: str
    windows: bool = True
    depth: int = 0
    remotes: tuple = ()                 # ((name, host), ...) of the checkout's git remotes
    stdin: object = None                # a here document's Word, or None
    piped: bool = False
    env: tuple = ()                   # assignments in front of the current command
    # name -> literal the script assigned before here; a read-only view, shared rather than copied per command
    consts: Mapping = field(default_factory=dict, compare=False)
    sink: object = field(default=None, compare=False)   # a list collecting nested shell strings, or None
    repo: str = ""                      # the checkout `remotes` were read from, "" when none was

    def deeper(self, d: str | None = None, fresh: bool = True, **kw) -> "Ctx":
        # A new shell sees none of this one's unexported variables; `$(...)` and groups do.
        return replace(self, d=d or self.d, depth=self.depth + 1, stdin=None, piped=False, env=(),
                       consts={} if fresh else self.consts, **kw)


def top(reason: str) -> set:
    return {("top", reason)}


# A member name the lexer saw after `.`/`::` but could not read: a `$var` or a quoted string.
DYNAMIC_MEMBER = "\0member"
# How a group stands among a command's words: only `{...}` is a literal script block, code the guard reads.
GROUP_TEXT = {"(": "(...)", "{": "{...}", "@(": "@(...)", "@{": "@{...}"}
BLOCK = "{...}"


class Unclosed(ValueError):
    """A quote, substitution or here-string left open: the shell refuses the string, the guard cannot read it."""


class Unbounded(ValueError):
    """More possible directories than `STATES`: the call is `top` rather than tracked further."""


def states(cwds) -> frozenset:
    cwds = frozenset(cwds)
    if len(cwds) > STATES:
        raise Unbounded(f"more than {STATES} possible working directories")
    return cwds


# Arithmetic evaluation expands what a variable holds as an expression, and `a[$(…)]` in that value runs.
ARITH = ("arith", "")


def find(s: str, sub: str, i: int, what: str) -> int:
    j = s.find(sub, i)
    if j < 0:
        raise Unclosed(what)
    return j


# -- Lexing ------------------------------------------------------------------

OPS = {"posix": re.compile(r"&&|\|\||;;&?|;&|\|&|[;&|()\n]"),
       "ps": re.compile(r"&&|\|\||@\{|@\(|[;&|(){}\n]"),
       "cmd": re.compile(r"&&|\|\||[&|()\n]")}
REDIRS = {"posix": re.compile(r"(\d+|&)?(<<<|<<-|<<|<>|<&|>>|>&|>\||>|<)"),
          "ps": re.compile(r"(\d|\*)?(>>|>&|>|<)"),
          "cmd": re.compile(r"(\d)?(>>|>&|<&|>|<)")}
STOP = {"posix": set(" \t\r\n;&|()<>"), "ps": set(" \t\r\n;&|(){}<>"), "cmd": set(" \t\r\n&|()<>")}
OPENERS = {"posix": {"("}, "ps": {"(", "{", "@{", "@("}, "cmd": {"("}}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([\w.-]+)\1")


def close_paren(s: str, i: int, d: str) -> int:
    """Index of the `)` closing an opener just before `i`, honouring quotes."""
    depth, n, esc, bodies = 1, len(s), "`" if d == "ps" else "\\", []
    while i < n:
        c = s[i]
        if c == esc:
            i += 2
            continue
        if d == "posix" and c == "<" and (m := HEREDOC.match(s, i)):     # in place: a slice would copy
            bodies.append(m.group(2))
            i = m.end()
            continue
        if c == "\n" and bodies:
            # A here document's body is data: its quotes and parentheses close nothing.
            for delim in bodies:
                end = re.compile(rf"^\t*{re.escape(delim)}\r?$", re.M).search(s, i + 1)
                i = end.end() if end else n
            bodies = []
            continue
        if c == "'":
            i = find(s, "'", i + 1, "an unclosed quote") + 1
            continue
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == esc else 1
            i += 1
            continue
        depth += c == "("
        depth -= c == ")"
        if not depth:
            return i
        i += 1
    raise Unclosed("an unclosed parenthesis")


def dollar(s: str, i: int, w: Word, buf: list, d: str, mark: str = "") -> int:
    """Read the expansion at `s[i] == "$"`; `mark` is "q" or "u" where a plain name may be bound later."""
    nxt = s[i + 1:i + 2]
    plain = re.match(r"\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*)", s[i + 1:])
    if mark and plain and not (d == "ps" and s[i + 1 + plain.end():i + 2 + plain.end()] == ":"):
        name = plain.group(1) or plain.group(2)
        buf.append(f"\0{mark}{name.lower() if d == 'ps' else name}\1")
        w.vars = True
        return i + 1 + plain.end()
    if d == "posix" and s.startswith("$((", i):
        end = close_paren(s, close_paren(s, i + 3, d) + 1, d)
        w.subs.append(ARITH)
        w.taint("variable")
        return end + 1
    if d == "posix" and nxt == "[":
        j = find(s, "]", i, "an unclosed $[")
        w.subs.append(ARITH)
        w.taint("variable")
        return j + 1
    if nxt == "(":
        j = close_paren(s, i + 2, d)
        w.subs.append((d, s[i + 2:j]))
        w.taint("substitution")
        buf.append(s[i:j + 1])
        return j + 1
    if nxt == "{":
        j = find(s, "}", i, "an unclosed ${")
        body = s[i + 2:j]
        # `${a[i]}` evaluates `i`, `${x:i}` evaluates its offset and `${!x}` expands the name `x` holds.
        if d == "posix" and (body.startswith("!") or re.match(r"#?\w+\[(?![@*]\]|\d+\])", body)
                             or re.match(r"#?\w+:(?![-=?+])", body)):
            w.subs.append(ARITH)
        scan(body, w, d)
        w.taint("variable")
        buf.append(s[i:j + 1])
        return j + 1
    if d == "posix" and nxt == "'":
        j = i + 2
        while j < len(s) and s[j] != "'":
            j += 2 if s[j] == "\\" else 1
        if j >= len(s):
            raise Unclosed("an unclosed quote")
        body = s[i + 2:j]
        if "\\" in body:
            # `$'...'` decodes C escapes the guard does not: `\x2e` is `.`, so a
            # resolved path would differ from what the shell runs. Leave it undecided.
            w.taint("ANSI-C quoting")
        buf.append(body)
        w.quoted = True
        return j + 1
    name = re.match(r"(?:[A-Za-z_]\w*:)?[A-Za-z_]\w*|[_?$^]" if d == "ps" else r"[A-Za-z_]\w*|[0-9@*#?$!-]",
                    s[i + 1:])
    if not name:
        buf.append("$")
        return i + 1
    w.taint("variable")
    buf.append("$" + name.group())
    return i + 1 + name.end()


def scan(text: str, w: Word, d: str) -> None:
    """Expansions inside an expanding string: double quotes, unquoted here documents."""
    i, n, esc = 0, len(text), "`" if d == "ps" else "\\"
    while i < n:
        c = text[i]
        if c == esc:
            i += 2
        elif c == "$":
            i = dollar(text, i, w, [], d)
        elif c == "`" and d == "posix":
            j = find(text, "`", i + 1, "an unclosed backtick")
            w.subs.append((d, text[i + 1:j]))
            w.taint("substitution")
            i = j + 1
        elif c == "%" and d == "cmd" and re.match(r"%[^%\s]+%", text[i:]):
            w.taint("variable")
            i += 1
        else:
            i += 1


def dquote(s: str, i: int, w: Word, buf: list, d: str) -> int:
    """Read a double-quoted run starting after its `"`; return the index past it."""
    n, esc, start = len(s), "`" if d == "ps" else "\\", i
    while i < n and s[i] != '"':
        if s[i] == esc and d != "cmd":
            if d == "ps" or s[i + 1:i + 2] in '$`"\\\n':
                buf.append(s[i + 1:i + 2])
                i += 2
                continue
        if s[i] == "$" and d != "cmd":
            i = dollar(s, i, w, buf, d, "q")
            continue
        if s[i] == "`" and d == "posix":
            j = find(s, "`", i + 1, "an unclosed backtick")
            w.subs.append((d, s[i + 1:j]))
            w.taint("substitution")
            i = j + 1
            continue
        buf.append(s[i])
        i += 1
    if i >= n and d != "cmd":
        raise Unclosed("an unclosed quote")
    if d == "cmd":
        scan(s[start:i], w, d)
    w.quoted = True
    return i + 1


def read_word(s: str, i: int, d: str) -> tuple[Word, int]:
    w, buf, start, n = Word(), [], i, len(s)
    if d == "ps" and s[i:i + 2] in ("@'", '@"') and s[i + 2:i + 3] in ("\n", "\r"):
        end = find(s, "\n" + s[i + 1] + "@", i + 2, "an unclosed here-string")
        w.text, w.quoted = s[i + 3:end], True
        if s[i + 1] == '"':
            scan(w.text, w, d)
        return w, min(end + 3, n)
    while i < n and s[i] not in STOP[d]:
        c = s[i]
        if (d == "posix" and c == "\\") or (d == "ps" and c == "`") or (d == "cmd" and c == "^"):
            buf.append(s[i + 1:i + 2])
            i += 2
            continue
        if c == "'" and d != "cmd":
            j, part = i + 1, []
            while j < n:
                if s[j] == "'" and not (d == "ps" and s[j + 1:j + 2] == "'"):
                    break
                part.append(s[j])
                j += 2 if s[j] == "'" else 1
            if j >= n:
                raise Unclosed("an unclosed quote")
            buf.append("".join(part))
            w.quoted, i = True, j + 1
            continue
        if c == '"':
            i = dquote(s, i + 1, w, buf, d)
            continue
        if c == "$" and d != "cmd":
            i = dollar(s, i, w, buf, d, "u")
            continue
        if c == "`" and d == "posix":
            j = find(s, "`", i + 1, "an unclosed backtick")
            w.subs.append((d, s[i + 1:j]))
            w.taint("substitution")
            buf.append(s[i:j + 1])
            i = j + 1
            continue
        if d == "cmd" and c in "%!" and (m := re.match(r"([%!])[^%!\s]+\1", s[i:])):
            w.taint("variable")
            buf.append(m.group())
            i += m.end()
            continue
        if c in "*?" or (c == "[" and d != "cmd" and re.match(r"\[[^\s\]]*\]", s[i:])):
            w.taint("glob")
        if c == "{" and d == "posix" and re.match(r"\{[^{}\s]*(,|\.\.)[^{}\s]*\}", s[i:]):
            w.taint("glob")
        buf.append(c)
        i += 1
    w.text, w.raw = "".join(buf), s[start:i]
    if d == "ps" and w.text in ("?", "%") and w.why == "glob":
        w.why = ""                      # Where-Object and ForEach-Object
    if d == "ps" and s[i:i + 1] == "(":
        tail = s[start:i]
        # Whatever follows the last `.`/`::` is the member: an identifier is judged by its name, anything
        # else (an escape, a quote, a variable, nothing) names a member only known at run time.
        if m := re.search(r"(?:\[([\w.`\[\]]+)\])?(?:\.|::)([^.:]*)$", tail):
            name = m.group(2)
            w.recv, w.method = m.group(1) or "", name if re.fullmatch(r"[A-Za-z_]\w*", name) else DYNAMIC_MEMBER
            if name.lower() in ("foreach", "where", "psforeach", "pswhere"):
                j = i + 1
                while j < len(s) and s[j] in " \t`\r\n":
                    j += 1
                # `.ForEach({block})`/`.Where({block})` run the block, which is reduced as a group;
                # `.ForEach('Delete')` names a method on each item and must be judged (left as a method).
                if s[j:j + 1] == "{":
                    w.method = ""
    return w, i


def heredoc(s: str, i: int, w: Word, delim: str, strip: bool, d: str) -> int:
    start, n = i, len(s)
    while i < n:
        j = s.find("\n", i)
        line = s[i:n if j < 0 else j].rstrip("\r")
        if (line.lstrip("\t") if strip else line) == delim:
            w.text = s[start:i]
            return n if j < 0 else j + 1
        i = n if j < 0 else j + 1
    w.text = s[start:]
    return n


def lex(s: str, d: str) -> list:
    out, i, n, pending = [], 0, len(s), []
    while i < n:
        c = s[i]
        if c in " \t\r\f\v":
            i += 1
            continue
        if (d == "posix" and s.startswith("\\\n", i)) or (d == "ps" and s.startswith("`\n", i)):
            i += 2
            continue
        if c == "\n":
            out.append(("op", "\n"))
            i += 1
            for w, delim, strip in pending:
                i = heredoc(s, i, w, delim, strip, d)
                if not w.quoted:
                    scan(w.text, w, d)
            pending = []
            continue
        if d == "ps" and s.startswith("<#", i):
            i = find(s, "#>", i, "an unclosed comment") + 2
            continue
        if c == "#" and d != "cmd":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if d == "posix" and s.startswith("((", i):
            j = find(s, "))", i, "an unclosed ((")
            out.append(("w", Word("true", subs=[ARITH])))
            i = j + 2
            continue
        if d == "posix" and s[i:i + 2] in ("<(", ">("):
            j = close_paren(s, i + 2, d)
            out.append(("w", Word(s[i:j + 1], "substitution", [(d, s[i + 2:j])])))
            i = j + 1
            continue
        m = REDIRS[d].match(s, i)
        if m and not (d == "posix" and m.group(1) == "&" and m.group(2) in ("<", "<<", "<<<")):
            i = m.end()
            while i < n and s[i] in " \t":
                i += 1
            target, i = read_word(s, i, d) if i < n and s[i] not in "\n;|&" else (Word(), i)
            op = m.group(2)
            if op in ("<<", "<<-"):
                body = Word(quoted=target.quoted)
                pending.append((body, target.text, op == "<<-"))
                target = body
            out.append(("r", m.group(1) or "", op, target))
            continue
        m = OPS[d].match(s, i)
        if m:
            out.append(("op", m.group()))
            i = m.end()
            continue
        w, j = read_word(s, i, d)
        out.append(("w", w))
        i = max(j, i + 1)
    return out


# -- Parsing -----------------------------------------------------------------

SEPS = {";", "&&", "||", "|", "|&", "&", "\n", ";;", ";&", ";;&"}


def closing(toks: list, i: int) -> int:
    depth = 0
    for j in range(i, len(toks)):
        if toks[j][0] == "op" and toks[j][1] in ("(", "{", "@{", "@("):
            depth += 1
        elif toks[j][0] == "op" and toks[j][1] in (")", "}"):
            depth -= 1
            if not depth:
                return j
    return len(toks)


def commands(toks: list, d: str) -> list:
    """The command tree's one level: `[(join, items)]`, a group being `("g", opener, tokens)`."""
    out, cur, join, i = [], [], ";", 0
    while i < len(toks):
        kind, val = toks[i][0], toks[i][1]
        if kind == "op" and val in OPENERS[d]:
            j = closing(toks, i)
            cur.append(("g", val, toks[i + 1:j]))
            i = j + 1
            continue
        if kind == "op" and d == "ps" and val == "&" and not cur:
            cur.append(("call",))
        elif kind == "op" and (val in SEPS or val in (")", "}")):
            # An unmatched `)` ends a `case` pattern, so it separates as well.
            if cur:
                out.append((join, cur))
                cur, join = [], val
            elif not (val == "\n" and join in ("&&", "||", "|")):
                join = val
        else:
            cur.append(toks[i])
        i += 1
    if cur:
        out.append((join, cur))
    return out


OPEN_KW, CLOSE_KW = {"if", "while", "until", "for", "case", "select", "{"}, {"fi", "done", "esac", "}"}
# Loops, functions and traps run their bodies any number of times, or wherever they are called: not modelled.
REPEATS = {"while", "until", "for", "select", "foreach", "do", "function", "filter", "trap"}
# Names whose value changes without an assignment in the script.
DYNAMIC = {"pwd", "oldpwd", "random", "seconds", "lineno", "bashpid", "reply", "optarg", "optind", "ifs", "_",
           "args", "input", "matches", "this", "psitem", "lastexitcode", "error", "home", "pid", "null", "true",
           "false", "host", "myinvocation", "psscriptroot", "pscommandpath", "foreach", "switch", "profile"}


def bound_once(src: str, d: str) -> set:
    """Names `src` binds exactly once, counting every form that can bind one; empty when anything rebinds blindly."""
    from collections import Counter

    if d == "ps":
        if re.search(r"(?i)\b(set-variable|new-variable|sv|nv|invoke-expression|iex)\b|\[ref\]|(?:^|[;\s])\.\s", src):
            return set()
        names = re.findall(r"\$(?:script:|global:|local:|private:)?([A-Za-z_]\w*)\s*(?:[+\-*/%]|\?\?)?=(?!=)", src)
        names += re.findall(r"(?i)\b(?:foreach|for)\s*\(\s*\$(\w+)", src)
        names += re.findall(r"(?i)-(?:outvariable|ov|errorvariable|ev|pipelinevariable|pv|informationvariable|iv|"
                            r"warningvariable|wv)\s*:?\s*['\"]?(\w+)", src)
        counts = Counter(n.lower() for n in names)
    else:
        # A quoted here document's body is data: nothing in it binds a name in this shell.
        src = re.sub(r"<<-?\s*(['\"])([\w.-]+)\1(.*?\n).*?^\t*\2\r?$", r"\3", src, flags=re.M | re.S)
        if re.search(r"\b(?:eval|source)\b|(?:^|[;&|\s])\.\s", src):
            return set()
        counts = Counter(re.findall(r"(?:^|[\s;&|(`{])([A-Za-z_]\w*)\+?=", src))
        counts.update(re.findall(r"\$\{([A-Za-z_]\w*):?=", src))
        counts.update(re.findall(r"\b(?:for|select)\s+([A-Za-z_]\w*)", src))
        counts.update(re.findall(r"\bprintf\s+-v\s*([A-Za-z_]\w*)", src))
        for args in re.findall(r"\b(?:read|unset|local|declare|typeset|mapfile|readarray|getopts|nameref)\b"
                               r"([^;&|\n]*)", src):
            counts.update(re.findall(r"[A-Za-z_]\w*", args))
        if "IFS" in counts:
            return set()
    return {n for n, c in counts.items() if c == 1 and n.lower() not in DYNAMIC}


def bind(w: Word, consts: dict) -> Word:
    """`w` with each plain variable the script bound to a literal replaced by it, or marked unknown."""
    if not w.vars:
        return w
    unknown = []

    def value(m: re.Match) -> str:
        v = consts.get(m.group(2))
        # An unquoted expansion splits on whitespace and globs: its words cannot be told apart.
        if v is None or m.group(1) == "u" and re.search(r"[\s*?\[]", v):
            unknown.append(m.group(2))
            return "$" + m.group(2)
        return v

    text = MARK.sub(value, w.text)
    return replace(w, text=text, why=w.why or ("variable" if unknown else ""), vars=False)


def literal(items: list, d: str, consts: dict, once: set) -> dict:
    """What an assignment-only command binds to literals: `S=x`, `export S=x`, `$S = 'x'`."""
    words = [it[1] for it in items if it[0] == "w"]
    if len(words) != len(items) or not words:
        return {}
    if d == "ps":
        name = re.fullmatch(r"\$([A-Za-z_]\w*)", words[0].raw)
        if len(words) != 3 or not name or words[1].raw != "=" or name.group(1).lower() not in once:
            return {}
        value = bind(words[2], consts)
        return {} if value.why else {name.group(1).lower(): value.text}
    if words[0].raw in ("export", "readonly"):
        words = words[1:]
    out = {}
    for w in words:
        name = re.match(r"([A-Za-z_]\w*)=", w.raw)
        bound = bind(w, consts)
        if not name or bound.why or name.group(1) not in once:
            return {}
        out[name.group(1)] = bound.text[len(name.group(1)) + 1:]
    return out


def run(toks: list, ctx: Ctx, src: str = "") -> tuple[set, frozenset]:
    """Effects of a token list, and every directory the shell may be in once it has run."""
    if ctx.depth > DEPTH:
        return top(f"nesting deeper than {DEPTH}"), ctx.cwds
    out, cwds, pending = set(), ctx.cwds, None
    # A layer over the enclosing script's names: entering a group copies nothing, so work stays linear.
    consts, once, stack, chain = ChainMap({}, ctx.consts), bound_once(src, ctx.d) if src else set(), [], set()
    cmds = commands(toks, ctx.d)
    for k, (join, items) in enumerate(cmds):
        if ctx.sink is not None:
            # `deny.py` fnmatches each simple command against the pages' `Bash(...)` patterns,
            # so a block holds even after a preceding statement (`echo ok; git reset --hard`).
            words = " ".join(it[1].raw or it[1].text for it in items if it[0] == "w")
            if words:
                ctx.sink.append(words)
        if pending is not None:
            cwds = pending if join == "&&" else states(cwds | pending)
            pending = None
        if join != "&&":
            # An assignment behind `&&` holds only while the chain does.
            for name in chain:
                consts.pop(name, None)
            chain = set()
        piped_in = join in ("|", "|&")
        nxt = cmds[k + 1][0] if k + 1 < len(cmds) else ""
        if nxt == ")" and ctx.d == "posix":
            continue                    # a `case` pattern: words to match, not a command
        eff, moved = simple(items, replace(ctx, cwds=cwds, piped=piped_in, consts=consts))
        out |= eff
        for it in items:
            if it[0] == "w" and ctx.d == "posix" and not it[1].quoted:
                if it[1].raw in OPEN_KW:
                    stack.append(it[1].raw)
                elif it[1].raw in CLOSE_KW and stack:
                    stack.pop()
        if moved is not None:
            if k or ctx.depth or stack or piped_in or nxt in ("|", "|&"):
                # Only the call's own first statement may set where the rest runs.
                out |= top("a directory change after the first statement")
            else:
                pending = moved
        # Only an assignment that runs at this level, not in a pipeline or a branch, binds what follows it.
        words = [bind(it[1], consts).text for it in items if it[0] == "w"]   # as `simple` dispatched them
        here = once and not stack and join in (";", "\n", "&&") and not piped_in and nxt not in ("|", "|&")
        bound = literal(items, ctx.d, consts, once) if here else {}
        consts.update(bound)
        chain |= set(bound) if join == "&&" else set()
        # `export` binds each word's `NAME=` after quote removal: one `literal` did not bind has no literal.
        if ctx.d == "posix" and "export" in words:
            consts.update({m.group(1): None for t in words if (m := re.match(r"([A-Za-z_]\w*)\+?=", t))
                           and m.group(1) not in bound})
    # Where the last command moved, it may or may not have run: every place stays possible.
    return out, states(cwds | pending) if pending is not None else cwds


def code(text: str, d: str, ctx: Ctx, fresh: bool = True, **kw) -> set:
    """Effects of a string parsed again one level down."""
    return run_code(text, d, ctx, fresh, **kw)[0]


def run_code(text: str, d: str, ctx: Ctx, fresh: bool = True, **kw) -> tuple[set, frozenset]:
    if ctx.depth >= DEPTH:
        return top(f"nesting deeper than {DEPTH}"), ctx.cwds
    return run(lex(text, d), ctx.deeper(d, fresh, **kw), text)   # `Unclosed` makes the whole call top


def member_of(w: Word) -> set:
    if not w.method:
        return set()
    # The receiver is a string literal only when the word begins with a quote (`'x'.Replace`);
    # a quote later in the word (an index like `.Methods['Delete']`) is not the receiver.
    return member_effect(w.recv, w.method, bool(re.match(r"^\s*['\"]", w.raw)))


def member_effect(recv: str, method: str, literal_recv: bool) -> set:
    """One gate every PowerShell member invocation routes through, whatever the syntax wrote it.

    A member name does not fix its effect: `String.Replace` is pure but `FileInfo.Replace`
    overwrites a file, so a name alone is not safe. A call is inert only when the receiver's
    type is statically known and safe, or the receiver is a string literal, or the name is
    read-only for every receiver type. Anything else — a polymorphic or dispatching name on
    an unknown receiver, or a name the lexer could not read — is undecidable.
    """
    from guard_windows import SAFE_MEMBERS, SAFE_TYPES

    name = method.lower()
    recv = recv.lower()
    if name == DYNAMIC_MEMBER:
        return top("a dynamic or quoted member name")
    if name == "new":
        # A constructor's receiver decides what it does: `[Text.UTF8Encoding]::new`
        # is inert, `[IO.FileStream]::new('out', 'Create')` creates a file.
        return set() if recv and SAFE_TYPES.match(recv) else top(f"constructor of {recv or 'an unknown type'}")
    if (recv and SAFE_TYPES.match(recv)) or (not recv and literal_recv):
        return set()
    return set() if name in SAFE_MEMBERS else top(f"method {method}")


ASSIGN = re.compile(r"^([A-Za-z_]\w*)(?:\[[^\]]*\])?\+?=")


def simple(items: list, ctx: Ctx) -> tuple[set, object]:
    """Effects of one simple command and, for `cd`, the directories it moves to."""
    from guard_models import KEYWORDS, run_argv
    from guard_windows import ps_path, ps_statement

    eff, words, stdin, call, consts, redirs = {READ}, [], None, False, ctx.consts, []
    # PowerShell evaluates groups (`(…)`, `{…}` blocks it runs) and subexpressions (`$(…)`) in this
    # runspace, so one that moves the location would move it for the rest of the call: `top`. A POSIX
    # `(…)` or `$(…)` is a subshell and moves nothing here.
    ps = ctx.d == "ps"
    for it in items:
        if it[0] == "g":
            if ctx.depth < DEPTH:
                inner, after = run(it[2], ctx.deeper(fresh=False))
                if ps and after != ctx.cwds:
                    inner |= top("a directory change inside a group")
            else:
                inner = top(f"nesting deeper than {DEPTH}")
            eff |= inner
            words.append(Word(GROUP_TEXT.get(it[1], "(...)"), "group"))
            continue
        if it[0] == "call":
            call = True
            continue
        w = bind(it[3] if it[0] == "r" else it[1], consts)
        for d, text in w.subs:
            if (d, text) == ARITH:
                eff |= top("arithmetic evaluation, which runs what its variables hold")
                continue
            inner, after = run_code(text, d, ctx, fresh=False)
            eff |= inner | (top("a directory change inside a subexpression") if ps and after != ctx.cwds else set())
        eff |= member_of(w)
        if it[0] == "r":
            redirs.append((it[2], w))
        else:
            words.append(w)
    for op, target in redirs:
        if op in ("<<", "<<-", "<<<"):
            stdin = target
        elif op in (">", ">>", ">|", "<>") or (op == ">&" and not re.fullmatch(r"\d*-?", target.text)):
            eff |= paths("write", ps_path(target) if ps else target, ctx)   # PowerShell writes the file itself
    ctx, env = replace(ctx, stdin=stdin), []
    while True:
        if ctx.d == "posix":
            while words and assignment(words[0]):
                env.append(words.pop(0))
        if not (words and not words[0].quoted and words[0].text.lower() in KEYWORDS[ctx.d]):
            break
        # `foreach` is the loop statement only with its `(...)` clause; otherwise (piped, or with a
        # member name like `foreach -m Delete`) it is the ForEach-Object cmdlet, judged by its model.
        if ctx.d == "ps" and words[0].text.lower() == "foreach" and not (len(words) > 1 and words[1].why == "group"):
            break
        key = words.pop(0).text.lower()
        if key in REPEATS:
            return eff | top(f"`{key}`: a loop, function or trap the table does not model"), None
        if key in ("case", "using", "param", "class", "enum", "data"):
            loads = key == "using" and not (words and words[0].text.lower() == "namespace")
            return eff | envs(env, ctx) | (top("`using` that loads a module or an assembly") if loads else set()), None
    eff |= envs(env, ctx)
    ctx = replace(ctx, env=tuple(w.text for w in env))
    if ctx.d == "ps" and not call:
        words, stop = ps_statement(words, ctx)
        eff |= stop
    if ctx.d == "posix" and len(words) > 1 and words[1].why == "group" and words[1].text == "(...)" \
            and not words[0].why:
        # `name() { ...; }` defines a POSIX function. In PowerShell `name (expr)` passes the expression's value.
        return eff | top(f"the shell function {words[0].text}"), None
    if not words:
        return eff, None
    if words[0].why == "group":
        # `& (expr)` runs whatever command the expression evaluates to; only a literal block is code read here.
        computed = call and words[0].text != BLOCK
        return eff | (top("a computed command name") if computed else set()), None
    if words[0].why:
        return eff | top(f"{words[0].why} as the command name"), None
    more, moved = run_argv(words, ctx)
    return eff | more, moved


def assignment(w: Word) -> re.Match | None:
    """`NAME=` written unquoted at the start of a word, as the shell reads an assignment."""
    return None if w.raw[:1] in ("'", '"') else ASSIGN.match(w.raw or w.text)


# Variables whose value is a command line: what it runs is derived like any command.
ENV_COMMAND = re.compile(r"^(?:GIT_EDITOR|GIT_SEQUENCE_EDITOR|EDITOR|VISUAL|PAGER|GIT_PAGER|GIT_SSH_COMMAND|"
                         r"GIT_SSH|GIT_ASKPASS|SSH_ASKPASS|GIT_EXTERNAL_DIFF|GIT_PROXY_COMMAND|GH_PAGER|GH_BROWSER|"
                         r"BROWSER|GH_EDITOR)$", re.I)
# Variables that change which code a later program loads or runs, or where `~`, `/tmp` and a client's
# configuration land: never decidable here.
ENV_EXEC = re.compile(r"^(?:LD_\w+|DYLD_\w+|BASH_ENV|ENV|PROMPT_COMMAND|PATH|PATHEXT|GIT_(?!AUTHOR_|COMMITTER_|"
                      r"TERMINAL_PROMPT|OPTIONAL_LOCKS|DIR$|WORK_TREE$)\w+|NODE_OPTIONS|PYTHONSTARTUP|"
                      r"PERL5OPT|RUBYOPT|SHELLOPTS|PS4|COMSPEC|PSModulePath|HOME|USERPROFILE|ZDOTDIR|"
                      r"XDG_CONFIG_HOME|CURL_HOME|TMPDIR|TMP|TEMP|GH_CONFIG_DIR|SSLKEYLOGFILE)$", re.I)
# Variables that send a later client to another host: the host is reached whichever command reads them.
ENV_NETWORK = re.compile(r"^(?:GH_HOST|GH_REPO|HTTPS?_PROXY|ALL_PROXY)$", re.I)


def envs(assigns: list, ctx: Ctx) -> set:
    from guard_models import network

    out = set()
    for w in assigns:
        name, _, value = w.text.partition("=")
        name = name.rstrip("+")
        if "[" in name and not re.fullmatch(r"\w+\[\d+\]", name):
            # An array subscript other than a number is evaluated as arithmetic.
            out |= top("arithmetic evaluation, which runs what its variables hold")
        name = name.split("[")[0]
        if name.upper() == "GH_REPO":
            out |= top("GH_REPO, which points gh at a repository other than the checkout's")
        elif ENV_NETWORK.match(name):
            out |= network(Word(value, w.why))
        elif ENV_COMMAND.match(name):
            out |= top(f"{w.why} in {name}") if w.why else code(value, "posix", ctx)
        elif ENV_EXEC.match(name) or name.upper() in ("GIT_DIR", "GIT_WORK_TREE"):
            # `GIT_DIR`/`GIT_WORK_TREE` move git to another repository, whose remotes the guard has not read.
            out |= top(f"{name} changes what runs or where files land")
    return out


def names_set(ws: list, ctx: Ctx) -> set:
    """A builtin that assigns or unsets names (`printf -v`, `read`, `for NAME`) is judged like `NAME=…`
    with a value known only at run time."""
    return envs([Word(w.text + "=", w.why or "a value assigned at run time") for w in ws], ctx)


# -- Paths -------------------------------------------------------------------

# Devices by the host's rule: `/dev/...` as spelled where a POSIX layer serves it, Windows' names in any case.
NULLS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/stdin"}


def resolve(text: str, cwd: str | None, ctx: Ctx) -> str | None:
    """The absolute path a literal names from `cwd`; `None` for a device. Raises ValueError."""
    t = text.replace("\\", "/") if ctx.windows else text
    unix = ctx.d == "posix" or not ctx.windows
    if unix and (t in NULLS or t.startswith("/dev/fd/")) or ctx.windows and t.lower() in ("nul", "nul:", "con"):
        return None
    if t == "~" or t.startswith("~/"):
        t = ctx.home + t[1:]
    elif t.startswith("~"):
        raise ValueError("another user's home")
    if ctx.windows:
        if t.startswith("//?/"):
            # `\\?\UNC\host\share` is a share; any other device form but a drive stays absolute (`GLOBALROOT`).
            t = "//" + t[8:] if t[4:8].lower() == "unc/" else t[4:] if re.match(r"[A-Za-z]:/", t[4:]) else t
        if ctx.d == "posix" and (m := re.match(r"^/([A-Za-z])(/|$)", t)):
            t = m.group(1).upper() + ":/" + t[3:]
        elif ctx.d == "posix" and (t == "/tmp" or t.startswith("/tmp/")):
            t = ctx.temp + t[4:]
        elif ctx.d == "posix" and t.startswith("/") and not t.startswith("//"):
            t = "//msys" + t           # Git Bash's own root: no drive, inside no root
        if re.match(r"^[A-Za-z]:(?!/)", t):
            if not cwd or cwd[:2].lower() != t[:2].lower():
                raise ValueError("drive-relative path")
            t = cwd + "/" + t[2:]
        elif t.startswith("/") and not t.startswith("//"):
            if not cwd or not re.match(r"^[A-Za-z]:", cwd):
                raise ValueError("path rooted on an unknown drive")
            t = cwd[:2] + t
        elif not re.match(r"^[A-Za-z]:/|^//", t):
            if cwd is None:
                raise ValueError("relative path after an unknown cd")
            t = cwd + "/" + t
        t = ntpath.normpath(t).replace("\\", "/")
        return t[0].upper() + t[1:] if re.match(r"^[a-z]:", t) else t
    if not t.startswith("/"):
        if cwd is None:
            raise ValueError("relative path after an unknown cd")
        t = cwd.rstrip("/") + "/" + t
    # `..` stays: on POSIX it is resolved after symlinks, by `contained`.
    return re.sub(r"/+", "/", t)


def paths(kind: str, w: Word, ctx: Ctx) -> set:
    """`kind(path)` for every directory the command may run in, or `top` when the path is unknown."""
    if w.raw.lower() == "$null" and ctx.d == "ps":     # the variable; a quoted '$null' is a file
        return set()
    if w.why:
        return top(f"{w.why} in a {'delete' if kind == 'delete' else 'write'} path")
    out = set()
    for cwd in ctx.cwds:
        try:
            where = resolve(w.text, cwd, ctx)
        except ValueError as error:
            return top(str(error))
        if where is not None:
            out.add((kind, where))
    return out


def moved_to(w: Word | None, ctx: Ctx) -> frozenset:
    if w is None:
        return frozenset({ctx.home})
    if w.why or w.text == "-":
        return frozenset({None})
    out = set()
    for cwd in ctx.cwds:
        try:
            out.add(resolve(w.text, cwd, ctx))
        except ValueError:
            out.add(None)
    return frozenset(out)


def real_path(p: str, windows: bool | None = None) -> str:
    """`p` with links, junctions and `..` resolved, in the platform's case."""
    mod = ntpath if (os.name == "nt" if windows is None else windows) else posixpath
    try:
        p = mod.realpath(p)
    except (OSError, ValueError):
        p = mod.abspath(p)
    return mod.normcase(p)


def contained(path: str, roots: list[str], windows: bool | None = None) -> bool:
    """Is `path` inside one of `roots` once links, junctions and `..` are resolved?"""
    mod = ntpath if (os.name == "nt" if windows is None else windows) else posixpath
    where = real_path(path, windows)
    for root in roots:
        base = real_path(root, windows)
        try:
            if mod.commonpath([where, base]) == base:
                return True
        except ValueError:
            continue
    return False


# -- Deriving a call's effects ----------------------------------------------

SHELL_TOOLS = {"Bash", "PowerShell", "Monitor", "exec_command", "shell_command", "shell", "local_shell"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
# Codex sends the shell command under `cmd`, sometimes as an argv list, and on Windows it is PowerShell.
CODEX_SHELL_TOOLS = SHELL_TOOLS - {"Bash", "PowerShell", "Monitor"}


def shell_command(payload: dict, host: str = "claude") -> tuple[str, object, str]:
    """`(tool, command, dialect)` read off a tool call the one way, for both the deny check and the guard.

    A shell command may arrive under `command` or `cmd`, as a string or an argv list, from a tool
    whose name may carry a `functions.` prefix; and a Codex shell on Windows is PowerShell. Reading
    it in one place keeps the declared-deny check and the effect guard judging the same command.
    """
    tool = str(payload.get("tool_name") or "").removeprefix("functions.")
    given = payload.get("tool_input") or {}
    command = given.get("command", given.get("cmd", "")) if isinstance(given, dict) else ""
    windows = os.name == "nt"
    dialect = "ps" if tool == "PowerShell" or (host == "codex" and windows) \
        or (tool in CODEX_SHELL_TOOLS and windows) else "posix"
    return tool, command, dialect
PATCH = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


def derive(command, dialect: str, ctx: Ctx) -> set:
    """Effects of a shell command: a string, or an argv list run without a shell."""
    from guard_models import run_argv

    ctx = replace(ctx, d=dialect)
    try:
        if isinstance(command, list):
            return {READ} | run_argv([Word(str(a), quoted=True) for a in command], ctx)[0]
        return {READ} | run(lex(str(command), dialect), ctx, str(command))[0]
    except (Unclosed, Unbounded) as error:
        return {READ} | top(str(error))
    except RecursionError:
        return top("nesting too deep to parse")


def nested_bash_commands(command, dialect: str = "posix") -> list:
    """Every command string a nested shell or substitution inside `command` will run.

    `deny.py` fnmatches these against the pages' `Bash(...)` patterns, so a
    declared block such as `git reset --hard` is kept even when the command is
    wrapped in `bash -c`, `pwsh -Command` or `$(...)`. Paths stay unresolved
    here (`cwds` is unknown); only the raw strings matter.
    """
    found: list = []
    ctx = Ctx(dialect, frozenset({None}), "", "", os.name == "nt", sink=found)
    try:
        if isinstance(command, list):
            from guard_models import run_argv
            run_argv([Word(str(a), quoted=True) for a in command], ctx)
        else:
            run(lex(str(command), dialect), ctx, str(command))
    except Exception:
        pass
    return found


def call_effects(tool: str, given: dict, ctx: Ctx, host: str, payload: dict) -> set:
    if tool in SHELL_TOOLS:
        _tool, command, dialect = shell_command(payload, host)
        return derive(command, dialect, ctx)
    if tool in EDIT_TOOLS:
        native = replace(ctx, d="ps")     # host paths: no Git Bash drive mapping
        if tool != "apply_patch":
            target = given.get("file_path") or given.get("notebook_path") or ""
            return paths("write", Word(str(target)), native) if target else set()
        out = set()
        for kind, path, moved in PATCH.findall(str(given.get("input") or given.get("patch") or "")):
            out |= paths("delete" if kind == "Delete" else "write", Word((path or moved).strip()), native)
        return out
    return {READ}


def render(effect: tuple) -> str:
    if effect == READ:
        return effect[0]
    # git's write of its repository is a write to the person.
    kind = "write" if effect[0] == "repo" else effect[0]
    return f"{kind}({effect[1]})"


# -- The hook ----------------------------------------------------------------

def posix(p: Path) -> str:
    return str(p).replace("\\", "/") if os.name == "nt" else str(p)


def run_dir(cwd: Path, given: dict) -> str | None:
    """Where a shell call runs: its own `workdir` (Codex), relative to the session's folder; else that folder.

    The grant stays the session's; only the paths the command names resolve from here. A `workdir`
    that is not a plain string is a place the guard cannot read: `None`, so relative paths are `top`.
    """
    for key in ("workdir", "cwd"):
        if key in given:
            wd = given[key]
            return posix(cwd / wd) if isinstance(wd, str) and wd and "\0" not in wd else None
    return posix(cwd)


def verdict(payload: dict, host: str = "claude") -> dict | None:
    from guard_grant import grant, outside, temps

    if os.environ.get("WIKI_AGENT_MANAGED") != "1":
        return None
    tool = str(payload.get("tool_name") or "").removeprefix("functions.")
    given = payload.get("tool_input") or {}
    if not isinstance(given, dict) or (tool not in SHELL_TOOLS and tool not in EDIT_TOOLS):
        return None
    cwd = Path(str(payload.get("cwd") or os.getcwd()))
    g = grant(cwd, payload)
    if g is None:
        return None
    ctx = Ctx("posix", frozenset({run_dir(cwd, given) if tool in SHELL_TOOLS else posix(cwd)}),
              posix(Path.home()), temps()[0].as_posix(), os.name == "nt", remotes=g.remotes, repo=g.top)
    bad = [(render(e), why) for e in sorted(call_effects(tool, given, ctx, host, payload)) if (why := outside(e, g))]
    if not bad:
        return None
    lines = ["위키 효과 검사가 이 호출을 막았다 — 작업에 허가된 효과 밖이다."]
    lines += [f"- {effect}: {why}" for effect, why in bad]
    lines.append(f"허가: 쓰기 루트 {', '.join(g.roots)} · 호스트 {', '.join(sorted(g.hosts))}")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "\n".join(lines)}}


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        # Through the module, so `guard_models` and this share one `guard`.
        import guard
        answer = guard.verdict(payload) if isinstance(payload, dict) else None
        if answer:
            json.dump(answer, sys.stdout, ensure_ascii=False)
    except Exception as error:
        print(f"hook skipped: {type(error).__name__}", file=sys.stderr)
