"""PowerShell cmdlets and cmd.exe builtins: the Windows half of the closed table, for `guard`.

`PS_ROWS` lists each modelled cmdlet with the parameters it understands, by
full name or a listed alias (no abbreviation is guessed): cmdlets that only
read or print, and `Set-Location`. A parameter outside the row is `top`, and
so is a cmdlet with no row. `CMD_ROWS` lists the cmd.exe builtins that only
read or print; every other builtin is `top`.
Expressions run nothing except the members they invoke, judged by
`guard.member_effect`: a name is accepted receiver-free only when it is in
`SAFE_MEMBERS`, read-only for every type that defines it.
"""

from __future__ import annotations

import re

from guard import Word, envs, moved_to, top

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
    "irm": "invoke-restmethod", "iex": "invoke-expression", "sc": "set-content", "ac": "add-content",
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
PS_REFUSED = {"expand-archive": "extracts an archive into names the archive decides"}
PATH = ("path", "literalpath")
# Common parameters every cmdlet takes, and the aliases PowerShell binds for them and for `-LiteralPath`.
COMMON = ("erroraction= warningaction= informationaction= errorvariable= warningvariable= informationvariable= "
          "outvariable= outbuffer= pipelinevariable= progressaction= verbose debug whatif confirm")
PS_PARAM_ALIASES = {"ea": "erroraction", "wa": "warningaction", "infa": "informationaction", "ev": "errorvariable",
                    "wv": "warningvariable", "iv": "informationvariable", "ov": "outvariable", "ob": "outbuffer",
                    "pv": "pipelinevariable", "proga": "progressaction", "vb": "verbose", "db": "debug",
                    "wi": "whatif", "cf": "confirm", "lp": "literalpath", "pspath": "literalpath"}
FILTERS = "filter= include= exclude= force"
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
}


def cmdlet(raw: str) -> str | None:
    """The cmdlet a PowerShell command name runs, or `None` for an external program."""
    if re.search(r"[\\/]|\.\w+$", raw):
        return None
    canon = PS_ALIAS.get(raw.lower(), raw.lower())
    return canon if canon in PS_ROWS or canon in PS_TOP or re.match(r"^[a-z]+-[a-z]", canon) else None


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
        # value (`-LiteralPath:'../x'`) does not turn the word into a positional path.
        if (w.raw or t)[:1] == "-" and re.match(r"^-[A-Za-z]", w.raw or t) and not w.why:
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
        return set(), moved_to(given[0], ctx)
    return set(), None


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
            and re.match(r"^\$?[\w:.-]+=", words[0].text):
        target, _, value = words[0].text.partition("=")
        source = (words[0].raw or words[0].text).partition("=")[2]
        why = words[0].why if re.search(r"[$(`]", source) else ""
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
# Every other builtin writes, links, copies, runs a command or a batch file, or sets the environment.
CMD_REFUSED = {"del", "erase", "rd", "rmdir", "md", "mkdir", "copy", "move", "ren", "rename", "pushd", "popd", "set",
               "setlocal", "endlocal", "if", "for", "start", "mklink", "xcopy", "robocopy", "call", "title", "color",
               "prompt", "pause", "break"}
CMD_BUILTINS = CMD_ROWS | CMD_REFUSED


def cmd_builtin(name: str, args: list, ctx):
    if name in CMD_REFUSED:
        return top(f"{name}: a cmd builtin the table does not model"), None
    if name in ("cd", "chdir"):
        ops = [w for w in args if w.why or not w.text.startswith("/")]
        if len(ops) != 1 or len(args) > 2 or any(w.text.lower() not in ("/d",) for w in args if w not in ops):
            return top(f"{name} without exactly one path"), None
        return set(), moved_to(ops[0], ctx)
    return set(), None
