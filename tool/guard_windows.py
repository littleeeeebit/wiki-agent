"""PowerShell cmdlets and cmd.exe builtins: the Windows half of the closed table, for `guard`.

`PS_ROWS` lists each modelled cmdlet with the parameters it understands, by
full name or a listed alias (no abbreviation is guessed): cmdlets that only
read or print, `Set-Location`, and writers of one literal path each
(`ps_writes`). A parameter outside the row is `top`, and so is a cmdlet with
no row. `CMD_ROWS` lists the cmd.exe builtins that only read or print and
`CMD_WRITES` those that write, delete, copy or move literal paths; every other
builtin is `top`.
Expressions run nothing except the members they invoke, judged by
`guard.member_effect`: a name is accepted receiver-free only when it is in
`SAFE_MEMBERS`, read-only for every type that defines it.
"""

from __future__ import annotations

import re

from dataclasses import replace

from guard import Word, envs, moved_to, paths, top

# Members that only read, whatever the receiver's type. Polymorphic or dispatching names
# (replace, foreach, where, invoke, add, insert, get, getvalue, item, delete, copyto, …) are
# left out on purpose: on an unknown receiver they reduce to `top` (see `guard.member_effect`),
# since the same name means a write on one type and a read on another.
SAFE_MEMBERS = set("""trim trimend trimstart split substring tostring tolower toupper tolowerinvariant
toupperinvariant contains startswith endswith indexof lastindexof padleft padright join combine getfullpath
getfilename getfilenamewithoutextension getdirectoryname getextension gettemppath exists readalltext readalllines
readallbytes readlines matches match ismatch escape unescape parse tryparse format equals compareto gettype
containskey containsvalue getstring getbytes normalize isnullorempty
isnullorwhitespace toarray sort reverse distinct toint32 toint64 todouble round floor ceiling max min
abs newguid tobase64string frombase64string getenvironmentvariable getcurrentdirectory
count sum average totalseconds totalmilliseconds
addseconds adddays addhours addminutes tolocaltime touniversaltime getname getnames getvalues isdefined utf8
getencoding fromfiletime haskey test values keys getfiles getdirectories enumeratefiles enumeratedirectories
getrelativepath ispathrooted haschanged indexofany length name fullname directoryname basename extension""".split())
SAFE_TYPES = re.compile(r"^(system\.)?(text\.stringbuilder|collections\.[\w.`\[\]]+|object|psobject|pscustomobject|"
                        r"diagnostics\.stopwatch|random|datetime|timespan|version|uri|guid|text\.utf8encoding|"
                        r"text\.regularexpressions\.regex)$")

PS_ALIAS = {
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem", "cat": "get-content", "gc": "get-content",
    "type": "get-content", "rm": "remove-item", "del": "remove-item", "ri": "remove-item", "rmdir": "remove-item",
    "rd": "remove-item", "erase": "remove-item", "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item",
    "mv": "move-item", "move": "move-item", "mi": "move-item", "ren": "rename-item", "rni": "rename-item",
    "mkdir": "new-item", "md": "new-item", "ni": "new-item", "echo": "write-output", "write": "write-output",
    "cd": "set-location", "sl": "set-location", "chdir": "set-location", "pushd": "push-location",
    "popd": "pop-location", "iwr": "invoke-webrequest", "curl": "invoke-webrequest", "wget": "invoke-webrequest",
    "irm": "invoke-restmethod", "iex": "invoke-expression", "ac": "add-content",
    "clc": "clear-content", "start": "start-process", "saps": "start-process", "kill": "stop-process",
    "spps": "stop-process", "%": "foreach-object", "foreach": "foreach-object", "?": "where-object",
    "where": "where-object", "sls": "select-string", "select": "select-object", "sort": "sort-object",
    "measure": "measure-object", "tee": "tee-object", "sleep": "start-sleep", "icm": "invoke-command",
    "sajb": "start-job", "pwd": "get-location", "gl": "get-location", "gi": "get-item", "gp": "get-itemproperty",
    "ft": "format-table", "fl": "format-list", "gm": "get-member", "cls": "clear-host", "h": "get-history",
    "history": "get-history", "r": "invoke-history", "ipmo": "import-module", "nal": "new-alias",
    "sal": "set-alias", "sv": "set-variable", "set": "set-variable", "ii": "invoke-item", "group": "group-object",
    "compare": "compare-object", "diff": "compare-object", "gcm": "get-command", "gal": "get-alias",
    "gps": "get-process", "ps": "get-process", "gsv": "get-service", "gjb": "get-job", "rcjb": "receive-job",
    "wjb": "wait-job", "rjb": "remove-job", "epcsv": "export-csv", "ipcsv": "import-csv", "rvpa": "resolve-path",
}
PS_TOP = {"invoke-expression", "add-type", "import-module", "set-executionpolicy", "invoke-history", "new-alias",
          "set-alias", "register-scheduledjob", "register-scheduledtask", "new-service", "set-service",
          "enable-psremoting", "enter-pssession", "new-pssession", "invoke-item", "start-transcript"}
PS_REFUSED = {"expand-archive": "extracts an archive into names the archive decides",
              "sc": "is Set-Content in Windows PowerShell but runs sc.exe in PowerShell 7"}
# Aliases PowerShell 7 drops off Windows, where the name runs the native program of that name; `mkdir` is a
# function only on Windows. `md` stays an alias of `mkdir` everywhere, so off Windows it runs the native one.
POSIX_NAMES = set("ac cat clear compare cp cpp diff kill ls man mount mv ps rm rmdir sleep sort start tee write "
                  "mkdir".split())
PATH = ("path", "literalpath")
# Common parameters every cmdlet takes, and the aliases PowerShell binds for them and for `-LiteralPath`.
COMMON = ("erroraction= warningaction= informationaction= errorvariable= warningvariable= informationvariable= "
          "outvariable= outbuffer= pipelinevariable= progressaction= verbose debug whatif confirm")
PS_PARAM_ALIASES = {"ea": "erroraction", "wa": "warningaction", "infa": "informationaction", "ev": "errorvariable",
                    "wv": "warningvariable", "iv": "informationvariable", "ov": "outvariable", "ob": "outbuffer",
                    "pv": "pipelinevariable", "proga": "progressaction", "vb": "verbose", "db": "debug",
                    "wi": "whatif", "cf": "confirm", "lp": "literalpath", "pspath": "literalpath"}
FILTERS = "filter= include= exclude= force"
DASHES = "[-–—―]"
# cmdlet: the parameters it understands (`name=` takes a value, `name` is a switch). No row: a `spawn`.
PS_ROWS = {
    "get-content": "path= literalpath= totalcount= head= first= tail= last= raw encoding= delimiter= readcount= "
                   "wait stream= asbytestream " + FILTERS,
    "get-childitem": "path= literalpath= recurse depth= name directory file hidden readonly system attributes= "
                     "followsymlink " + FILTERS,
    "get-item": "path= literalpath= stream= " + FILTERS,
    "get-itemproperty": "path= literalpath= name= " + FILTERS,
    "test-path": "path= literalpath= pathtype= isvalid newerthan= olderthan= " + FILTERS,
    "resolve-path": "path= literalpath= relative relativebasepath=",
    "split-path": "path= literalpath= parent leaf leafbase extension qualifier noqualifier isabsolute resolve",
    "join-path": "path= childpath= additionalchildpath= resolve",
    "get-location": "stack stackname=",
    "select-object": "property= expandproperty= excludeproperty= first= last= skip= skiplast= unique index= wait "
                     "inputobject=",
    "sort-object": "property= descending unique casesensitive culture= top= bottom= stable inputobject=",
    "group-object": "property= noelement ashashtable asstring casesensitive culture= inputobject=",
    "measure-object": "property= sum average maximum minimum line word character ignorewhitespace allstats "
                      "inputobject=",
    "format-list": "property= groupby= view= force expand= inputobject=",
    "format-table": "property= autosize wrap groupby= hidetableheaders view= force expand= inputobject=",
    "out-string": "stream width= nonewline inputobject=",
    "out-null": "inputobject=", "out-host": "paging inputobject=", "out-default": "inputobject=",
    "write-output": "inputobject= noenumerate",
    "write-host": "object= nonewline separator= foregroundcolor= backgroundcolor=",
    "write-error": "message= category= errorid= targetobject=",
    "write-warning": "message=", "write-verbose": "message=", "write-debug": "message=",
    "write-information": "messagedata= tags=",
    "convertfrom-json": "inputobject= ashashtable depth= noenumerate",
    "convertto-json": "inputobject= depth= compress asarray enumsasstrings escapehandling=",
    "get-command": "name= commandtype= module= all syntax",
    "get-date": "date= format= uformat= year= month= day= hour= minute= second= millisecond= displayhint= asutc",
    "get-process": "name= id= includeusername",
    "get-member": "membertype= name= force static inputobject=",
    "select-string": "pattern= path= literalpath= simplematch casesensitive context= notmatch allmatches list quiet "
                     "raw encoding= include= exclude= inputobject= noemphasis",
    "start-sleep": "seconds= milliseconds= duration=",
    "get-filehash": "path= literalpath= algorithm= inputstream=",
    "compare-object": "referenceobject= differenceobject= property= includeequal excludedifferent passthru "
                      "syncwindow= casesensitive",
    "new-timespan": "start= end= days= hours= minutes= seconds= milliseconds=",
    "get-variable": "name= scope= valueonly include= exclude=",
    # The location, honoured only in the first statement (`guard.run`).
    "set-location": "path= literalpath=",
    # Rows that write, delete, copy or move one literal path each (`ps_writes`).
    "set-content": "path= literalpath= value= encoding= nonewline force passthru asbytestream",
    "add-content": "path= literalpath= value= encoding= nonewline force passthru asbytestream",
    "clear-content": "path= literalpath= force",
    "out-file": "filepath= literalpath= inputobject= encoding= append force noclobber width= nonewline",
    "tee-object": "filepath= literalpath= inputobject= append encoding=",
    "new-item": "path= name= itemtype= value= force",
    "remove-item": "path= literalpath= recurse force",
    "copy-item": "path= literalpath= destination= force container passthru",
    "move-item": "path= literalpath= destination= force passthru",
    "rename-item": "path= literalpath= newname= force passthru",
}
FILE = ("filepath", "literalpath")
# The writing rows' parameter slots in the order positional words bind to them.
PS_POSITIONS = {
    "set-content": (PATH, ("value",)), "add-content": (PATH, ("value",)), "clear-content": (PATH,),
    "out-file": (FILE, ("encoding",)), "tee-object": (FILE,), "new-item": (("path",),), "remove-item": (PATH,),
    "copy-item": (PATH, ("destination",)), "move-item": (PATH, ("destination",)),
    "rename-item": (PATH, ("newname",)),
}


def cmdlet(raw: str, windows: bool = True) -> str | None:
    """The cmdlet a PowerShell command name runs, or `None` for an external program."""
    if re.search(r"[\\/]|\.\w+$", raw) or not windows and raw.lower() in POSIX_NAMES:
        return None
    if not windows and raw.lower() == "md":
        return "md"                     # no row: `top`, since it runs a program under another name
    canon = PS_ALIAS.get(raw.lower(), raw.lower())
    return canon if canon in PS_ROWS or canon in PS_TOP or canon in PS_REFUSED \
        or re.match(r"^[a-z]+-[a-z]", canon) else None


def ps_args(args: list, spec: str) -> tuple[dict, list, str]:
    """`(named, positional, unknown)`: parameters bound by their full name or a listed alias.

    A switch is recorded as `[Word("true")]`. `unknown` is the first parameter the row does not
    list ("" when there is none): PowerShell may bind it anywhere, so the cmdlet is `top`.
    """
    params = (spec + " " + COMMON).split()
    values = {p[:-1] for p in params if p.endswith("=")}
    names = values | {p for p in params if not p.endswith("=")}
    named, pos, i = {}, [], 0
    while i < len(args):
        w, t = args[i], args[i].text
        i += 1
        # A named parameter is one whose own `-Name` was written unquoted; quoting only its
        # value (`-LiteralPath:'../x'`) does not turn the word into a positional path. PowerShell
        # takes an en dash, em dash or horizontal bar for the hyphen too.
        if re.match(f"^{DASHES}[A-Za-z]", w.raw or t) and not w.why:
            k, colon, v = t[1:].partition(":")
            k = PS_PARAM_ALIASES.get(k.lower(), k.lower())
            if k not in names:
                return named, pos, t
            if colon:
                named.setdefault(k, []).append(Word(v, w.why))
            elif k in values and i < len(args):
                named.setdefault(k, []).append(args[i])
                i += 1
            else:
                named.setdefault(k, []).append(Word("true"))
            continue
        pos.append(w)
    return named, pos, ""


def ps_cmdlet(canon: str, args: list, ctx):
    if canon in PS_TOP:
        return top(f"{canon} runs code the guard cannot read"), None
    if canon in PS_REFUSED:
        return top(f"{canon} {PS_REFUSED[canon]}"), None
    if canon not in PS_ROWS:
        # A cmdlet runs in this process, not as an executable a grant could name: outside the table is `top`.
        return top(f"{canon}: a cmdlet the table does not model"), None
    named, pos, unknown = ps_args(args, PS_ROWS[canon])
    if unknown:
        return top(f"{canon} {unknown}: a parameter the table does not model"), None
    if canon == "set-location":
        given = [w for k in PATH for w in named.get(k, [])] + pos
        if len(given) != 1:
            return top("set-location without exactly one path"), None
        return set(), moved_to(ps_path(given[0]), ctx)
    if canon in PS_POSITIONS:
        return ps_writes(canon, named, pos, ctx), None
    return set(), None


def ps_path(w: Word) -> Word:
    """`w` as PowerShell's file-system provider reads it: a backslash is a separator on every host. Only paths
    PowerShell itself opens (its cmdlets, redirections and location) are read so; a native program's are not."""
    return replace(w, text=w.text.replace("\\", "/"))


def literal_path(w: Word, ctx, cmd: bool = False) -> Word | None:
    """`w` when it names one file-system path literally, `None` when PowerShell or cmd may read it otherwise:
    a wildcard, a list, a script block bound per input object, or a provider or stream (`Env:X`, `f:s`)."""
    t = w.text[4:] if w.text[:4] in ("\\\\?\\", "//?/") else w.text
    if w.why or not t or re.search(r"[*?,]" if cmd else r"[*?\[\],{}]", t) or ":" in t[2 if t[1:2] == ":" else 0:]:
        return None
    return w


def under(folder: Word, name: str) -> Word:
    return Word(folder.text.rstrip("\\/") + "/" + name, folder.why)


def ps_writes(canon: str, named: dict, pos: list, ctx) -> set:
    """The paths a writing row names, bound as PowerShell binds them; every one literal, or `top`."""
    order = PS_POSITIONS[canon]
    open_ = [s for s in order if not any(k in named for k in s)]
    if len(pos) > len(open_):
        return top(f"{canon}: a positional argument the table does not bind")
    slot = {s[0]: [ps_path(w) for k in s for w in named.get(k, [])] for s in order}
    for s, w in zip(open_, pos):
        slot[s[0]] = [ps_path(w)]
    if any(len(v) > 1 for v in slot.values()):
        return top(f"{canon}: a parameter given twice")
    one = {k: v[0] for k, v in slot.items() if v}
    if canon == "new-item":
        # Its other parameters bind from piped objects by property name: a link type or target included.
        kind = named.get("itemtype", [Word("file")])[0]
        if ctx.piped or kind.why or kind.text.lower() not in ("file", "directory") or len(named.get("itemtype", [])) > 1:
            return top("new-item that may make a link or bind its parameters from the pipeline")
        name = [ps_path(w) for w in named.get("name", [])]
        where = one.get("path", Word("."))
        target = under(where, name[0].text) if name else where if "path" in one else None
        if len(name) > 1 or target is None or name and name[0].why:
            return top("new-item without exactly one path")
        one = {"path": target}
    first, rest = order[0][0], order[-1][0]
    if first not in one or rest in ("destination", "newname") and rest not in one:
        return top(f"{canon} without a literal path for every place it writes")
    src = literal_path(one[first], ctx)
    if src is None:
        return top(f"{canon}: a path PowerShell reads as a wildcard, list, provider or stream")
    if canon == "remove-item":
        return paths("delete", src, ctx)
    if canon in ("copy-item", "move-item", "rename-item"):
        return moves(src, one[rest], canon != "copy-item", canon == "rename-item", ctx)
    return paths("write", src, ctx)


def moves(src: Word, dest: Word, move: bool, rename: bool, ctx, cmd: bool = False) -> set:
    """A copy or move of `src`: a write of `dest` and of `dest/<name of src>` (where it lands in a folder),
    and for a move a delete of `src`. A rename names only the new name, beside `src`."""
    base = re.split(r"[\\/]+", src.text.rstrip("\\/"))[-1]
    if literal_path(dest, ctx, cmd) is None or base in ("", ".", "..") or re.fullmatch(r"[A-Za-z]:", base):
        return top("a copy or move whose source or destination is not one literal path")
    if "~" in base:
        # A short (8.3) name: the copy lands under the long name (`GIT~1` is `.git`), which the text does not show.
        return top("a copy or move of a short name, which lands under the long one")
    if rename:
        if re.search(r"[\\/]", dest.text):
            return top("a new name that is a path")
        parent = src.text.rstrip("\\/")[:-len(base)] or "."
        targets = [under(Word(parent, src.why), dest.text)]
    else:
        targets = [dest, under(dest, base)]
    out = paths("delete", src, ctx) if move else set()
    for w in targets:
        out |= paths("write", w, ctx)
    return out


ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "??="}


def ps_statement(words: list, ctx) -> tuple[list, set]:
    """The command a PowerShell statement runs: assignments stripped, expressions running nothing."""
    out = set()
    member_assign = out | top("assignment to a member or indexed target")
    if len(words) >= 2 and words[1].text in ASSIGN_OPS and not words[1].quoted:
        target = words[0].text
        if target.lower().startswith("$env:"):
            value = words[2] if len(words) > 2 else Word()
            out |= envs([Word(target[5:] + "=" + value.text, value.why)], ctx)
            words = words[2:]
        elif re.fullmatch(r"\$[A-Za-z_]\w*", target) and not words[0].quoted:
            words = words[2:]           # a plain scalar variable: the value runs as its own command
        else:
            # `$x.Prop = …`, `$x[0] = …`: a write through a member or element, not a scalar.
            return [], member_assign
    # `$env:X='v'` is one word, quoted only after the `=`: a word is a string only when it opens with a quote
    # (a here-string, which keeps no source, is quoted throughout).
    elif words and not (re.match(r"['\"]", words[0].raw) if words[0].raw else words[0].quoted) \
            and (m := re.match(r"^(\$?[\w:.-]+?)(\+|-|\*|/|%|\?\?)?=", words[0].text)):
        target, value = m.group(1), words[0].text[m.end():]
        source = (words[0].raw or words[0].text).partition("=")[2]
        # A compound operator (`+=`) combines with a value known only at run time.
        why = words[0].why if re.search(r"[$(`]", source) else "a value combined at run time" if m.group(2) else ""
        if "." in target or "[" in target:
            return [], member_assign
        if target.lower().startswith("$env:"):
            out |= envs([Word(target[5:] + "=" + value, why)], ctx)
        # A quoted value is a string; a bare one runs as its own command.
        bare = value and source[:1] not in ("'", '"')
        words = ([Word(value, why)] if bare else []) + words[1:]
    else:
        # `(expr).Member = …` spans several words before the operator.
        for j in range(1, len(words)):
            if words[j].text in ASSIGN_OPS and not words[j].quoted:
                return [], member_assign
            if words[j].why or not re.fullmatch(r"[.\w:$\[\]()'\"-]*", words[j].text):
                break
    # An expression statement: a variable, a type, a literal, or a unary operator (`-not (...)`, `!$x`).
    if words and (words[0].text.startswith(("$", "[", "-", "!")) or words[0].quoted or re.match(r"^\d", words[0].text)):
        return [], out
    return words, out


# cmd.exe builtins that only read or print, whatever their switches; `cd` is the location (`guard.run`).
CMD_ROWS = {"cd", "chdir", "type", "echo", "dir", "ver", "vol", "cls", "exit", "rem", "where"}
# Builtins that write, delete, copy or move the literal paths they name, with the switches they understand.
# `del /s` (every subfolder) is not listed.
CMD_WRITES = {"del": "/f /q", "erase": "/f /q", "rd": "/s /q", "rmdir": "/s /q", "md": "", "mkdir": "",
              "move": "/y /-y", "ren": "", "rename": ""}
# Every other builtin links or sets the environment. `copy` of a source an earlier statement turned into a
# folder writes that folder's files under names the command does not show. `start` runs a program in a new
# process: it is not a builtin here, so it is a `spawn` like any program with no row.
CMD_REFUSED = {"pushd", "popd", "set", "setlocal", "endlocal", "if", "for", "mklink", "xcopy", "robocopy",
               "title", "color", "prompt", "pause", "break", "copy"}
CMD_BUILTINS = CMD_ROWS | set(CMD_WRITES) | CMD_REFUSED | {"call"}


def cmd_builtin(name: str, args: list, ctx):
    if name == "call":
        # `call` runs a builtin in this interpreter (`call cd ..` moves it, `call set` changes its
        # environment): what it calls is judged as itself. A batch file or program it runs is a spawn.
        from guard_models import run_argv
        return run_argv(args, ctx)
    if name in CMD_REFUSED:
        return top(f"{name}: a cmd builtin the table does not model"), None
    if name in ("cd", "chdir"):
        ops = [w for w in args if w.why or not w.text.startswith("/")]
        if len(ops) != 1 or len(args) > 2 or any(w.text.lower() not in ("/d",) for w in args if w not in ops):
            return top(f"{name} without exactly one path"), None
        return set(), moved_to(ops[0], ctx)
    if name in CMD_WRITES:
        return cmd_writes(name, args, ctx), None
    return set(), None


def cmd_writes(name: str, args: list, ctx) -> set:
    ops = [w for w in args if w.why or not w.text.startswith("/")]
    if any(w.text.lower() not in CMD_WRITES[name].split() for w in args if w not in ops):
        return top(f"{name}: a switch the table does not model")
    if not ops or any(literal_path(w, ctx, cmd=True) is None or "+" in w.text for w in ops):
        return top(f"{name}: a path cmd reads as a wildcard, list, concatenation or stream")
    if name in ("del", "erase", "rd", "rmdir", "md", "mkdir"):
        kind = "write" if name in ("md", "mkdir") else "delete"
        return {e for w in ops for e in paths(kind, w, ctx)}
    if len(ops) != 2:
        return top(f"{name} without exactly a source and a destination")
    return moves(ops[0], ops[1], True, name in ("ren", "rename"), ctx, cmd=True)
