"""What each executable does, as a model of its arguments — the half of `guard` that knows programs.

A model maps an argv to effects. Its arguments decide the targets: `rm a b`
deletes `a` and `b`, `curl URL -o F` reaches the URL's host and writes `F`,
`git -C dir commit` writes the repository at `dir`. Options a model does not
know are read as flags, which can only add operands, so an unknown option
over-approximates rather than hides a target. A name with no model is a
`spawn` of that executable.
"""

from __future__ import annotations

from dataclasses import replace
import re

from guard import Word, assignment, code, envs, moved_to, paths, top

KEYWORDS = {
    "posix": {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "{", "}", "!", "time", "for",
              "select", "case", "esac", "function", "coproc"},
    "ps": {"if", "elseif", "else", "switch", "foreach", "for", "while", "do", "until", "try", "catch", "finally",
           "trap", "function", "filter", "param", "begin", "process", "end", "return", "exit", "throw", "break",
           "continue", "data", "dynamicparam", "class", "enum", "using"},
    "cmd": {"else", "not"},
}

READERS = set("""ls cat head tail grep egrep fgrep rg wc cut tr echo printf pwd test [ [[ true false : diff cmp
stat file which type where whereis basename dirname realpath readlink date sleep du df printenv id whoami
hostname uname jq less more nl od xxd hexdump sha1sum sha256sum sha512sum md5sum cksum b2sum base64 column
comm join paste fold fmt rev seq yes expr bc tac strings ps tasklist nproc locale cygpath getconf tput clear
history jobs wait disown bg fg ulimit umask exit return break continue shift getopts let read mapfile readarray
unset shopt hash help man info chcp ver systeminfo ipconfig netstat dir cls true wslpath file tty
nvidia-smi free uptime lsof stty""".split())

TAKES = {
    "mkdir": {"-m", "--mode"}, "touch": {"-d", "-r", "-t", "--date", "--reference"},
    "truncate": {"-s", "--size", "-r", "--reference"}, "chmod": {"--reference"}, "chown": {"--reference"},
    "sort": {"-o", "--output", "-k", "--key", "-t", "--field-separator", "-T", "--temporary-directory", "-S"},
    "uniq": {"-f", "-s", "-w"}, "tree": {"-o", "-L", "-P", "-I", "--charset"}, "iconv": {"-f", "-t", "-o"},
}
COPY_TAKES = {"-t", "--target-directory", "-S", "--suffix", "-m", "--mode", "-o", "-g", "--owner", "--group"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "busybox"}
PS_SHELLS = {"pwsh", "powershell", "powershell_ise"}

def exe(raw: str) -> str:
    """An executable's identity: its file name, lowercased, without the Windows launcher suffix."""
    name = re.split(r"[\\/]", raw)[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|com)$", "", name)


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
    return {("network", host)} if host else set()


# -- Files ---------------------------------------------------------------------

def m_delete(name, args, ctx):
    return writes("delete", getopt(args)[0], ctx), None


def m_create(name, args, ctx):
    pos, vals, _ = getopt(args, TAKES.get(name, frozenset()))
    out = writes("write", pos, ctx)
    for k in ("-o", "--output"):
        out |= writes("write", vals.get(k, []), ctx)
    return out, None


def m_reader_with_output(name, args, ctx):
    pos, vals, _ = getopt(args, TAKES.get(name, frozenset()))
    out = writes("write", vals.get("-o", []) + vals.get("--output", []), ctx)
    if name == "uniq" and len(pos) > 1:
        out |= paths("write", pos[1], ctx)
    return out, None


def basename(text: str) -> str:
    return re.split(r"[\\/]", text.rstrip("/\\"))[-1]


def into_dir(pos: list, dest: list, ctx) -> set:
    """A directory destination writes `<dir>/<source-basename>`, not just the directory.

    Only syntactic directory destinations count: a `-t`/`--target-directory`
    option, a trailing separator, or (for three or more operands) the last one.
    This names the real descendant, so a copy into a protected file's parent is
    seen. A tainted end is already a `top` from the directory write itself.
    """
    dirs = list(dest)
    srcs = list(pos)
    if not dest:
        dirs = [w for w in pos if not w.why and re.search(r"[\\/]$", w.text)]
        if len(pos) >= 3 and not pos[-1].why and pos[-1] not in dirs:
            dirs.append(pos[-1])
        srcs = [w for w in pos if w not in dirs]
    out = set()
    for d in dirs:
        if d.why:
            continue
        for s in srcs:
            if s.why or not s.text:
                continue
            out |= paths("write", Word(d.text.rstrip("/\\") + "/" + basename(s.text)), ctx)
    return out


def m_copy(name, args, ctx):
    pos, vals, flags = getopt(args, COPY_TAKES)
    if name == "ln" and ("-s" in flags or "--symbolic" in flags):
        # A symlink redirects every later path through it; its target is not decidable here.
        return top("ln creates a link the guard cannot follow"), None
    dest = vals.get("-t", []) + vals.get("--target-directory", [])
    out = writes("write", dest or pos[1:] or ([here(ctx)] if name == "ln" else []), ctx)
    out |= into_dir(pos, dest, ctx)
    if name == "mv":
        out |= writes("delete", pos if dest else pos[:-1], ctx)
    return out, None


def m_dd(name, args, ctx):
    return writes("write", [Word(w.text[3:], w.why) for w in args if w.text.startswith("of=")], ctx), None


def m_patch(name, args, ctx):
    pos, vals, _ = getopt(args, {"-d", "--directory", "-o", "--output", "-i", "--input", "-p", "-F", "-B", "-r",
                                 "-D", "-z", "--strip", "--fuzz", "--prefix", "--reject-file", "--suffix"})
    target = vals.get("-d", []) + vals.get("--directory", []) + vals.get("-o", []) + vals.get("--output", [])
    return writes("write", target or pos[:1] or [here(ctx)], ctx), None


def m_mktemp(name, args, ctx):
    pos, vals, _ = getopt(args, {"-p", "--tmpdir", "--suffix"})
    where = vals.get("-p", []) or [w for w in pos if "/" in w.text or "\\" in w.text] or [Word(ctx.temp)]
    return writes("write", where, ctx), None


def m_tar(name, args, ctx):
    if args and not args[0].why and not args[0].text.startswith("-"):
        args = [Word("-" + args[0].text)] + args[1:]
    runs = ("-I", "--use-compress-program", "--to-command", "--checkpoint-action", "--rsh-command",
            "--info-script", "-F", "--new-volume-script")
    pos, vals, flags = getopt(args, {"-f", "--file", "-C", "--directory", "-T", "--files-from", "-X",
                                     "--exclude-from", "-b", "-H", "--format", *runs})
    if any(k in vals for k in runs):
        return top("tar runs a program"), None
    short = "".join(f[1:] for f in flags if not f.startswith("--"))
    if "x" in short or "--extract" in flags or "--get" in flags:
        return writes("write", vals.get("-C", []) + vals.get("--directory", []) or [here(ctx)], ctx), None
    if "t" in short or "--list" in flags:
        return set(), None
    return writes("write", vals.get("-f", []) + vals.get("--file", []), ctx), None


def m_archive(name, args, ctx):
    pos, vals, flags = getopt(args, {"-d", "-x", "-P", "-S", "--suffix", "-b"})
    if name == "unzip":
        if any(f in ("-l", "-p", "-t", "-v", "-Z", "-z") for f in flags):
            return set(), None
        return writes("write", vals.get("-d", []) or [here(ctx)], ctx), None
    if name == "zip":
        return writes("write", pos[:1], ctx), None
    if any(f in ("-c", "--stdout", "-l", "--list", "-t", "--test") for f in flags):
        return set(), None
    return writes("write", pos, ctx) | writes("delete", pos, ctx), None


def m_split(name, args, ctx):
    return writes("write", [here(ctx)] + getopt(args, {"-l", "-b", "-n", "-a", "--lines", "--bytes"})[0][1:2],
                  ctx), None


def m_cd(name, args, ctx):
    if name == "popd":
        return set(), frozenset({None})
    pos = [w for w in args if not w.text.startswith("-") or w.text == "-" or w.why]
    return set(), moved_to(pos[0] if pos else None, ctx)


# -- Text tools whose programs can write or run ------------------------------

SED_RUNS = re.compile(r"(?:^|[;\n{}/$\d!])\s*[wWe](?:\s|$)|s(.).*?(?<!\\)\1.*?(?<!\\)\1[gpiImM0-9]*[we]")
AWK_RUNS = re.compile(r"\bsystem\s*\(|\|\s*getline|\|&|\bprintf?\b[^;{}\n]*[>|]")


def m_sed(name, args, ctx):
    pos, vals, flags = getopt(args, {"-e", "--expression", "-f", "--file", "-l", "--line-length"})
    if "-f" in vals or "--file" in vals:
        return top("sed script file"), None
    scripts = vals.get("-e", []) + vals.get("--expression", []) or pos[:1]
    files = pos if (vals.get("-e") or vals.get("--expression")) else pos[1:]
    out = set()
    for s in scripts:
        if s.why:
            out |= top(f"{s.why} in a sed script")
        elif SED_RUNS.search(s.text):
            out |= top("sed script writes or runs")
    inplace = any(f.startswith("--in-place") or (not f.startswith("--") and "i" in f[1:]) for f in flags)
    return out | (writes("write", files, ctx) if inplace else set()), None


def m_awk(name, args, ctx):
    pos, vals, flags = getopt(args, {"-f", "--file", "-v", "--assign", "-F", "--field-separator", "-i",
                                     "--include", "-e", "--source"})
    if "-f" in vals or "--file" in vals:
        return top("awk program file"), None
    progs = vals.get("-e", []) + vals.get("--source", []) or pos[:1]
    out = set()
    for p in progs:
        if p.why:
            out |= top(f"{p.why} in an awk program")
        elif AWK_RUNS.search(p.text):
            out |= top("awk program writes or runs")
    if any(w.text == "inplace" for w in vals.get("-i", [])):
        out |= writes("write", pos[1:], ctx)
    return out, None


def m_find(name, args, ctx):
    roots, i = [], 0
    while i < len(args) and (args[i].why or not args[i].text.startswith(("-", "(", "!", ")"))):
        roots.append(args[i])
        i += 1
    roots, out = roots or [here(ctx)], set()
    while i < len(args):
        t = args[i].text
        if t == "-delete":
            out |= writes("delete", roots, ctx)
        elif t in ("-exec", "-execdir", "-ok", "-okdir"):
            j, argv = i + 1, []
            while j < len(args) and args[j].text not in (";", "+"):
                argv.append(Word("{}", "variable") if "{}" in args[j].text else args[j])
                j += 1
            out |= run_argv(argv, ctx)[0] if argv else set()
            i = j
        elif t in ("-fprint", "-fprint0", "-fprintf", "-fls") and i + 1 < len(args):
            out |= paths("write", args[i + 1], ctx)
            i += 1
        i += 1
    return out, None


RG_TAKES = {"--pre", "--pre-glob", "--hostname-bin", "-f", "--file", "-g", "--glob", "--iglob", "--type-add",
            "-E", "--encoding", "-M", "--max-columns", "-A", "--after-context", "-B", "--before-context",
            "-C", "--context", "-m", "--max-count", "-d", "--max-depth", "--sort", "--sortr", "-j", "--threads",
            "--color", "--colors", "-r", "--replace", "--ignore-file", "-t", "--type", "-T", "--type-not"}


def m_rg(name, args, ctx):
    """ripgrep reads, except `--pre`/`--hostname-bin`, which launch a program per file."""
    _pos, vals, _flags = getopt(args, RG_TAKES)
    out = set()
    for w in vals.get("--pre", []) + vals.get("--hostname-bin", []):
        out |= run_argv([w], ctx)[0] if w.text else set()
    return out, None


def m_xargs(name, args, ctx):
    pos, vals, flags = getopt(args, {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "--max-args", "--replace",
                                     "--max-procs", "--delimiter", "--arg-file", "--max-lines", "--max-chars"},
                              stop=True)
    argv, unknown = pos or [Word("echo")], Word("{xargs}", "variable")
    rep = (vals.get("-I") or vals.get("--replace") or [None])[0]
    rep = rep.text if rep else ("{}" if any(f.startswith("-i") for f in flags) else "")
    argv = [unknown if w.text and rep and rep in w.text else w for w in argv] if rep else argv + [unknown]
    return run_argv(argv, ctx)[0], None


# -- Programs that run other programs ----------------------------------------

PREFIX = {"env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
          "timeout": {"-s", "--signal", "-k", "--kill-after"}, "nice": {"-n", "--adjustment"},
          "ionice": {"-c", "-n", "-p"}, "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
          "nohup": set(), "time": {"-f", "--format", "-o", "--output"}, "command": set(), "exec": {"-a"}, "builtin": set(), "chronic": set(),
          "winpty": set(), "unbuffer": set(), "caffeinate": set(), "noglob": set()}


def m_prefix(name, args, ctx):
    pos, vals, flags = getopt(args, PREFIX[name], stop=True)
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
    return (out | run_argv(pos, ctx)[0] if pos else out), None


def m_watch(name, args, ctx):
    pos = getopt(args, {"-n", "--interval", "-d", "--differences"}, stop=True)[0]
    if any(w.why for w in pos):
        return top("variable in a watched command"), None
    return code(" ".join(w.text for w in pos), "posix", ctx) if pos else set(), None


def m_trap(name, args, ctx):
    if not args or args[0].text.startswith("-"):
        return set(), None
    return (top("variable in a trap") if args[0].why else code(args[0].text, "posix", ctx)), None


def m_top(name, args, ctx):
    return top(f"{name} runs code the guard cannot read"), None


def nested(w: Word, d: str, ctx) -> set:
    if w.why == "group":
        return set()
    return top(f"{w.why} in a nested shell string") if w.why else code(w.text, d, ctx)


def from_stdin(d: str, ctx, what: str) -> set:
    if ctx.stdin is not None and not ctx.piped:
        return nested(ctx.stdin, d, ctx) if ctx.stdin.quoted or not ctx.stdin.why else top(
            f"{ctx.stdin.why} in a here document a shell runs")
    return top(f"{what} reads its program from stdin")


def m_sh(name, args, ctx):
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} in a shell's arguments"), None
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", t):
            return (nested(args[i + 1], "posix", ctx) if i + 1 < len(args) else top("-c without a string")), None
        if t in ("--rcfile", "--init-file"):
            # The shell runs this file at startup; its contents are not decidable here.
            return top(f"a shell startup file ({t})"), None
        if t in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if t in ("-s", "-"):
            break
        if t.startswith(("-", "+")):
            i += 1
            continue
        return top("a shell script file"), None
    return from_stdin("posix", ctx, name), None


PWSH_VALUES = ("executionpolicy", "windowstyle", "workingdirectory", "outputformat", "inputformat", "version",
               "configurationname", "custompipename", "settingsfile", "psconsolefile")


def joined(rest: list, d: str, ctx) -> set:
    if any(w.why == "group" for w in rest):
        return set()
    if any(w.why for w in rest):
        return top("variable in a nested shell string")
    return code(" ".join(w.text for w in rest), d, ctx)


def m_pwsh(name, args, ctx):
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why == "group":
            return set(), None
        if len(t) > 1 and t[0] in "-/" and not w.why:
            k = t.lstrip("-/").split(":")[0].lower()
            if k in ("e", "ec", "ea") or (k.startswith("en") and "encodedcommand".startswith(k)) \
                    or "encodedarguments".startswith(k) and len(k) > 7:
                return top("an encoded command"), None
            if k and "command".startswith(k):
                rest = args[i + 1:]
                if not rest or [r.text for r in rest] == ["-"]:
                    return from_stdin("ps", ctx, name), None
                return joined(rest, "ps", ctx), None
            if k and "file".startswith(k):
                if args[i + 1:i + 2] and args[i + 1].text == "-":
                    return from_stdin("ps", ctx, name), None
                return top("a PowerShell script file"), None
            full = next((v for v in PWSH_VALUES if v.startswith(k) and len(k) > 1 or k in ("wd", "ep", "ex")), "")
            if full and i + 1 < len(args):
                if k == "wd" or full == "workingdirectory":
                    ctx = replace(ctx, cwds=moved_to(args[i + 1], ctx))
                i += 2
                continue
            i += 1
            continue
        if name == "pwsh":
            return top("a PowerShell script file"), None
        return joined(args[i:], "ps", ctx), None
    return from_stdin("ps", ctx, name), None


def m_cmd(name, args, ctx):
    for i, w in enumerate(args):
        t = w.text.lower()
        if re.fullmatch(r"//?[ck]", t):
            return joined(args[i + 1:], "cmd", ctx), None
        if not t.startswith("/") or w.why:
            return top("cmd without /c"), None
    return from_stdin("cmd", ctx, name), None


# -- Network -------------------------------------------------------------------

CURL_TAKES = {"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--output-dir", "-T", "--upload-file",
              "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-H", "--header", "-X",
              "--request", "-u", "--user", "-A", "--user-agent", "-e", "--referer", "-b", "--cookie", "-F",
              "--form", "--form-string", "-w", "--write-out", "-m", "--max-time", "--connect-timeout", "--retry",
              "--retry-delay", "--retry-max-time", "-x", "--proxy", "--resolve", "--connect-to", "-K", "--config",
              "--url", "-E", "--cert", "--key", "--cacert", "-r", "--range", "--limit-rate", "-z", "--stderr",
              "--trace", "--trace-ascii", "--unix-socket", "--interface", "--max-filesize", "--max-redirs",
              "--noproxy", "--preproxy", "--socks5", "--socks5-hostname", "--oauth2-bearer", "--variable"}


def m_curl(name, args, ctx):
    pos, vals, flags = getopt(args, CURL_TAKES)
    if "-K" in vals or "--config" in vals:
        return top("a curl config file"), None
    out = set()
    for w in pos + [v for k in ("--url", "-x", "--proxy", "--preproxy", "--socks5", "--socks5-hostname")
                    for v in vals.get(k, [])]:
        out |= network(w)
    for k in ("-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--stderr", "--trace",
              "--trace-ascii", "--output-dir"):
        out |= writes("write", [w for w in vals.get(k, []) if w.text != "-"], ctx)
    if any(f in ("--remote-name", "--remote-name-all") or (f[1:2] != "-" and "O" in f) for f in flags):
        out |= paths("write", here(ctx), ctx)
    return out, None


def m_wget(name, args, ctx):
    pos, vals, flags = getopt(args, {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output",
                                     "-P", "--directory-prefix", "-i", "--input-file", "-e", "--execute", "-U",
                                     "--user-agent", "--header", "-T", "--timeout", "-t", "--tries", "-w",
                                     "--user", "--password", "-l", "--level", "--post-data", "--post-file"})
    if "-i" in vals or "--input-file" in vals:
        return top("wget reads its URLs from a file"), None
    out = set()
    for w in pos:
        out |= network(w)
    named = [w for k in ("-O", "--output-document") for w in vals.get(k, [])]
    out |= writes("write", [w for w in named if w.text != "-"], ctx)
    out |= writes("write", [w for k in ("-P", "--directory-prefix", "-o", "--output-file", "-a",
                                        "--append-output") for w in vals.get(k, [])], ctx)
    if not named and "--spider" not in flags:
        out |= paths("write", here(ctx), ctx)
    return out, None


REMOTE = re.compile(r"^(?:[^@/\\:\s]+@)?([^/\\:\s]{2,}|\[[^\]]+\]):")


def m_ssh(name, args, ctx):
    pos, vals, _ = getopt(args, {"-p", "-P", "-i", "-l", "-o", "-F", "-L", "-R", "-D", "-J", "-b", "-c", "-E",
                                 "-e", "-m", "-O", "-Q", "-S", "-W", "-w", "-B", "-I", "-X", "--rsh",
                                 "--rsync-path", "--exclude", "--include", "--filter", "--files-from",
                                 "--exclude-from", "--log-file", "--temp-dir", "--chmod", "--port",
                                 "--password-file", "--timeout", "--bwlimit", "--backup-dir"})
    opts = [w.text.lower() for w in vals.get("-o", [])]
    if "-F" in vals or any(re.search(r"proxycommand|localcommand|knownhostscommand", o) for o in opts) \
            or (name != "ssh" and (vals.get("-S") or vals.get("-e") or vals.get("--rsh") or vals.get("--rsync-path"))):
        return top(f"{name} runs a local program"), None
    out = set()
    for w in vals.get("-J", []):
        out |= {("network", host_of(h)) for h in w.text.split(",")}
    if name in ("ssh", "sftp", "telnet", "nc", "ncat", "netcat", "ftp", "tftp", "mosh"):
        return out | (network(Word(re.sub(r"^[^@]*@", "", pos[0].text), pos[0].why)) if pos else set()), None
    for k, w in enumerate(pos):
        if w.why:
            out |= top(f"{w.why} in a {name} operand")
        elif REMOTE.match(w.text) or "::" in w.text:
            out |= {("network", host_of(re.sub(r"^[^@]*@", "", w.text).split("::")[0] + ":"))}
        elif k == len(pos) - 1 and k:
            out |= paths("write", w, ctx)
    return out, None


def m_lookup(name, args, ctx):
    pos = getopt(args, {"-c", "-n", "-w", "-W", "-i", "-t", "-p", "-q", "-type", "-port", "-l", "-s"})[0]
    out = set()
    for w in pos:
        out |= network(Word(w.text.lstrip("@"), w.why)) if not re.fullmatch(r"[A-Z]+|\d+", w.text) else set()
    return out, None


# -- git and gh ----------------------------------------------------------------

GIT_READ = set("""status log diff show rev-parse rev-list ls-files ls-tree cat-file blame grep describe shortlog
merge-base name-rev for-each-ref show-ref show-branch check-ignore check-attr check-ref-format var version help
count-objects fsck whatchanged cherry range-diff diff-tree diff-files diff-index annotate verify-commit verify-tag
get-tar-commit-id stripspace column check-mailmap""".split())
GIT_WRITE = set("""add commit checkout switch restore reset merge rebase cherry-pick revert rm mv stash am apply tag
branch notes gc prune repack update-ref update-index replace sparse-checkout worktree config remote init clone
format-patch archive bundle clean submodule push fetch pull ls-remote lfs maintenance commit-tree mktree mktag
read-tree write-tree symbolic-ref pack-refs reflog hash-object merge-file merge-tree bisect request-pull""".split())
GIT_TAKES = {"-m", "--message", "-F", "--file", "-C", "-c", "-b", "-B", "--branch", "-o", "--output",
             "--output-directory", "-x", "--exec", "--set-upstream-to", "-X",
             "--strategy-option", "-s", "--strategy", "--depth", "-j", "--jobs", "--repo", "--push-option",
             "--receive-pack", "--upload-pack", "--format", "--pretty", "--author", "--date", "--template",
             "--reference", "--separate-git-dir", "--onto", "--remote", "--origin", "--shallow-since"}
GIT_RUNS = re.compile(r"^alias\.|command|editor|pager|hookspath|askpass|program|helper|textconv|^filter\.|"
                      r"\.cmd$|external|proxy|exec|fsmonitor|gpg|\.tool$|^include|^url\.|^core\.worktree$", re.I)


def git_key(w: Word) -> set:
    if w.why:
        return top(f"{w.why} in a git config value")
    return top("a git config key that runs a program") if GIT_RUNS.search(w.text.split("=")[0]) else set()


def remotes_of(rest: list, ctx, push: bool) -> set:
    pos = getopt(rest, GIT_TAKES)[0]
    named = dict(ctx.remotes)
    if any(f.text in ("--receive-pack", "--upload-pack", "--exec") or f.text.startswith(("--receive-pack=",
           "--upload-pack=", "--exec=")) for f in rest):
        return top("git runs a program on the remote side")
    if not pos:
        return {("network", h) for h in named.values()}
    w, t = pos[0], pos[0].text
    if w.why:
        return top(f"{w.why} in a git remote")
    if t in named:
        return {("network", named[t])}
    if "://" in t and not t.startswith("file:"):
        return network(w)
    if REMOTE.match(t):
        return {("network", host_of(t))}
    if "/" in t or "\\" in t or t.startswith("."):
        return paths("write", w, ctx) if push else set()
    return top(f"git remote {t} is not configured")


def m_git(name, args, ctx):
    out, i, g = set(), 0, ctx
    while i < len(args):
        w, t = args[i], args[i].text
        if t == "-C" and i + 1 < len(args):
            g, i = replace(g, cwds=moved_to(args[i + 1], g)), i + 2
            continue
        if t == "-c" and i + 1 < len(args):
            out, i = out | git_key(args[i + 1]), i + 2
            continue
        if t.startswith(("--config-env=", "--exec-path=")):
            out |= top("git option that runs a program") if t.startswith("--exec-path") else \
                git_key(Word(t.split("=", 1)[1], w.why))
        elif t in ("--git-dir", "--work-tree") and i + 1 < len(args):
            out, i = out | paths("write", args[i + 1], g), i + 1
        elif t.startswith(("--git-dir=", "--work-tree=")):
            out |= paths("write", Word(t.split("=", 1)[1], w.why), g)
        elif not t.startswith("-") or w.why:
            break
        i += 1
    if i >= len(args):
        return out, None
    verb, rest = args[i].text, args[i + 1:]
    if args[i].why:
        return out | top(f"{args[i].why} as the git verb"), None
    if verb in GIT_READ:
        # A read verb still writes through `--output`/`-o` (e.g. `git diff --output=f`).
        vals = getopt(rest, GIT_TAKES)[1]
        out |= writes("write", vals.get("-o", []) + vals.get("--output", [])
                      + vals.get("--output-directory", []), g)
        return out, None
    if verb == "ls-remote":
        return out | remotes_of(rest, g, False), None
    if verb not in GIT_WRITE:
        return out | top(f"git {verb}: an alias or a verb with no model"), None
    out |= paths("write", here(g), g)
    pos, vals, flags = getopt(rest, GIT_TAKES)
    sub = pos[0].text if pos else ""
    if verb in ("push", "fetch", "pull"):
        out |= remotes_of(rest, g, verb == "push")
    elif verb == "clone" and pos:
        out |= network(pos[0]) if ("://" in sub or REMOTE.match(sub)) else set()
        base = re.sub(r"\.git$", "", re.split(r"[/:\\]", sub.rstrip("/"))[-1])
        out |= paths("write", pos[1] if len(pos) > 1 else Word(base, pos[0].why), g)
    elif verb == "init" and pos:
        out |= paths("write", pos[0], g)
    elif verb == "worktree" and len(pos) > 1:
        kind = {"add": "write", "move": "write", "remove": "delete"}.get(sub)
        out |= paths(kind, pos[-1] if sub == "move" else pos[1], g) if kind else set()
    elif verb == "config":
        if "--global" in flags:
            out |= paths("write", Word("~/.gitconfig"), g)
        if "--system" in flags:
            out |= top("git config --system")
        out |= writes("write", vals.get("-f", []) + vals.get("--file", []), g)
        out |= git_key(pos[0]) if len(pos) > 1 or any(f.startswith(("--add", "--replace")) for f in flags) \
            else set()
    elif verb == "submodule":
        out |= {("network", h) for h in dict(g.remotes).values()}
        if sub == "foreach":
            out |= joined(pos[1:], "posix", g)
    elif verb == "rebase":
        for w in vals.get("-x", []) + vals.get("--exec", []):
            out |= nested(w, "posix", g)
    elif verb == "bisect" and sub == "run":
        out |= run_argv(pos[1:], g)[0]
    elif verb in ("format-patch", "archive", "bundle"):
        out |= writes("write", vals.get("-o", []) + vals.get("--output", []) + vals.get("--output-directory", [])
                      + (pos[1:2] if verb == "bundle" else []), g)
    elif verb == "clean" and any(f[1:2] != "-" and ("x" in f or "X" in f) for f in flags):
        out |= paths("delete", here(g), g)
    elif verb == "lfs":
        out |= {("network", h) for h in dict(g.remotes).values()}
    return out, None


GH_KNOWN = set("""auth browse codespace gist issue org pr project release repo cache run workflow api alias config
extension ext gpg-key label ruleset search secret ssh-key status variable attestation completion help version
copilot preview agent-task""".split())
GH_TAKES = {"-R", "--repo", "-H", "--header", "-f", "-F", "--field", "--raw-field", "-X", "--method", "-q", "--jq",
            "-t", "--template", "-b", "--body", "-B", "--base", "--head", "-l", "--label", "-a", "--assignee",
            "-m", "--milestone", "-p", "--project", "-r", "--reviewer", "--input", "--body-file", "--json", "-L",
            "--limit", "-s", "--state", "-D", "--dir", "-O", "--output", "--pattern", "--hostname", "-w",
            "--title", "-T", "--search", "-S", "--author", "-e", "--env", "-c", "--commit", "-n", "--name",
            "--subject", "-A", "--app", "--branch", "--workflow", "--job", "-j", "--event", "--user"}


def m_gh(name, args, ctx):
    pos, vals, flags = getopt(args, GH_TAKES)
    if not pos or pos[0].text in ("help", "version", "completion") or "--version" in flags:
        return set(), None
    verb, sub = pos[0].text, pos[1].text if len(pos) > 1 else ""
    if pos[0].why or verb not in GH_KNOWN or verb in ("extension", "ext") and sub == "exec":
        return top(f"gh {verb}: an extension or a command with no model"), None
    env = dict(a.split("=", 1) for a in ctx.env if "=" in a)
    hosts = [w.text for w in vals.get("--hostname", [])] or [env.get("GH_HOST", "github.com")]
    repo = (vals.get("-R") or vals.get("--repo") or [None])[0]
    if repo is not None and repo.text.count("/") >= 2:
        hosts.append(repo.text.split("/")[0])
    out = {("network", host_of(h)) for h in hosts}
    if verb in ("config", "alias", "extension", "ext") and sub not in ("get", "list", "") \
            or verb == "auth" and sub in ("login", "logout", "setup-git", "refresh", "switch"):
        out |= paths("write", Word("~/.config/gh"), ctx)
    if (verb, sub) in (("repo", "clone"), ("gist", "clone")):
        out |= paths("write", pos[3] if len(pos) > 3 else here(ctx), ctx)
    elif (verb, sub) == ("pr", "checkout"):
        out |= paths("write", here(ctx), ctx)
    elif sub == "download":
        out |= writes("write", vals.get("-D", []) + vals.get("--dir", []) + vals.get("-O", []) + vals.get(
            "--output", []) or [here(ctx)], ctx)
    elif verb == "codespace" and sub in ("cp", "ssh", "code", "jupyter", "ports"):
        out |= top(f"gh codespace {sub}")
    return out, None


def m_export(name, args, ctx):
    return envs([w for w in args if assignment(w)], ctx), None


# -- Dispatch ------------------------------------------------------------------

MODELS = {name: model for model, names in (
    (m_delete, "rm rmdir unlink shred"),
    (m_create, "mkdir touch chmod chown chgrp chattr setfacl attrib icacls takeown truncate tee"),
    (m_reader_with_output, "sort uniq tree iconv"),
    (m_copy, "cp mv ln install"), (m_dd, "dd"), (m_patch, "patch"), (m_mktemp, "mktemp"), (m_tar, "tar"),
    (m_archive, "zip unzip gzip gunzip xz unxz bzip2 bunzip2 zstd unzstd"), (m_split, "split csplit"),
    (m_cd, "cd pushd popd chdir"), (m_export, "export declare local typeset readonly alias set"), (m_sed, "sed"), (m_awk, "awk gawk mawk nawk"), (m_find, "find"),
    (m_xargs, "xargs"), (m_rg, "rg"), (m_prefix, " ".join(PREFIX)), (m_watch, "watch"), (m_trap, "trap"),
    (m_top, "eval source . socat"), (m_sh, " ".join(SHELLS)), (m_pwsh, " ".join(PS_SHELLS)),
    (m_cmd, "cmd"), (m_curl, "curl"), (m_wget, "wget"),
    (m_ssh, "ssh scp sftp rsync telnet nc ncat netcat ftp tftp mosh"),
    (m_lookup, "ping nslookup dig host traceroute tracert whois"), (m_git, "git"), (m_gh, "gh"),
) for name in names.split()}


def run_argv(words: list, ctx) -> tuple[set, object]:
    """Effects of running `words[0]` with the rest as its arguments."""
    if not words:
        return set(), None
    raw, args = words[0].text, words[1:]
    if words[0].why:
        return top(f"{words[0].why} as the command name"), None
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
    if name in READERS and name not in MODELS:
        return set(), None
    model = MODELS.get(name)
    return model(name, args, ctx) if model else ({("spawn", name)}, None)
