"""What each program does: a closed table of modelled programs, each with the flags it understands.

`TABLE` is all the guard knows about programs, and it is kept small: programs
that only read or print, `sort -o`/`uniq` writing their one output, `cd`,
`find` without actions, nested shells, and git's `status`, `diff`, `log`,
`show`, `rev-parse`, `add`, `commit` and `push` to a configured remote. A row
names a program, the flags it understands (`row`'s spec: one-letter flags,
flags taking a value, long flags) and the model that maps its argv to effects.
`gate` runs before every model: a flag the row does not list is `top`, and so
is a word computed at run time where a flag may stand, unless no option of the
program writes, runs or connects (`inert`). Some rows exist only to refuse
(`refuse`). A program with no row is a `spawn` of that executable, which only
a grant naming it allows. A denied ordinary command is answered by adding a
row in a later change, never by modelling one more form inside this one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import posixpath
import re

from guard import BLOCK, Word, code, moved_to, names_set, paths, resolve, top

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


def exe(raw: str, windows: bool) -> str:
    """A bare program's identity as the host looks it up. On Windows that is its name lowercased, and
    `.exe` is how it spells the same program; any other suffix (`.cmd`, `.bat`, `.com`) names a different
    file. A POSIX lookup is exact: `CAT` and `cat.exe` are other files than `cat`."""
    name = re.split(r"[\\/]", raw)[-1]
    if not windows:
        return name
    name = name.lower()
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


def m_output(name, args, ctx):
    """A reader that writes only where `-o`/`--output` points, or (`uniq`) to its second operand."""
    pos, vals, _ = parse(name, args)
    out = writes("write", vals.get("-o", []) + vals.get("--output", []), ctx)
    return out | (paths("write", pos[1], ctx) if name == "uniq" and len(pos) > 1 else set()), None


def m_cd(name, args, ctx):
    pos = parse(name, args)[0]
    return set(), moved_to(pos[0] if pos else None, ctx)


FIND_TESTS = set("""-print -print0 -ls -prune -quit -empty -true -false -readable -writable -executable -depth
-mount -xdev -follow -daystart -nouser -nogroup -noleaf -not -a -and -o -or ! ( ) ,""".split())
FIND_VALUES = set("""-name -iname -path -ipath -wholename -iwholename -regex -iregex -regextype -type -xtype
-maxdepth -mindepth -newer -anewer -cnewer -mtime -mmin -atime -amin -ctime -cmin -size -perm -user -group -uid
-gid -links -inum -samefile -lname -ilname -printf -used -fstype""".split())


def m_find(name, args, ctx):
    """find without actions: every test is listed; `-delete`, `-exec`, `-fprint`… are not, so they are `top`."""
    i = 0
    while i < len(args) and not args[i].why and args[i].text in ("-L", "-H", "-P"):
        i += 1
    roots = []
    while i < len(args) and (args[i].why or not args[i].text.startswith(("-", "(", "!", ")", ","))):
        if args[i].why and not literal_start(args[i]):
            return top(f"{args[i].why} where a find primary may stand"), None
        roots.append(args[i])
        i += 1
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} in a find expression"), None
        if t in FIND_VALUES or re.fullmatch(r"-newer[aBcmt][aBcmt]", t):
            i += 1
        elif t not in FIND_TESTS:
            return top(f"find {t}: a primary the table does not model"), None
        i += 1
    return set(), None


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


# -- git -----------------------------------------------------------------------

# git's own options before the verb that take no value. `-C`, `-c`, `--git-dir`, `--work-tree` and `--exec-path`
# change which repository, configuration or program runs: they are not here, so they are `top`.
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
           "--cc --full-history --simplify-by-decoration --notes --no-notes")
# verb: (the flags its row understands, what its model does). A verb with no row is `top`.
GIT_VERBS = {
    "status": ("-sbvz -u? --short --branch --porcelain --long --untracked-files --ignored --ahead-behind "
               "--no-ahead-behind --show-stash --column --no-column --renames --no-renames --verbose", "read"),
    "diff": (GIT_LOG, "read"), "log": (GIT_LOG, "read"), "show": (GIT_LOG, "read"),
    "rev-parse": ("-q --verify --quiet --short --abbrev-ref --symbolic --symbolic-full-name --show-toplevel "
                  "--show-prefix --show-cdup --git-dir --git-common-dir --absolute-git-dir --is-inside-work-tree "
                  "--is-inside-git-dir --is-bare-repository --is-shallow-repository --show-object-format --all "
                  "--branches --tags --remotes --not --default: --since: --until: --path-format: --git-path: --sq "
                  "--local-env-vars --show-superproject-working-tree --revs-only --no-revs --flags --no-flags", "read"),
    # Writes of the repository it runs in, and the push of its branch to a configured remote.
    "add": ("-Auv --all --update --verbose", "repo"),
    "commit": ("-qa -m: -F: --quiet --all --message: --file:", "repo"),
    "push": ("-uq --set-upstream --quiet", "push"),
}
GIT = {verb: (row(None, spec, digits=True), kind) for verb, (spec, kind) in GIT_VERBS.items()}


def same_path(a: str, b: str, ctx) -> bool:
    """One place by the host's rule: Windows paths ignore case, POSIX paths do not."""
    a, b = a.rstrip("/"), b.rstrip("/")
    return a.lower() == b.lower() if ctx.windows else a == b


def repo_roots(ctx) -> set | None:
    """The work tree each place `ctx` may run in belongs to: the nearest folder holding `.git`, or the
    checkout the grant was read for when no nearer one exists on disk. `None` when a place is unknown."""
    roots = set()
    for cwd in ctx.cwds:
        if cwd is None:
            return None
        p, found = cwd.rstrip("/") or "/", None
        while found is None:
            if ctx.repo and same_path(p, ctx.repo, ctx) or os.path.exists(p + "/.git"):
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
    return bool(ctx.repo) and roots is not None and all(same_path(r, ctx.repo, ctx) for r in roots)


def pushed(pos: list, ctx) -> set:
    """`git push [REMOTE [BRANCH]]`: every host the grant read for that remote (each `url` and `pushurl`),
    or for every destination when none is named. A URL, a path, a refspec with `+`, `:` or a revision
    suffix (a forced push, a deletion, another ref) is not in the row."""
    if not own_repo(ctx):
        return top("git push from a repository the grant has not read")
    if len(pos) > 2 or any(w.why for w in pos) or pos and pos[0].text not in {n for n, _ in ctx.remotes} \
            or len(pos) == 2 and re.search(r"[+:^~@{}]", pos[1].text) or not ctx.remotes:
        return top("git push to anything but a configured remote and a branch")
    hosts = {h for n, h in ctx.remotes if not pos or n == pos[0].text}
    if "\0unread" in hosts:
        return top("git push to a local path, a rewritten URL or configuration syntax the guard does not decode")
    return {("network", h) for h in hosts}


PRETTY = {"oneline", "short", "medium", "full", "fuller", "reference", "email", "mboxrd", "raw"}


def formats(args: list) -> set:
    """A `--format`/`--pretty` value is a built-in format or a literal template. A `%G` field verifies the
    signature, which runs the configured verifier; any other name is an alias the config defines."""
    for k, w in enumerate(args):
        m = re.match(r"--(?:format|pretty)(=?)(.*)", w.text, re.S)
        if not m:
            continue
        value = m.group(2) if m.group(1) else (args[k + 1].text if k + 1 < len(args) else "")
        if "%G" in value:
            return top("a git format field that verifies signatures")
        if "%" not in value and value.removeprefix("format:").removeprefix("tformat:") == value \
                and value not in PRETTY:
            return top("a git format alias the configuration defines")
    return set()


def m_git(name, args, ctx):
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} as a git option or verb"), None
        if t not in GIT_GLOBAL:
            if t.startswith("-"):
                return top(f"git {t}: an option the table does not model"), None
            break
        i += 1
    if i >= len(args):
        return set(), None
    verb, rest = args[i].text, args[i + 1:]
    if verb not in GIT:
        return top(f"git {verb}: a verb the table does not model"), None
    r, kind = GIT[verb]
    if stop := gate(f"git {verb}", rest, r):
        return stop, None
    # What git starts from its configuration (hooks, a text converter, a verifier) is trusted only because
    # that configuration is protected: in another repository it may be one the agent wrote.
    if not own_repo(ctx):
        return top("git in a repository other than the checkout"), None
    if kind == "read":
        return formats(rest), None
    # git's write of the repository it runs in is the granted git verb; `outside` judges its place only.
    out = paths("repo", here(ctx), ctx)
    return out | (pushed(getopt(rest, r.takes)[0], ctx) if kind == "push" else set()), None


def subscripted(ws: list) -> set:
    """A variable name with a subscript (`a[i]`) has bash evaluate the subscript as arithmetic."""
    if any(w.why and w.why != "group" or "[" in w.text for w in ws):
        return top("arithmetic evaluation in a variable subscript")
    return set()


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
    # The working directory, honoured only in the first statement (`guard.run`).
    "cd": row(m_cd, "-LP", inert=True),
    "find": row(m_find, free=True),
    # Nested shells: the string they run is parsed and judged like the call itself.
    **{s: row(m_sh, free=True) for s in SHELLS},
    **{s: row(m_pwsh, free=True) for s in PS_SHELLS},
    "cmd": row(m_cmd, free=True),
    "git": row(m_git, free=True),
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
        return {("spawn", exe(raw, ctx.windows))}
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


def run_argv(words: list, ctx) -> tuple[set, object]:
    """Effects of running `words[0]` with the rest as its arguments, and where a `cd` moves to."""
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

        if ctx.d == "ps" and (canon := cmdlet(raw)):
            return ps_cmdlet(canon, args, ctx)
        if ctx.d == "cmd" and raw.lstrip("@").lower() in CMD_BUILTINS:
            return cmd_builtin(raw.lstrip("@").lower(), args, ctx)
    name = exe(raw, ctx.windows)
    r = TABLE.get(name)
    if r is None:
        # No row: the program is only a `spawn`, which a grant naming it allows and nothing else does.
        return {("spawn", name)}, None
    if stop := gate(name, args, r):
        return stop, None
    return r.model(name, args, ctx)
