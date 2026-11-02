"""What each program does: a closed table of modelled programs, each with the flags it understands.

`TABLE` is all the guard knows about programs. A row names a program, the
flags it understands (`row`'s spec: one-letter flags, flags taking a value,
long flags) and the model that maps its argv to effects: `rm a b` deletes `a`
and `b`, `curl URL -o F` reaches the URL's host and writes `F`. `gate` runs
before every model: a flag the row does not list is `top`, and so is a word
computed at run time where a flag may stand, unless no option of the program
writes, runs or connects (`inert`). Some rows exist only to refuse (`refuse`):
links, extraction, patches, arithmetic. A program with no row is a `spawn` of
that executable, which only a grant naming it allows. Nothing is modelled one
form at a time: a denied ordinary command is answered by adding a row.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import posixpath
import re

from guard import BLOCK, NULLS, Word, assignment, code, envs, moved_to, names_set, paths, resolve, top

KEYWORDS = {
    "posix": {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "{", "}", "!", "time", "for",
              "select", "case", "esac", "function", "coproc"},
    "ps": {"if", "elseif", "else", "switch", "foreach", "for", "while", "do", "until", "try", "catch", "finally",
           "trap", "function", "filter", "param", "begin", "process", "end", "return", "exit", "throw", "break",
           "continue", "data", "dynamicparam", "class", "enum", "using"},
    "cmd": {"else", "not"},
}

SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
PS_SHELLS = {"pwsh", "powershell"}


@dataclass(frozen=True)
class Row:
    model: object                       # (name, args, ctx) -> (effects, directories moved to or None)
    short: str = ""                     # one-letter flags taking no value
    takes: frozenset = frozenset()      # flags taking a value: `-o`, `--output`
    longs: frozenset = frozenset()      # long flags taking no value (`--color=x` binds an optional one)
    glued: frozenset = frozenset()      # one-letter flags whose optional value is glued on: `-i.bak`
    digits: bool = False                # `-20` is a count
    inert: bool = False                 # no option writes, runs or connects: a computed word is an operand
    free: bool = False                  # no flags to check here: the model reads every argument itself
    stop: bool = False                  # options end at the first operand, which starts a command


def row(model, spec: str = "", **kw) -> Row:
    """`spec`: `-abc` one-letter flags, `--long` long flags, `-o:`/`--out:` taking a value, `-i?` glued."""
    short, takes, longs, glued = "", set(), set(), set()
    for t in spec.split():
        if t.endswith(":"):
            takes.add(t[:-1])
        elif t.endswith("?"):
            glued.add(t[:-1])
        elif t.startswith("--"):
            longs.add(t)
        else:
            short += t[1:]
    return Row(model, short, frozenset(takes), frozenset(longs), frozenset(glued), **kw)


def refuse(why: str):
    """A row listed only to refuse: what the program does is never decidable from its argv."""
    def model(name, args, ctx):
        return top(f"{name} {why}"), None
    return model


def literal_start(w: Word) -> bool:
    """A computed word that starts with a literal (`./$x`, `out-$n`) cannot expand into an option."""
    return bool(re.match(r"[\w./\\:~]", w.text))


def gate(name: str, args: list, r: Row) -> set:
    """`top` for the first flag the row does not understand, read the way getopt reads it."""
    if r.free:
        return set()
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        i += 1
        if w.why:
            if not r.inert and not literal_start(w):
                return top(f"{w.why} where an option of {name} may stand")
            if r.stop:
                return set()
            continue
        if t == "--":
            return set()
        if len(t) < 2 or t[0] != "-":
            if r.stop:
                return set()
            continue
        if r.digits and t[1:].isdigit():
            continue
        if t.startswith("--"):
            k, eq, _ = t.partition("=")
            if k in r.takes:
                i += 0 if eq else 1
            elif k not in r.longs:
                return top(f"{name} {k}: an option the table does not model")
            continue
        for j, ch in enumerate(t[1:], 1):
            if "-" + ch in r.glued:
                break
            if "-" + ch in r.takes:
                i += 0 if t[j + 1:] else 1
                break
            if ch not in r.short:
                return top(f"{name} -{ch}: an option the table does not model")
    return set()


def exe(raw: str) -> str:
    """A bare program's identity: its name, lowercased. `.exe` is how Windows spells the same program;
    any other suffix (`.cmd`, `.bat`, `.com`) names a different file."""
    name = re.split(r"[\\/]", raw)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def parse(name: str, args: list, stop: bool = False) -> tuple[list, dict, list]:
    return getopt(args, TABLE[name].takes, stop)


def getopt(args: list, takes=frozenset(), stop: bool = False) -> tuple[list, dict, list]:
    """`(operands, option values, flags)`; options in `takes` consume a value, others are flags."""
    pos, vals, flags, i = [], {}, [], 0
    while i < len(args):
        w, t = args[i], args[i].text
        if t == "--" and not w.why:
            pos += args[i + 1:]
            break
        if len(t) > 1 and t[0] == "-" and not w.why:
            flags.append(t)
            if t.startswith("--"):
                k, eq, v = t.partition("=")
                if k in takes:
                    if eq:
                        vals.setdefault(k, []).append(Word(v, w.why, quoted=w.quoted))
                    elif i + 1 < len(args):
                        vals.setdefault(k, []).append(args[i + 1])
                        i += 1
            else:
                for j, ch in enumerate(t[1:], 1):
                    if "-" + ch in takes:
                        if t[j + 1:]:
                            vals.setdefault("-" + ch, []).append(Word(t[j + 1:], w.why, quoted=w.quoted))
                        elif i + 1 < len(args):
                            vals.setdefault("-" + ch, []).append(args[i + 1])
                            i += 1
                        break
            i += 1
            continue
        pos.append(w)
        if stop:
            pos += args[i + 1:]
            break
        i += 1
    return pos, vals, flags


def writes(kind: str, ws: list, ctx) -> set:
    out = set()
    for w in ws:
        out |= paths(kind, w, ctx)
    return out


def here(ctx) -> Word:
    return Word(".")


LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}


def host_of(text: str) -> str:
    t = text.strip()
    if m := re.match(r"^[a-zA-Z][\w+.-]*://(?:[^@/]*@)?(\[[^\]]*\]|[^/:?#]*)", t):
        host = m.group(1)
    elif m := re.match(r"^\[([^\]]+)\](?:[/:?#]|$)", t):
        host = m.group(1)              # a bare IPv6 literal, `[::1]` or `[::1]:80`
    elif m := re.match(r"^(?:[^@/\\:\s]+@)?([^/\\:\s]{2,}|\[[^\]]+\]):", t):
        host = m.group(1)
    else:
        host = re.split(r"[/:?#]", t, maxsplit=1)[0]
    host = host.lstrip("@").strip("[]").lower().rstrip(".")
    return "127.0.0.1" if host in LOOPBACK else host


def network(w: Word) -> set:
    if w.why:
        return top(f"{w.why} in a network target")
    if w.text.lower().startswith("file:") or not w.text.strip():
        return set()
    host = host_of(w.text)
    # A target whose host cannot be read is not "no connection": it is a connection somewhere unknown.
    return {("network", host)} if host else top(f"a network target with no readable host ({w.text})")


# -- Files ---------------------------------------------------------------------

def m_read(name, args, ctx):
    return set(), None


def m_delete(name, args, ctx):
    return writes("delete", parse(name, args)[0], ctx), None


def m_create(name, args, ctx):
    # A new directory is `dir`: making one at a place never writes through what is there.
    return writes("dir" if name == "mkdir" else "write", parse(name, args)[0], ctx), None


def m_output(name, args, ctx):
    """A reader that writes only where `-o`/`--output` points, or (`uniq`) to its second operand."""
    pos, vals, _ = parse(name, args)
    out = writes("write", vals.get("-o", []) + vals.get("--output", []), ctx)
    return out | (paths("write", pos[1], ctx) if name == "uniq" and len(pos) > 1 else set()), None


def basename(text: str) -> str:
    return re.split(r"[\\/]", text.rstrip("/\\"))[-1]


def into_dir(pos: list, dest: list, ctx, kind: str = "write") -> set:
    """A directory destination writes `<dir>/<source-basename>`, not just the directory.

    A `-t`/`--target-directory` option, or the last operand of a two-or-more
    operand copy, may be an existing directory, so the real target is a
    descendant of it. Naming that descendant catches a copy into a protected
    file's parent (`cp x .wiki`), which a bare-directory write would miss. A
    tainted end is already a `top` from the directory write itself.
    """
    dirs = list(dest)
    srcs = list(pos)
    if not dest:
        dirs = [w for w in pos if not w.why and re.search(r"[\\/]$", w.text)]
        if len(pos) >= 2 and not pos[-1].why and pos[-1] not in dirs:
            dirs.append(pos[-1])
        srcs = [w for w in pos if w not in dirs]
    out = set()
    for d in dirs:
        if d.why or d.text.replace("\\", "/").rstrip("/").lower() in NULLS:
            continue
        for s in srcs:
            if s.why or not s.text:
                continue
            out |= paths(kind, Word(d.text.rstrip("/\\") + "/" + basename(s.text)), ctx)
    return out


def shorts(flags: list) -> str:
    return "".join(f[1:] for f in flags if not f.startswith("--"))


def m_copy(name, args, ctx):
    """`cp`/`mv` with only the flags their rows list: no link, backup or preserve mode is among them."""
    pos, vals, flags = parse(name, args)
    short = shorts(flags)
    recursive = name == "cp" and ("r" in short or "R" in short or "--recursive" in flags)
    follow = "L" in short or "--dereference" in flags
    # A rename moves an entry as it is, a symlink included; a recursive copy that does not dereference
    # (`-L`) reproduces the links in its source tree. Either lays down a tree that may hold a link.
    kind = "linktree" if name == "mv" or recursive and not follow else "write_tree" if recursive else "write"
    dest = vals.get("-t", []) + vals.get("--target-directory", [])
    out = writes(kind, dest or pos[1:], ctx) | into_dir(pos, dest, ctx, kind)
    if name == "mv":
        out |= writes("delete", pos if dest else pos[:-1], ctx)
    return out, None


def m_dd(name, args, ctx):
    return writes("write", [Word(w.text[3:], w.why) for w in args if w.text.startswith("of=")], ctx), None


def m_mktemp(name, args, ctx):
    """The folder mktemp creates its new file or folder in; the name itself is random, so never an existing file.

    A template alone is relative to the current folder; `-p DIR`/`--tmpdir=DIR` or `-t` place it in that
    folder or the temporary one; with no template it goes to the temporary folder.
    """
    pos, vals, flags = parse(name, args)
    template = pos[0] if pos else None
    base = vals.get("-p", []) + vals.get("--tmpdir", [])
    if template is not None and template.why:
        return top(f"{template.why} in a mktemp template"), None
    if base or template is None or "t" in shorts(flags) or any(f.startswith("--tmpdir") for f in flags):
        folder = base[0] if base else Word(ctx.temp)
        rel = posixpath.dirname(template.text.replace("\\", "/")) if template is not None else ""
        where = Word(posixpath.join(folder.text, rel) if rel else folder.text, folder.why)
    else:
        where = Word(posixpath.dirname(template.text.replace("\\", "/")) or ".")
    return paths("write", where, ctx), None


TAR = row(None, "-ctvzjJah -f: -C: -T: -X: --create --list --verbose --gzip --bzip2 --xz --auto-compress "
                "--dereference --file: --directory: --exclude: --files-from: --exclude-from:")


def m_tar(name, args, ctx):
    """Creation and listing only: extraction, source removal and program options are not in `TAR`."""
    if args and not args[0].why and args[0].text[:1] not in ("-", ""):
        args = [Word("-" + args[0].text)] + args[1:]     # `tar czf x`: the first word is a flag cluster
    if stop := gate(name, args, TAR):
        return stop, None
    _pos, vals, flags = getopt(args, TAR.takes)
    if "c" in shorts(flags) or "--create" in flags:
        return writes("write", vals.get("-f", []) + vals.get("--file", []), ctx), None
    return set(), None


SUFFIX = {"gzip": ".gz", "xz": ".xz", "bzip2": ".bz2", "zstd": ".zst"}


def m_compress(name, args, ctx):
    """A compressor writes `<file><suffix>`; one that removes its sources (no `-k`) is refused."""
    pos, vals, flags = parse(name, args)
    short = shorts(flags)
    if not pos or "c" in short or "t" in short or "l" in short or {"--stdout", "--to-stdout", "--test", "--list"} \
            & set(flags):
        return set(), None
    if not (name == "zstd" or "k" in short or "--keep" in flags):
        return top(f"{name} removes its sources"), None
    named = vals.get("-o", [])
    return writes("write", named or [Word(w.text + SUFFIX[name], w.why) for w in pos], ctx), None


def m_cd(name, args, ctx):
    pos = parse(name, args)[0]
    if name == "popd" or pos and re.fullmatch(r"[+-]\d+", pos[0].text):
        return set(), frozenset({None})            # a place on the directory stack
    return set(), moved_to(pos[0] if pos else None, ctx)


# -- Text tools whose programs can write or run ------------------------------

# A sed `w`/`W`/`e` command after any address or delimiter, or an `s` command with a `w`/`e` flag.
# Both match across newlines: a backslash-newline continues a line, and an `s` replacement may hold one, so
# a recognizer that stops at a newline would miss a write the program still performs.
SED_RUNS = re.compile(r"(?:^|[^A-Za-z])[wWe](?:\s|$)|s(.).*?(?<!\\)\1.*?(?<!\\)\1[gpiImM0-9]*[we]", re.S)
AWK_RUNS = re.compile(r"\bsystem\s*\(|\|\s*getline|\|&|\bprintf?\b[^;{}]*[>|]|@\s*(?:load|include)")


def m_sed(name, args, ctx):
    pos, vals, flags = parse(name, args)
    if "-f" in vals or "--file" in vals:
        return top("a sed script file"), None
    given = vals.get("-e", []) + vals.get("--expression", [])
    scripts, files = (given, pos) if given else (pos[:1], pos[1:])
    out = set()
    for s in scripts:
        out |= top(f"{s.why} in a sed script") if s.why else top("a sed script that writes or runs") \
            if SED_RUNS.search(re.sub(r"\\\r?\n", "\n", s.text)) else set()
    # `-i[SUFFIX]`/`--in-place[=SUFFIX]` rewrites each file and keeps a backup named `<file>SUFFIX`.
    suffixes = [f.partition("=")[2] for f in flags if f.startswith("--in-place")]
    for f in flags:
        if m := re.match(r"-[nEruzs]*i(.*)", f):       # letters before `-e`/`-f`/`-l` take no value
            suffixes.append(m.group(1))
    for suffix in suffixes:
        if re.search(r"[*/\\]", suffix):
            out |= top("a sed backup name the guard cannot place")
        out |= writes("write", files + [Word(f.text + suffix, f.why) for f in files if suffix], ctx)
    return out, None


def m_awk(name, args, ctx):
    pos, vals, _ = parse(name, args)
    if "-f" in vals or "--file" in vals:
        return top("an awk program file"), None
    include = vals.get("-i", []) + vals.get("--include", [])
    if any(w.why or w.text != "inplace" for w in include):
        return top("an awk include file"), None
    given = vals.get("-e", []) + vals.get("--source", [])
    progs, files = (given, pos) if given else (pos[:1], pos[1:])
    out = set()
    for p in progs:
        out |= top(f"{p.why} in an awk program") if p.why else top("an awk program that writes or runs") \
            if AWK_RUNS.search(re.sub(r"\\\r?\n", "", p.text)) else set()
    return out | (writes("write", files, ctx) if include else set()), None


FIND_TESTS = set("""-print -print0 -ls -prune -quit -empty -true -false -readable -writable -executable -depth
-mount -xdev -follow -daystart -nouser -nogroup -noleaf -not -a -and -o -or ! ( ) , -delete""".split())
FIND_VALUES = set("""-name -iname -path -ipath -wholename -iwholename -regex -iregex -regextype -type -xtype
-maxdepth -mindepth -newer -anewer -cnewer -mtime -mmin -atime -amin -ctime -cmin -size -perm -user -group -uid
-gid -links -inum -samefile -lname -ilname -printf -used -fstype""".split())


def m_find(name, args, ctx):
    """find's expression is its own flag table: every primary is listed, the rest is `top`."""
    i = 0
    while i < len(args) and not args[i].why and args[i].text in ("-L", "-H", "-P"):
        i += 1
    roots = []
    while i < len(args) and (args[i].why or not args[i].text.startswith(("-", "(", "!", ")", ","))):
        if args[i].why and not literal_start(args[i]):
            return top(f"{args[i].why} where a find primary may stand"), None
        roots.append(args[i])
        i += 1
    roots, out = roots or [here(ctx)], set()
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} in a find expression"), None
        if t == "-delete":
            out |= writes("delete", roots, ctx)
        elif t in ("-exec", "-ok"):
            j, argv = i + 1, []
            while j < len(args) and args[j].text not in (";", "+"):
                argv.append(Word("{}", "variable") if "{}" in args[j].text else args[j])
                j += 1
            out |= run_argv(argv, ctx)[0] if argv else set()
            i = j
        elif t in ("-fprint", "-fprint0", "-fls", "-fprintf") and i + 1 < len(args):
            out |= paths("write", args[i + 1], ctx)
            i += 2 if t == "-fprintf" else 1
        elif t in FIND_VALUES or re.fullmatch(r"-newer[aBcmt][aBcmt]", t):
            i += 1
        elif t not in FIND_TESTS:
            return top(f"find {t}: a primary the table does not model"), None
        i += 1
    return out, None


def m_xargs(name, args, ctx):
    pos, vals, flags = parse(name, args, stop=True)
    argv, unknown = pos or [Word("echo")], Word("{xargs}", "variable")
    rep = (vals.get("-I") or vals.get("--replace") or [None])[0]
    rep = rep.text if rep else next((f[2:] or "{}" for f in flags if f.startswith("-i")), "")
    argv = [unknown if w.text and rep and rep in w.text else w for w in argv] if rep else argv + [unknown]
    return run_argv(argv, ctx)[0], None


# -- Programs that run other programs ----------------------------------------

def m_prefix(name, args, ctx):
    pos, vals, flags = parse(name, args, stop=True)
    if name == "command" and any(f in ("-v", "-V") for f in flags):
        return set(), None
    out = set()
    if name == "env":
        for w in vals.get("-S", []) + vals.get("--split-string", []):
            out |= top(f"{w.why} in env -S") if w.why else code(w.text, "posix", ctx)
        assigns = []
        while pos and assignment(pos[0]):
            assigns.append(pos.pop(0))
        out |= envs(assigns, ctx)
        ctx = replace(ctx, env=ctx.env + tuple(w.text for w in assigns))
        if dirs := vals.get("-C", []) + vals.get("--chdir", []):
            ctx = replace(ctx, cwds=moved_to(dirs[-1], ctx))
    if name == "timeout":
        pos = pos[1:]
    if name == "time":
        out |= writes("write", vals.get("-o", []) + vals.get("--output", []), ctx)
    if not pos:
        return out, None
    more, moved = run_argv(pos, ctx)
    # `command` and `builtin` run a builtin in this shell, so a `cd` behind them moves it.
    return out | more, moved if name in ("command", "builtin") else None


def m_trap(name, args, ctx):
    if not args or args[0].text.startswith("-"):
        return set(), None
    return (top("variable in a trap") if args[0].why else code(args[0].text, "posix", ctx)), None


def computed(ws: list) -> set | None:
    """For a script handed over as groups: `set()` for literal `{...}` blocks, reduced where they stand;
    `top` for any other group, whose value is computed when it runs; `None` when there is no group."""
    groups = [w for w in ws if w.why == "group"]
    if not groups:
        return None
    return set() if all(w.text == BLOCK for w in groups) else top("a computed script or command")


def nested(w: Word, d: str, ctx) -> set:
    if (given := computed([w])) is not None:
        return given
    return top(f"{w.why} in a nested shell string") if w.why else code(w.text, d, ctx)


def from_stdin(d: str, ctx, what: str) -> set:
    if ctx.stdin is not None and not ctx.piped:
        return nested(ctx.stdin, d, ctx) if ctx.stdin.quoted or not ctx.stdin.why else top(
            f"{ctx.stdin.why} in a here document a shell runs")
    return top(f"{what} reads its program from stdin")


# The `set` letters a shell takes on its command line; none reads a startup file (`-i`, `-l` are not here).
SH_SETS = "euxvnfaBCHhmPTEb"


def m_sh(name, args, ctx):
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} in a shell's arguments"), None
        if re.fullmatch(rf"-[{SH_SETS}li]*c[{SH_SETS}li]*", t):
            return (nested(args[i + 1], "posix", ctx) if i + 1 < len(args) else top("-c without a string")), None
        if t in ("-o", "+o", "-O", "+O"):
            i += 2
        elif t in ("--noprofile", "--norc", "--posix", "--login", "-l", "-i") \
                or re.fullmatch(rf"[-+][{SH_SETS}]+", t):
            # `-l`/`-i` read the home's own startup files, which no granted write reaches.
            i += 1
        elif t in ("-s", "-"):
            break
        elif t.startswith(("-", "+")):
            return top(f"{name} {t}: an option the table does not model"), None
        else:
            return top("a shell script file"), None
    return from_stdin("posix", ctx, name), None


# pwsh's parameters the table understands, and the short names pwsh binds to them.
PWSH_SWITCHES = ("noprofile", "noninteractive", "nologo", "mta", "sta")
PWSH_VALUES = ("executionpolicy", "workingdirectory", "windowstyle", "outputformat", "inputformat")
PWSH_NAMES = PWSH_SWITCHES + PWSH_VALUES + ("command", "file", "encodedcommand", "encodedarguments")
PWSH_ALIASES = {"nop": "noprofile", "noni": "noninteractive", "nol": "nologo", "ep": "executionpolicy",
                "ex": "executionpolicy", "wd": "workingdirectory", "w": "windowstyle", "c": "command", "f": "file",
                "e": "encodedcommand", "ec": "encodedcommand", "ea": "encodedarguments", "o": "outputformat",
                "of": "outputformat", "if": "inputformat"}


def pwsh_param(key: str) -> str | None:
    """The parameter `-key` names: an alias, or an unambiguous prefix of at least three letters."""
    if key in PWSH_ALIASES or key in PWSH_NAMES:
        return PWSH_ALIASES.get(key, key)
    hits = [p for p in PWSH_NAMES if p.startswith(key)]
    if hits and all(h.startswith("encoded") for h in hits):
        return "encodedcommand"         # `-enc`: pwsh takes any prefix of these as an encoded command
    return hits[0] if len(key) >= 3 and len(hits) == 1 else None


def joined(rest: list, d: str, ctx) -> set:
    if (given := computed(rest)) is not None:
        return given
    if any(w.why for w in rest):
        return top("variable in a nested shell string")
    return code(" ".join(w.text for w in rest), d, ctx)


def m_pwsh(name, args, ctx):
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why == "group":
            return computed([w]), None
        if len(t) > 1 and t[0] in "-/" and not w.why:
            p = pwsh_param(t.lstrip("-/").split(":")[0].lower())
            if p is None:
                return top(f"{name} {t}: a parameter the table does not model"), None
            if p.startswith("encoded"):
                return top("an encoded command"), None
            if p == "command":
                rest = args[i + 1:]
                if not rest or [r.text for r in rest] == ["-"]:
                    return from_stdin("ps", ctx, name), None
                return joined(rest, "ps", ctx), None
            if p == "file":
                if args[i + 1:i + 2] and args[i + 1].text == "-":
                    return from_stdin("ps", ctx, name), None
                return top("a PowerShell script file"), None
            if p in PWSH_VALUES and i + 1 < len(args):
                if p == "workingdirectory":
                    ctx = replace(ctx, cwds=moved_to(args[i + 1], ctx))
                i += 2
                continue
            i += 1
            continue
        if w.why:
            return top(f"{w.why} in {name}'s arguments"), None
        if name == "pwsh":
            return top("a PowerShell script file"), None
        return joined(args[i:], "ps", ctx), None
    return from_stdin("ps", ctx, name), None


def m_cmd(name, args, ctx):
    for i, w in enumerate(args):
        t = w.text.lower()
        if re.fullmatch(r"//?[ck]", t):
            return joined(args[i + 1:], "cmd", ctx), None
        if w.why or not re.fullmatch(r"//?(?:[dqsau]|[vef]:(?:on|off))", t):
            return top(f"cmd {w.text}: a switch the table does not model"), None
    return from_stdin("cmd", ctx, name), None


# -- Network -------------------------------------------------------------------

def m_curl(name, args, ctx):
    """curl without redirects, proxies or connection overrides: those flags are not in its row."""
    pos, vals, flags = parse(name, args)
    urls = pos + vals.get("--url", [])
    out = set()
    for w in urls:
        out |= network(w)
    for w in vals.get("-w", []) + vals.get("--write-out", []):
        # `%output{file}` switches the write-out to a file; `@file` reads a format that may do so.
        if w.why or w.text.startswith("@") or "%output" in w.text.lower():
            out |= top("a curl --write-out that may name an output file")
    for w in vals.get("-o", []) + vals.get("--output", []):
        if w.text != "-":
            # `#1` is replaced by what a URL glob matched: the final name is not this text.
            out |= top("a curl output name with a #N placeholder") if "#" in w.text else paths("write", w, ctx)
    if "O" in shorts(flags) or "--remote-name" in flags:
        # `-O` names the file after the URL path's last segment, in the current directory.
        for w in urls:
            base = re.sub(r"^[a-zA-Z][\w+.-]*://[^/]*", "", w.text).rsplit("/", 1)[-1]
            if w.why or re.search(r"[?#%\[\]{}]", w.text) or base in ("", ".", ".."):
                out |= top("a curl -O name the guard cannot read")
            else:
                out |= paths("write", Word(base), ctx)
    return out, None


REMOTE = re.compile(r"^(?:[^@/\\:\s]+@)?([^/\\:\s]{2,}|\[[^\]]+\]):")


def m_rsync(name, args, ctx):
    """`rsync`/`scp` without link modes, remote shells or program options: those are not in their rows."""
    pos, _vals, flags = parse(name, args)
    kind = "write_tree" if "r" in shorts(flags) or "--recursive" in flags else "write"
    out = set()
    for k, w in enumerate(pos):
        if w.why:
            out |= top(f"{w.why} in a {name} operand")
        elif "://" in w.text:
            out |= network(w)
        elif REMOTE.match(w.text) or "::" in w.text:
            out |= {("network", host_of(re.sub(r"^[^@]*@", "", w.text).split("::")[0] + ":"))}
        elif k == len(pos) - 1 and k:
            # The destination may be an existing directory the sources land in, by their own names.
            out |= paths(kind, w, ctx) | into_dir(pos[:-1], [w], ctx, kind)
    return out, None


# -- git and gh ----------------------------------------------------------------

# git's own options before the verb that take no value; `-C`, `-c`, `--git-dir`, `--work-tree` are read in `m_git`.
GIT_GLOBAL = {"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects", "--version",
              "--help"}
# The options the log/diff family shares. `--output` is a write; `--ext-diff`, `--textconv` are not here.
GIT_LOG = ("-pusczwbRqiEm -M? -B? -C? -U: -n: -S: -G: -L: -O: --patch --no-patch --stat --numstat --shortstat --name-only "
           "--name-status --summary --check --raw --cached --staged --merge-base --no-index --exit-code --quiet "
           "--color --no-color --word-diff --diff-filter: --unified: --relative --minimal --patience --histogram "
           "--ignore-all-space --ignore-space-change --ignore-blank-lines --ignore-space-at-eol --ignore-cr-at-eol "
           "--abbrev --full-index --binary --no-prefix --src-prefix: --dst-prefix: --find-renames --no-renames "
           "--dirstat --oneline --format: --pretty: --graph --decorate --no-decorate --all --branches --tags "
           "--remotes --author: --committer: --since: --until: --after: --before: --grep: --follow --reverse "
           "--no-merges --merges --first-parent --date: --abbrev-commit --left-right --cherry-pick --cherry-mark "
           "--boundary --topo-order --date-order --max-count: --skip: --source --no-walk --stat-width: "
           "--output: --cc --full-history --simplify-by-decoration --show-signature --notes --no-notes")
# verb: (the flags its row understands, what its model does). A verb with no row is `top`.
GIT_VERBS = {
    "status": ("-sbvz -u? --short --branch --porcelain --long --untracked-files --ignored --ahead-behind "
               "--no-ahead-behind --show-stash --column --no-column --renames --no-renames --verbose", "read"),
    "diff": (GIT_LOG, "read"), "log": (GIT_LOG, "read"), "show": (GIT_LOG, "read"),
    "shortlog": ("-sneci --summary --numbered --email --committer --group: --since: --until: --all --format:", "read"),
    "rev-parse": ("-q --verify --quiet --short --abbrev-ref --symbolic --symbolic-full-name --show-toplevel "
                  "--show-prefix --show-cdup --git-dir --git-common-dir --absolute-git-dir --is-inside-work-tree "
                  "--is-inside-git-dir --is-bare-repository --is-shallow-repository --show-object-format --all "
                  "--branches --tags --remotes --not --default: --since: --until: --path-format: --git-path: --sq "
                  "--local-env-vars --show-superproject-working-tree --revs-only --no-revs --flags --no-flags", "read"),
    "rev-list": ("-n: --count --all --branches --tags --remotes --left-right --left-only --right-only --cherry-pick "
                 "--cherry-mark --max-count: --skip: --since: --until: --after: --before: --reverse --first-parent "
                 "--no-merges --merges --boundary --objects --topo-order --date-order --author: --grep: --format: "
                 "--pretty: --abbrev-commit --parents --children --quiet --not --ancestry-path", "read"),
    "ls-files": ("-cdmoiskzutvf -x: -X: --cached --deleted --modified --others --ignored --stage --unmerged --killed "
                 "--exclude: --exclude-from: --exclude-standard --error-unmatch --full-name --abbrev --directory "
                 "--no-empty-directory --format: --eol --sparse", "read"),
    "ls-tree": ("-rtdlz --name-only --name-status --object-only --long --full-name --full-tree --abbrev --format:",
                "read"),
    "cat-file": ("-tspe --allow-unknown-type", "read"),
    "blame": ("-lswepfntc -L: --porcelain --line-porcelain --incremental --root --show-stats --since: --date: "
              "--abbrev: --first-parent --ignore-rev: --color-lines --color-by-age", "read"),
    "grep": ("-niwvlLcEFGPhHIqorzaWp -e: -f: -A: -B: -C: -m: --cached --untracked --no-index --exclude-standard "
             "--no-exclude-standard --heading --break --line-number --column --ignore-case --word-regexp "
             "--invert-match --files-with-matches --files-without-match --count --name-only --full-name --all-match "
             "--and --or --not --max-depth: --threads: --only-matching --quiet --null --extended-regexp "
             "--fixed-strings --perl-regexp --basic-regexp --text --function-context --show-function --color "
             "--no-color", "read"),
    "describe": ("--tags --all --always --long --dirty --broken --exact-match --contains --first-parent --abbrev: "
                 "--match: --exclude: --candidates:", "read"),
    "merge-base": ("-a --all --octopus --independent --is-ancestor --fork-point", "read"),
    "show-ref": ("-qsd --heads --tags --head --verify --quiet --hash --dereference --abbrev --exists", "read"),
    "check-ignore": ("-qvnz --quiet --verbose --non-matching --no-index --stdin", "read"),
    "check-attr": ("-az --all --cached --stdin --source:", "read"),
    "for-each-ref": ("--format: --sort: --count: --points-at: --merged: --no-merged: --contains: --no-contains: "
                     "--shell --perl --python --tcl --exclude: --ignore-case --omit-empty", "read"),
    "name-rev": ("--name-only --tags --refs: --exclude: --all --no-undefined --always", "read"),
    "count-objects": ("-vH --verbose --human-readable", "read"),
    "version": ("", "read"),
    "ls-remote": ("-htq --heads --tags --refs --quiet --exit-code --get-url --sort: --symref", "ls-remote"),
    "add": ("-AuNfvn --all --update --intent-to-add --force --verbose --dry-run --renormalize --ignore-errors "
            "--ignore-removal --no-all --sparse --chmod:", "repo"),
    "commit": ("-qanvs -F: -m: -C: --all --quiet --amend --no-edit --allow-empty --allow-empty-message --signoff "
               "--verbose --no-verify --dry-run --author: --date: --fixup: --squash: --cleanup: --trailer: "
               "--message: --file: --reuse-message: --only --include --reset-author --no-gpg-sign", "repo"),
    "branch": ("-arvldDmMcCfqt -u: --all --remotes --verbose --list --show-current --delete --move --copy --force "
               "--contains: --no-contains: --merged: --no-merged: --sort: --format: --points-at: --set-upstream-to: "
               "--unset-upstream --track --no-track --column --no-column --color --no-color --quiet --abbrev:", "repo"),
    "tag": ("-adlfni -m: -F: --annotate --delete --list --force --sort: --contains: --no-contains: --merged: "
            "--points-at: --format: --column", "repo"),
    "update-index": ("-q --skip-worktree --no-skip-worktree --assume-unchanged --no-assume-unchanged --refresh "
                     "--really-refresh", "repo"),
    "reflog": ("-n: --date: --all --format: --pretty: --oneline --expire: --expire-unreachable: --rewrite "
               "--updateref --stale-fix --dry-run", "repo"),
    "push": ("-ufdqvn -o: --set-upstream --force --force-with-lease --force-if-includes --tags --follow-tags "
             "--delete --quiet --verbose --dry-run --no-verify --all --prune --atomic --porcelain --push-option:",
             "push"),
    "fetch": ("-qvpPtnfa -j: --quiet --verbose --prune --prune-tags --tags --no-tags --all --multiple --force "
              "--dry-run --depth: --deepen: --shallow-since: --unshallow --jobs: --refetch --filter: --append "
              "--atomic --keep --no-recurse-submodules", "fetch"),
    "pull": ("-qvr -X: --quiet --verbose --ff-only --ff --no-ff --rebase --no-rebase --autostash --no-autostash "
             "--no-edit --squash --no-squash --commit --no-commit --tags --no-tags --prune --depth: --all "
             "--strategy-option: --no-recurse-submodules", "pull"),
    "merge": ("-qvne -m: -F: -X: --stat --no-stat --ff --no-ff --ff-only --squash --no-squash --commit --no-commit "
              "--edit --no-edit --abort --continue --quit --allow-unrelated-histories --strategy-option: --log "
              "--no-log --autostash --no-verify --signoff --quiet --verbose", "tree"),
    "rebase": ("-qvf -X: --onto: --continue --abort --skip --quit --autostash --no-autostash --root --keep-base "
               "--update-refs --no-update-refs --empty: --rebase-merges --no-verify --force-rebase "
               "--strategy-option: --committer-date-is-author-date --signoff --fork-point --no-fork-point --quiet",
               "tree"),
    "cherry-pick": ("-nxs -m: --no-commit --mainline: --continue --abort --skip --quit --ff --allow-empty "
                    "--keep-redundant-commits --signoff", "tree"),
    "revert": ("-ns -m: --no-commit --mainline: --no-edit --edit --continue --abort --skip --quit --signoff", "tree"),
    "switch": ("-dfmqt -c: -C: --detach --force --discard-changes --merge --quiet --track --no-track --orphan: "
               "--guess --no-guess --create: --force-create:", "checkout"),
    "sparse-checkout": ("--cone --no-cone --sparse-index --no-sparse-index --skip-checks", "tree"),
    "checkout": ("-fqtm -b: -B: --orphan: --detach --force --quiet --track --no-track --merge --ours --theirs",
                 "checkout"),
    "restore": ("-SWqm -s: --source: --staged --worktree --quiet --ours --theirs --merge --conflict: "
                "--ignore-unmerged --overlay --no-overlay", "restore"),
    "reset": ("-qN --soft --mixed --hard --merge --keep --quiet --no-refresh --refresh", "reset"),
    "stash": ("-uakqS -m: --include-untracked --all --keep-index --no-keep-index --quiet --message: --staged --index",
              "stash"),
    "rm": ("-rfnq --cached --force --dry-run --quiet --ignore-unmatch --sparse", "rm"),
    "mv": ("-fknv --force --dry-run --verbose", "mv"),
    "worktree": ("-fqvnz -b: -B: --force --detach --checkout --no-checkout --lock --track --no-track --guess-remote "
                 "--no-guess-remote --orphan --quiet --verbose --porcelain --dry-run --expire: --reason:", "worktree"),
    "clone": ("-qn -b: -o: --quiet --no-checkout --branch: --origin: --depth: --shallow-since: --single-branch "
              "--no-single-branch --no-tags --filter: --bare --sparse", "clone"),
    "init": ("-q -b: --quiet --bare --initial-branch: --object-format:", "init"),
    "config": ("-lz -f: --list --get --get-all --get-regexp --unset --unset-all --add --replace-all --rename-section "
               "--remove-section --global --local --worktree --file: --show-origin --show-scope --name-only --type: "
               "--bool --int --path --default: --null", "config"),
    "remote": ("-vf -t: -m: --verbose --tags --no-tags --push --add --delete", "remote"),
    "clean": ("-fdxXnq -e: --force --dry-run --quiet --exclude:", "clean"),
}
GIT = {verb: (row(None, spec, digits=True), kind) for verb, (spec, kind) in GIT_VERBS.items()}
GIT_RUNS = re.compile(r"^alias\.|command|editor|pager|hookspath|askpass|program|helper|textconv|^filter\.|"
                      r"\.cmd$|external|proxy|exec|fsmonitor|gpg|\.tool$|^include|^url\.|^core\.worktree$", re.I)


def git_key(w: Word) -> set:
    if w.why:
        return top(f"{w.why} in a git config value")
    return top("a git config key that runs a program") if GIT_RUNS.search(w.text.split("=")[0]) else set()


def repo_roots(ctx) -> set | None:
    """The work tree each place `ctx` may run in belongs to: the nearest folder holding `.git`, or the
    checkout the grant was read for when no nearer one exists on disk. `None` when a place is unknown."""
    roots = set()
    for cwd in ctx.cwds:
        if cwd is None:
            return None
        p, found = cwd.rstrip("/") or "/", None
        while found is None:
            if ctx.repo and p.lower() == ctx.repo.rstrip("/").lower() or os.path.exists(p + "/.git"):
                found = p
            elif (up := posixpath.dirname(p)) == p:
                return None
            else:
                p = up
        roots.add(found)
    return roots


def own_repo(ctx) -> bool:
    """Every place `ctx` may run in is inside the checkout whose remotes the grant read."""
    roots = repo_roots(ctx)
    return bool(ctx.repo) and roots is not None and all(r.lower() == ctx.repo.rstrip("/").lower() for r in roots)


def remotes_of(pos: list, ctx, push: bool) -> set:
    named = dict(ctx.remotes)
    own = own_repo(ctx)
    if not pos:
        return {("network", h) for h in named.values()} if own else top("the remotes of another repository")
    w, t = pos[0], pos[0].text
    if w.why:
        return top(f"{w.why} in a git remote")
    if t in named and own:
        return {("network", named[t])}
    if "://" in t and not t.startswith("file:"):
        return network(w)
    if REMOTE.match(t):
        return {("network", host_of(t))}
    if "/" in t or "\\" in t or t.startswith("."):
        return paths("write", w, ctx) if push else set()
    return top(f"git remote {t} is not configured" if own else f"git remote {t} of another repository")


def cloned(pos: list, ctx) -> set:
    """The tree a clone lays down: into the named directory, or one named after the repository. A new
    repository's `core.symlinks` is not the checkout's, so its tree may always hold a link."""
    base = re.sub(r"\.git$", "", re.split(r"[/:\\]", pos[0].text.rstrip("/"))[-1])
    return paths("linktree", pos[1] if len(pos) > 1 else Word(base, pos[0].why), ctx)


def based(pos: list, flags: list, ctx) -> bool:
    """`git merge <commit>` naming exactly the base commit the server asked this task to merge."""
    oid = pos[0].text.lower() if len(pos) == 1 and not pos[0].why else ""
    return bool(ctx.merge_base) and len(oid) >= 7 and ctx.merge_base.startswith(oid) and not set(flags) - {
        "--no-edit", "--no-ff", "--no-commit", "--quiet", "-q", "--edit", "--commit", "-m"}


def rewritten(g, trusted: bool) -> set:
    """A verb that moves the work tree to another commit: it may change any tracked file, so it is a tree
    write of each work tree, which `outside` refuses wherever that tree holds a file deciding the grant.
    Only a merge of the exact base commit the server recorded is trusted; its links are still tracked."""
    roots = repo_roots(g)
    if roots is None:
        return top("a git verb that rewrites a work tree it cannot place")
    # Without links (`core.symlinks` off) it writes files under the tree but makes no link to write through.
    out = set() if trusted else {("write_tree" if g.git_links else "write", r) for r in roots}
    if g.git_links:
        # Where git checks symlinks out as links, a rewritten work tree may hold one.
        out |= {("repolinks", r) for r in roots}
    return out


def m_git(name, args, ctx):
    out, i, g = set(), 0, ctx
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return out | top(f"{w.why} as a git option or verb"), None
        if t in ("-C", "-c") and i + 1 < len(args):
            v = args[i + 1]
            if t == "-C":
                g = replace(g, cwds=moved_to(v, g))
            out |= git_key(v) if t == "-c" else set()
            i += 2
            continue
        if t.startswith(("--git-dir", "--work-tree")):
            # Another repository's directory: its remotes and its work tree are not the ones read.
            return out | top(f"git {t.split('=')[0]} selects a repository the guard has not read"), None
        if t not in GIT_GLOBAL:
            if t.startswith("-"):
                return out | top(f"git {t}: an option the table does not model"), None
            break
        i += 1
    if i >= len(args):
        return out, None
    verb, rest = args[i].text, args[i + 1:]
    if verb not in GIT:
        return out | top(f"git {verb}: a verb the table does not model"), None
    r, kind = GIT[verb]
    if stop := gate(f"git {verb}", rest, r):
        return out | stop, None
    pos, vals, flags = getopt(rest, r.takes)
    out |= writes("write", vals.get("--output", []), g)        # `git diff --output=f`
    if kind == "read":
        return out, None
    if kind == "ls-remote":
        return out | remotes_of(pos, g, False), None
    # git's write of the repository it runs in is the granted git verb; `outside` judges its place only.
    out |= paths("repo", here(g), g)
    sub, short = pos[0].text if pos else "", shorts(flags)
    pathspec_only = any(w.text == "--" and not w.why for w in rest)
    rewrites = kind in ("tree", "pull") and not {"--continue", "--quit"} & set(flags) \
        or kind == "checkout" and not pathspec_only and (
            pos or not {"-b", "-B", "-c", "-C", "--orphan", "--create", "--force-create"} & set(vals)) \
        or kind == "reset" and {"--hard", "--merge", "--keep"} & set(flags) \
        or kind == "stash" and sub in ("", "push", "save", "pop", "apply", "branch")
    if rewrites:
        out |= rewritten(g, verb == "merge" and based(pos, flags, ctx))
    named = "linktree" if ctx.git_links else "write"
    if kind in ("push", "fetch", "pull"):
        out |= remotes_of(pos, g, kind == "push")
    elif kind in ("checkout", "restore"):
        # Every operand may be a pathspec the verb rewrites from a commit or the index.
        out |= writes(named, pos, g)
    elif kind == "rm":
        out |= writes("delete", pos, g)
    elif kind == "mv" and len(pos) > 1:
        # A rename moves a symlink without following it, whatever `core.symlinks` says.
        out |= writes("linktree", pos[-1:], g) | into_dir(pos[:-1], pos[-1:], g, "linktree")
        out |= writes("delete", pos[:-1], g)
    elif kind == "clone" and pos:
        out |= network(pos[0]) if ("://" in sub or REMOTE.match(sub)) else set()
        out |= cloned(pos, g)
    elif kind == "init" and pos:
        out |= paths("write", pos[0], g)
    elif kind == "worktree" and len(pos) > 1:
        k = {"add": named, "move": "write", "remove": "delete"}.get(sub)
        out |= paths(k, pos[-1] if sub == "move" else pos[1], g) if k else set()
    elif kind == "config":
        files = vals.get("-f", []) + vals.get("--file", [])
        changes = len(pos) > 1 or any(f.startswith(("--add", "--replace", "--unset", "--rename", "--remove"))
                                      for f in flags)
        if changes and not files and "--global" not in flags:
            # The repository's own configuration names its remotes, whose hosts the next call's grant reads.
            return out | top("git config writes the repository configuration the grant is read from"), None
        if "--global" in flags:
            out |= paths("write", Word("~/.gitconfig"), g)
        out |= writes("write", files, g)
        out |= git_key(pos[0]) if changes else set()
    elif kind == "remote":
        if sub not in ("", "show", "get-url", "update", "prune") or "f" in short or "--add" in flags:
            # `add`, `set-url`, `rename`…: a remote's URL is a host the next call's grant reads.
            return out | top(f"git remote {sub}: writes the remotes the grant is read from"), None
        if sub in ("update", "prune", "show"):
            out |= remotes_of([], g, False)
    elif kind == "clean" and "n" not in short and "--dry-run" not in flags:
        # Untracked files anywhere in the tree go, the protected ones among them.
        out |= paths("delete", here(g), g)
    elif kind == "stash":
        if {"u", "a"} & set(short) or {"--include-untracked", "--all"} & set(flags):
            out |= paths("delete", here(g), g)
        out |= writes(named, pos[1:], g) if sub == "push" else set()
    return out, None


GH_OUT = " -q: -t: --jq: --template: --json:"
# (command, subcommand): the flags its row understands. `--web` (a browser) is in none of them.
GH_COMMANDS = {
    ("pr", "view"): "-c --comments" + GH_OUT,
    ("pr", "list"): "-d -L: -s: -B: -H: -l: -a: -A: -S: --limit: --state: --base: --head: --label: --assignee: "
                    "--author: --search: --draft" + GH_OUT,
    ("pr", "diff"): "--name-only --patch --color:",
    ("pr", "checks"): "-i: --watch --fail-fast --required --interval:" + GH_OUT,
    ("pr", "status"): "-c --conflict-status" + GH_OUT,
    ("pr", "create"): "-df -t: -b: -F: -B: -H: -r: -a: -l: -m: -p: --title: --body: --body-file: --base: --head: "
                      "--reviewer: --assignee: --label: --milestone: --project: --draft --fill --fill-first "
                      "--fill-verbose --no-maintainer-edit --dry-run",
    ("pr", "merge"): "-smrd -b: -F: -t: -A: --squash --merge --rebase --delete-branch --auto --disable-auto --admin "
                     "--match-head-commit: --subject: --body: --body-file: --author-email:",
    ("pr", "comment"): "-b: -F: --body: --body-file: --edit-last",
    ("pr", "edit"): "-t: -b: -F: -B: --title: --body: --body-file: --base: --add-label: --remove-label: "
                    "--add-reviewer: --remove-reviewer: --add-assignee: --remove-assignee: --milestone:",
    ("pr", "close"): "-c: --comment:",
    ("pr", "reopen"): "-c: --comment:",
    ("pr", "ready"): "--undo",
    ("pr", "review"): "-acr -b: -F: --approve --comment --request-changes --body: --body-file:",
    ("pr", "checkout"): "-f -b: --branch: --force --detach",
    ("issue", "view"): "-c --comments" + GH_OUT,
    ("issue", "list"): "-L: -s: -l: -a: -A: -S: -m: --limit: --state: --label: --assignee: --author: --search: "
                       "--milestone:" + GH_OUT,
    ("issue", "create"): "-t: -b: -F: -a: -l: -m: -p: --title: --body: --body-file: --assignee: --label: "
                         "--milestone: --project:",
    ("issue", "comment"): "-b: -F: --body: --body-file: --edit-last",
    ("issue", "edit"): "-t: -b: -F: --title: --body: --body-file: --add-label: --remove-label: --add-assignee: "
                       "--remove-assignee: --milestone:",
    ("issue", "close"): "-c: -r: --comment: --reason:",
    ("issue", "reopen"): "-c: --comment:",
    ("run", "view"): "-v -j: -a: --job: --attempt: --log --log-failed --exit-status --verbose" + GH_OUT,
    ("run", "list"): "-a -L: -b: -w: -s: -u: -e: -c: --limit: --branch: --workflow: --status: --user: --event: "
                     "--commit: --all" + GH_OUT,
    ("run", "watch"): "-i: --interval: --exit-status --compact",
    ("run", "rerun"): "-d -j: --failed --debug --job:",
    ("run", "cancel"): "",
    ("repo", "view"): "-b: --branch:" + GH_OUT,
    ("repo", "list"): "-L: --limit: --visibility: --source --fork --archived --no-archived --language: --topic:"
                      + GH_OUT,
    ("repo", "clone"): "-u: --upstream-remote-name: --no-upstream",
    ("release", "view"): GH_OUT,
    ("release", "list"): "-L: --limit: --exclude-drafts --exclude-pre-releases --order:" + GH_OUT,
    ("workflow", "view"): "-y -r: --ref: --yaml",
    ("workflow", "list"): "-a -L: --limit: --all" + GH_OUT,
    ("workflow", "run"): "-r: -f: -F: --ref: --raw-field: --field: --json",
    ("api", ""): "-i -X: -H: -f: -F: -p: -q: -t: --method: --header: --raw-field: --field: --preview: --include "
                 "--paginate --slurp --silent --verbose --cache: --hostname: --input: --jq: --template:",
    ("auth", "status"): "-at -h: --hostname: --active --show-token",
}
GH = {key: row(None, spec + " -R: --repo:") for key, spec in GH_COMMANDS.items()}
GH_ANY_TAKES = frozenset().union(*(r.takes for r in GH.values()))


def m_gh(name, args, ctx):
    if not args or args[0].text in ("--version", "version", "help", "--help"):
        return set(), None
    # gh finds its command among the words that are not flags, wherever the flags stand.
    pos = getopt(args, GH_ANY_TAKES)[0]
    verb = pos[0] if pos else Word()
    sub = pos[1] if len(pos) > 1 and verb.text != "api" else Word()
    if verb.why or sub.why or (verb.text, sub.text) not in GH:
        return top(f"gh {verb.text} {sub.text}: a command the table does not model".replace("  ", " ")), None
    r = GH[(verb.text, sub.text)]
    rest = [w for w in args if w is not verb and w is not sub]
    if stop := gate(f"gh {verb.text}", rest, r):
        return stop, None
    pos, vals, flags = getopt(rest, r.takes)
    verb, sub = verb.text, sub.text
    env = dict(a.split("=", 1) for a in ctx.env if "=" in a)
    out = set()
    for w in vals.get("--hostname", []) or [Word(env.get("GH_HOST", "github.com"))]:
        out |= network(w)
    # `-R`/`--repo` and `GH_REPO` are `[HOST/]OWNER/REPO`: a host when there are three parts, unknown if unread.
    for repo in vals.get("-R", []) + vals.get("--repo", []) + [Word(env.get("GH_REPO", ""))]:
        if repo.why:
            out |= top(f"{repo.why} in a gh repository")
        elif repo.text.count("/") >= 2:
            out |= network(Word(repo.text.split("/")[0]))
    if verb == "api":
        # `gh api` sends an absolute endpoint to that URL's own host, deciding after expansion, so an
        # endpoint it cannot read may be one too.
        for w in pos:
            out |= network(w) if w.why or "://" in w.text else set()
    # An operand that is a URL, `git@host:` or (for a clone) `HOST/OWNER/REPO` selects the host gh reaches.
    for w in pos[:1] if verb == "api" else pos:
        if w.why and (verb, sub) == ("repo", "clone"):
            out |= top(f"{w.why} in the repository gh clones")
        elif "://" in w.text or REMOTE.match(w.text):
            out |= network(w)
        elif (verb, sub) == ("repo", "clone") and w is pos[0] and w.text.count("/") >= 2:
            out |= network(Word(w.text.split("/")[0]))
    if not (vals.get("-R") or vals.get("--repo") or env.get("GH_REPO")) and verb not in ("api", "auth") \
            and (verb, sub) not in (("repo", "clone"), ("repo", "list")):
        # Without `-R`, gh reads the repository from the remotes where it runs.
        out |= remotes_of([], ctx, False)
    if (verb, sub) == ("repo", "clone") and pos:
        if any(w.text == "--" and not w.why for w in rest):
            return out | top("gh repo clone passes options on to git"), None
        out |= cloned(pos, ctx)
    elif (verb, sub) == ("pr", "checkout") or (verb, sub) == ("pr", "merge") and (
            "-d" in flags or "--delete-branch" in flags or "d" in shorts(flags)):
        # A checkout of the repository it runs in (deleting the branch switches to and pulls the base).
        out |= paths("repo", here(ctx), ctx) | rewritten(ctx, False)
    return out, None


def subscripted(ws: list) -> set:
    """A variable name with a subscript (`a[i]`) has bash evaluate the subscript as arithmetic."""
    if any(w.why and w.why != "group" or "[" in w.text for w in ws):
        return top("arithmetic evaluation in a variable subscript")
    return set()


def m_export(name, args, ctx):
    words = parse(name, args)[0]
    return envs([w for w in words if assignment(w)], ctx) | subscripted(
        [w for w in words if not assignment(w)]), None


def m_names(name, args, ctx):
    """Builtins that take variable names (`read`, `unset`, `mapfile`, `printf -v`)."""
    if name == "printf":
        named = args[1:2] if args[:1] and args[0].text == "-v" and not args[0].why else \
            [Word(a.text[2:], a.why) for a in args[:1] if a.text.startswith("-v") and len(a.text) > 2]
    else:
        pos, vals, _ = parse(name, args)
        named = pos + vals.get("-a", [])
    # The name is assigned (or unset) like `NAME=…`: `PATH`, `TMPDIR`, `GIT_SSH_COMMAND`… change later effects.
    return subscripted(named) | names_set(named, ctx), None


ARITHMETIC = {"-eq", "-ne", "-lt", "-le", "-gt", "-ge"}


def m_test(name, args, ctx):
    """`[[` compares `-eq`… operands as arithmetic; `-v` evaluates an array subscript in `[[`, `[` and `test`."""
    bad = {"-v"} | (ARITHMETIC if name == "[[" else set())
    return (top(f"{name} evaluates arithmetic") if any(w.text in bad for w in args) else set()), None


# -- The table -----------------------------------------------------------------

GREP = ("-EFGPiyvwxcLlomqsbHhnTZzaIrRU -e: -f: -m: -A: -B: -C: -d: -D: --regexp: --file: --max-count: "
        "--after-context: --before-context: --context: --include: --exclude: --exclude-dir: --exclude-from: --label: "
        "--binary-files: --devices: --directories: --color --colour --extended-regexp --fixed-strings "
        "--basic-regexp --perl-regexp --ignore-case --no-ignore-case --invert-match --word-regexp --line-regexp "
        "--count --files-with-matches --files-without-match --only-matching --quiet --silent --no-messages "
        "--byte-offset --with-filename --no-filename --line-number --initial-tab --null --null-data --text "
        "--recursive --dereference-recursive --line-buffered")
SUMS = "-bctwz --binary --check --text --tag --quiet --status --strict --warn --zero --ignore-missing"
RG = ("-nNiIsSlcvwxFuUoqHhaL0p -g: -t: -T: -A: -B: -C: -m: -e: -f: -d: -M: -j: -E: -r: --files --hidden --no-ignore "
      "--no-ignore-vcs --glob: --iglob: --type: --type-not: --count --count-matches --files-with-matches "
      "--files-without-match --line-number --no-line-number --ignore-case --smart-case --case-sensitive "
      "--fixed-strings --word-regexp --line-regexp --invert-match --only-matching --max-count: --max-depth: "
      "--context: --after-context: --before-context: --json --vimgrep --no-heading --heading --with-filename "
      "--no-filename --color: --sort: --sortr: --multiline --follow --null --text --stats --trim --column --pretty "
      "--replace: --regexp: --file: --encoding: --max-columns: --threads: --quiet --type-list --max-filesize:")
DIFF = ("-uqrNawBbiItcysdpe -U: -C: -x: -X: -L: -I: -F: -W: --unified: --context: --brief --recursive --new-file "
        "--text --ignore-all-space --ignore-space-change --ignore-case --ignore-blank-lines --strip-trailing-cr "
        "--label: --exclude: --exclude-from: --color --side-by-side --width: --report-identical-files "
        "--suppress-common-lines --normal --minimal --speed-large-files --ignore-matching-lines: --expand-tabs "
        "--initial-tab --show-c-function --show-function-line: --no-dereference --to-file: --from-file:")
EXPORT = "-aAfFglrtuxp"                 # no `-i` (arithmetic) and no `-n` (a name reference)
PREFIX = dict(stop=True)

TABLE = {
    # Readers: no option of these writes, runs or connects (`inert`); every flag they take is still listed.
    "cat": row(m_read, "-AbeEnstTuv --show-all --number --number-nonblank --squeeze-blank --show-ends --show-tabs "
                       "--show-nonprinting", inert=True),
    "head": row(m_read, "-qvz -n: -c: --lines: --bytes: --quiet --silent --verbose --zero-terminated", digits=True,
                inert=True),
    "tail": row(m_read, "-fFqvzr -n: -c: -s: --lines: --bytes: --follow --retry --pid: --sleep-interval: --quiet "
                        "--silent --verbose --zero-terminated", digits=True, inert=True),
    "grep": row(m_read, GREP, digits=True, inert=True),
    "egrep": row(m_read, GREP, digits=True, inert=True),
    "fgrep": row(m_read, GREP, digits=True, inert=True),
    "wc": row(m_read, "-clmwL --bytes --chars --lines --words --max-line-length --total:", inert=True),
    "cut": row(m_read, "-sz -b: -c: -d: -f: --complement --only-delimited --zero-terminated --output-delimiter: "
                       "--bytes: --characters: --delimiter: --fields:", inert=True),
    "tr": row(m_read, "-cCdst --complement --delete --squeeze-repeats --truncate-set1", inert=True),
    "ls": row(m_read, "-1aAlhdtrSRFGQcuUXvxiognspmCLHBNk -I: -w: -T: --all --almost-all --human-readable --directory "
                      "--recursive --color --time-style: --sort: --format: --group-directories-first --full-time "
                      "--classify --indicator-style: --ignore: --hide: --width: --quoting-style: --literal "
                      "--dereference --inode --size --block-size: --reverse --time:", inert=True),
    "echo": row(m_read, free=True), "true": row(m_read, free=True), "false": row(m_read, free=True),
    ":": row(m_read, free=True), "sleep": row(m_read, free=True), "expr": row(m_read, free=True),
    "yes": row(m_read, free=True), "help": row(m_read, free=True), "wait": row(m_read, free=True),
    "exit": row(m_read, free=True), "return": row(m_read, free=True), "break": row(m_read, free=True),
    "continue": row(m_read, free=True), "shift": row(m_read, free=True), "where": row(m_read, free=True),
    "printf": row(m_names, free=True),
    "test": row(m_test, free=True), "[": row(m_test, free=True), "[[": row(m_test, free=True),
    "pwd": row(m_read, "-LP", inert=True),
    "basename": row(m_read, "-asz -s: --multiple --suffix: --zero", inert=True),
    "dirname": row(m_read, "-z --zero", inert=True),
    "realpath": row(m_read, "-emsqLPz --relative-to: --relative-base: --canonicalize-existing "
                            "--canonicalize-missing --no-symlinks --logical --physical --quiet --strip --zero",
                    inert=True),
    "readlink": row(m_read, "-fenmqsvz --canonicalize --canonicalize-existing --canonicalize-missing --no-newline "
                            "--quiet --silent --verbose --zero", inert=True),
    "seq": row(m_read, "-w -s: -f: --separator: --format: --equal-width", digits=True, inert=True),
    "date": row(m_read, "-uIR -d: -r: --utc --universal --date: --reference: --iso-8601 --rfc-3339: --rfc-email"),
    "du": row(m_read, "-shacSbkmLxDH0l -d: -B: -t: --max-depth: --summarize --human-readable --apparent-size "
                      "--exclude: --total --block-size: --threshold: --all --bytes --count-links --dereference "
                      "--one-file-system --si", inert=True),
    "df": row(m_read, "-hTPaklHi -t: -x: --total --human-readable --print-type --portability --all --local --inodes "
                      "--type: --exclude-type:", inert=True),
    "printenv": row(m_read, "-0 --null", inert=True), "whoami": row(m_read, "", inert=True),
    "id": row(m_read, "-ugGnrz", inert=True), "nproc": row(m_read, "--all --ignore:", inert=True),
    "uname": row(m_read, "-asnrvmpio --all --kernel-name --nodename --kernel-release --kernel-version --machine "
                         "--processor --hardware-platform --operating-system", inert=True),
    "tty": row(m_read, "-s", inert=True),
    "jq": row(m_read, "-rcnsjSaeCMR -f: --arg: --argjson: --slurpfile: --rawfile: --indent: --raw-output "
                      "--compact-output --null-input --slurp --join-output --sort-keys --ascii-output --exit-status "
                      "--tab --args --jsonargs --raw-input --raw-output0 --seq --stream --color-output "
                      "--monochrome-output", inert=True),
    "od": row(m_read, "-bcdfiloxsv -A: -j: -N: -t: -w? --address-radix: --skip-bytes: --read-bytes: --format: "
                      "--output-duplicates", inert=True),
    "hexdump": row(m_read, "-bcCdovx -n: -s: -e: -f:", inert=True),
    **{s: row(m_read, SUMS, inert=True) for s in ("md5sum", "sha1sum", "sha256sum", "sha512sum", "b2sum", "cksum")},
    "base64": row(m_read, "-di -w: --decode --ignore-garbage --wrap:", inert=True),
    "column": row(m_read, "-tnxeJ -s: -o: -c: -N: -R: -W: -H: -l: --table --separator: --output-separator:",
                  inert=True),
    "comm": row(m_read, "-123z --check-order --nocheck-order --output-delimiter: --total --zero-terminated",
                inert=True),
    "paste": row(m_read, "-sz -d: --serial --delimiters: --zero-terminated", inert=True),
    "fold": row(m_read, "-bs -w: --bytes --spaces --width:", inert=True),
    "rev": row(m_read, "", inert=True),
    "tac": row(m_read, "-br -s: --before --regex --separator:", inert=True),
    "strings": row(m_read, "-afw -n: -t: -e: --all --bytes: --radix: --encoding:", digits=True, inert=True),
    "ps": row(m_read, "-aefxlwjAHTW -p: -o: -u: -C: -t: --pid: --forest --sort:", inert=True),
    "netstat": row(m_read, "-anobersqtxy -p:", digits=True, inert=True),
    "cygpath": row(m_read, "-uwmalpsdDHOPSFCA -t: -c: --unix --windows --mixed --absolute --path --long-name "
                           "--short-name --type: --dos --option", inert=True),
    "wslpath": row(m_read, "-uwma", inert=True),
    "which": row(m_read, "-as --all --skip-alias --skip-functions --show-dot --show-tilde", inert=True),
    "type": row(m_read, "-afptP", inert=True),
    "jobs": row(m_read, "-lnprs", inert=True),
    "history": row(m_read, "", inert=True),
    "shopt": row(m_read, "-psuqo", inert=True),
    "umask": row(m_read, "-pS", inert=True),
    "hash": row(m_read, "-rdtl", inert=True),          # no `-p`: binding a name to another file
    "unalias": row(m_read, "-a", inert=True),
    "set": row(m_read, "-euxvfhnaBCHmPTEb -o:", inert=True),
    "diff": row(m_read, DIFF, inert=True),
    "cmp": row(m_read, "-bls -i: -n: --bytes: --ignore-initial: --silent --quiet --verbose --print-bytes",
               inert=True),
    "stat": row(m_read, "-LftZ -c: --format: --printf: --dereference --file-system --terse --cached:", inert=True),
    "rg": row(m_read, RG, digits=True),            # not inert: `--pre` runs a program, and it is not listed
    "read": row(m_names, "-ers -a: -d: -i: -n: -N: -p: -t: -u:", inert=True),
    "mapfile": row(m_names, "-t -d: -n: -O: -s: -u:", inert=True),
    "readarray": row(m_names, "-t -d: -n: -O: -s: -u:", inert=True),
    "unset": row(m_names, "-fvn", inert=True),
    # Readers that also write where their output option or second operand points.
    "sort": row(m_output, "-bdfgiMhnRrVsuzcC -k: -t: -o: -S: --key: --field-separator: --output: --unique --reverse "
                          "--numeric-sort --human-numeric-sort --version-sort --general-numeric-sort --month-sort "
                          "--random-sort --ignore-case --ignore-leading-blanks --dictionary-order "
                          "--ignore-nonprinting --stable --check --zero-terminated --buffer-size: --parallel: --debug"),
    "uniq": row(m_output, "-cdDiuz -f: -s: -w: --count --repeated --unique --ignore-case --skip-fields: "
                          "--skip-chars: --check-chars: --zero-terminated --all-repeated --group", inert=True),
    "iconv": row(m_output, "-cs -f: -t: -o: --from-code: --to-code: --output: --list --silent"),
    # Files.
    "rm": row(m_delete, "-rRfivdI --recursive --force --interactive --verbose --dir --one-file-system "
                        "--preserve-root", inert=True),
    "rmdir": row(m_delete, "-pv --parents --ignore-fail-on-non-empty --verbose", inert=True),
    "unlink": row(m_delete, "", inert=True),
    "mkdir": row(m_create, "-pv -m: --parents --verbose --mode:", inert=True),
    "touch": row(m_create, "-acmfh -d: -r: -t: --date: --reference: --no-create --no-dereference --time:",
                 inert=True),
    "chmod": row(m_create, "-RvfcrwxXst --recursive --verbose --changes --silent --quiet --reference: "
                           "--preserve-root", inert=True),
    "truncate": row(m_create, "-co -s: -r: --size: --reference: --no-create --io-blocks", inert=True),
    "tee": row(m_create, "-aip --append --ignore-interrupts --output-error", inert=True),
    "cp": row(m_copy, "-rRfinvuTL -t: --recursive --force --interactive --no-clobber --verbose --update "
                      "--no-target-directory --dereference --target-directory: --strip-trailing-slashes "
                      "--remove-destination --debug"),
    "mv": row(m_copy, "-finvuT -t: --force --interactive --no-clobber --verbose --update --no-target-directory "
                      "--target-directory: --strip-trailing-slashes --debug"),
    "dd": row(m_dd, free=True),
    "mktemp": row(m_mktemp, "-dqut -p: --tmpdir: --suffix: --directory --quiet --dry-run", inert=True),
    "tar": row(m_tar, free=True),
    "gzip": row(m_compress, "-kcvfqtlnN --keep --stdout --to-stdout --verbose --force --quiet --test --list "
                            "--no-name --name --best --fast", digits=True),
    "xz": row(m_compress, "-kcvfqtle -T: --keep --stdout --to-stdout --verbose --force --quiet --test --list "
                          "--threads: --extreme", digits=True),
    "bzip2": row(m_compress, "-kcvfqts --keep --stdout --verbose --force --quiet --test --small --best --fast",
                 digits=True),
    "zstd": row(m_compress, "-kcvfqt -o: -T: --keep --stdout --verbose --force --quiet --test --threads: --ultra "
                            "--long", digits=True),
    "cd": row(m_cd, "-LPe", inert=True), "pushd": row(m_cd, "-n", inert=True), "popd": row(m_cd, "-n", inert=True),
    "export": row(m_export, "-fnp"), "declare": row(m_export, EXPORT), "typeset": row(m_export, EXPORT),
    "local": row(m_export, EXPORT), "readonly": row(m_export, "-aAfp"),
    # Text tools whose program text is read for writes and runs.
    "sed": row(m_sed, "-nEruzs -e: -f: -l: -i? --quiet --silent --regexp-extended --null-data --zero-terminated "
                      "--separate --posix --debug --sandbox --unbuffered --expression: --file: --line-length: "
                      "--in-place"),
    **{a: row(m_awk, "-F: -v: -f: -e: -i: --field-separator: --assign: --file: --source: --include:")
       for a in ("awk", "gawk", "mawk", "nawk")},
    "find": row(m_find, free=True),
    # Programs that run the command they are given.
    "xargs": row(m_xargs, "-0rtpx -I: -n: -P: -L: -s: -d: -E: -a: -i? --null --no-run-if-empty --verbose "
                          "--interactive --exit --max-args: --replace: --max-procs: --delimiter: --arg-file: "
                          "--max-lines: --max-chars: --eof:", stop=True),
    "env": row(m_prefix, "-i0 -u: -C: -S: --ignore-environment --null --unset: --chdir: --split-string:", **PREFIX),
    "timeout": row(m_prefix, "-v -k: -s: --kill-after: --signal: --preserve-status --foreground --verbose", **PREFIX),
    "nice": row(m_prefix, "-n: --adjustment:", digits=True, **PREFIX),
    "nohup": row(m_prefix, "", **PREFIX), "builtin": row(m_prefix, "", **PREFIX), "winpty": row(m_prefix, "", **PREFIX),
    "time": row(m_prefix, "-pav -o: -f: --portability --append --verbose --output: --format:", **PREFIX),
    "command": row(m_prefix, "-pvV", **PREFIX),
    "exec": row(m_prefix, "-cl -a:", **PREFIX),
    "stdbuf": row(m_prefix, "-i: -o: -e: --input: --output: --error:", **PREFIX),
    "trap": row(m_trap, free=True),
    **{s: row(m_sh, free=True) for s in SHELLS},
    **{s: row(m_pwsh, free=True) for s in PS_SHELLS},
    "cmd": row(m_cmd, free=True),
    # Network.
    "curl": row(m_curl, "-sSfiIkvgGNOq#46 -o: -m: -w: -H: -X: -d: -A: -u: -e: -b: -F: -T: -r: --silent "
                        "--show-error --fail --fail-with-body --include --head --insecure --verbose --globoff --get "
                        "--compressed --no-buffer --no-progress-meter --progress-bar --remote-name --create-dirs "
                        "--http1.1 --http2 --ipv4 --ipv6 --output: --max-time: --connect-timeout: --retry: "
                        "--retry-delay: --retry-max-time: --retry-connrefused --retry-all-errors --write-out: "
                        "--header: --request: --data: --data-raw: --data-binary: --data-urlencode: --json: "
                        "--user-agent: --user: --referer: --cookie: --form: --form-string: --upload-file: --range: "
                        "--url: --max-filesize: --limit-rate: --oauth2-bearer:"),
    "rsync": row(m_rsync, "-rvzncuhqiPLtW --recursive --verbose --compress --dry-run --checksum --update "
                          "--human-readable --quiet --itemize-changes --progress --partial --stats --delete "
                          "--delete-after --times --copy-links --whole-file --exclude: --include: --exclude-from: "
                          "--include-from: --max-size: --min-size: --bwlimit: --timeout: --port:"),
    "scp": row(m_rsync, "-rqpCv346B -P: -i: -l:"),
    "git": row(m_git, free=True),
    "gh": row(m_gh, free=True),
    # Listed only to refuse: what these do is decided by data, links or code the argv does not show.
    "ln": row(refuse("creates a link the guard cannot follow"), free=True),
    **{z: row(refuse("extracts an archive into names the archive decides"), free=True)
       for z in ("unzip", "gunzip", "unxz", "bunzip2", "unzstd")},
    "patch": row(refuse("writes the files its patch names"), free=True),
    "split": row(refuse("names its output files itself"), free=True),
    "csplit": row(refuse("names its output files itself"), free=True),
    "wget": row(refuse("follows redirects and names its downloads itself"), free=True),
    "let": row(refuse("evaluates arithmetic, which runs what its variables hold"), free=True),
    "alias": row(refuse("defines a command the guard cannot follow"), free=True),
    **{e: row(refuse("runs code the guard cannot read"), free=True) for e in ("eval", "source", ".")},
}


def spawn_of(raw: str, ctx) -> set:
    """`spawn` of the program `raw` names, keyed by what identifies it.

    A bare name is looked up on PATH and keyed by that name. A name with a path is that very
    file, keyed by where it resolves, so neither a planted `./cat` nor `../outside/python` borrows
    the model or the grant of the program its basename suggests; the grant names it by path.
    """
    if not re.search(r"[\\/]", raw):
        return {("spawn", exe(raw))}
    out = set()
    for cwd in ctx.cwds:
        try:
            where = resolve(raw, cwd, ctx)
        except ValueError as error:
            return top(str(error))
        if where is None:
            return top("a device as a program")
        # The file as written, suffix and all: `helper.cmd` and `helper.exe` are different programs.
        out.add(("spawn", where.lower() if ctx.windows else where))
    return out


# Kinds that may lay down a tree holding a link: a command carrying one is bundled so `guard.collapse`
# can tell its own paths from what other commands write through it.
LINKING = ("linktree", "write_tree", "repolinks")


def run_argv(words: list, ctx) -> tuple[set, object]:
    """Effects of running `words[0]` with the rest as its arguments."""
    eff, moved = dispatch(words, ctx)
    if any(e[0] in LINKING for e in eff):
        eff = {("bundle", frozenset(eff))}
    return eff, moved


def dispatch(words: list, ctx) -> tuple[set, object]:
    if not words:
        return set(), None
    raw, args = words[0].text, words[1:]
    if words[0].why:
        return top(f"{words[0].why} as the command name"), None
    if re.search(r"[\\/]", raw):
        # A command named by a path is that file, not the PATH program of the same name: no model applies.
        return spawn_of(raw, ctx), None
    if ctx.d in ("ps", "cmd"):
        from guard_windows import CMD_BUILTINS, cmd_builtin, cmdlet, ps_cmdlet

        if ctx.d == "ps" and raw.lower() not in ctx.funcs and (canon := cmdlet(raw)):
            return ps_cmdlet(canon, args, ctx)
        if ctx.d == "cmd" and raw.lstrip("@").lower() in CMD_BUILTINS:
            return cmd_builtin(raw.lstrip("@").lower(), args, ctx)
    name = exe(raw)
    if name in ctx.funcs:
        # A function's body runs in the caller's context (cwd, variables), not where it
        # was defined, so its effects cannot be placed from the definition alone.
        return top(f"the shell function {name}"), None
    r = TABLE.get(name)
    if r is None:
        # No row: the program is only a `spawn`, which a grant naming it allows and nothing else does.
        return {("spawn", name)}, None
    if stop := gate(name, args, r):
        return stop, None
    return r.model(name, args, ctx)
