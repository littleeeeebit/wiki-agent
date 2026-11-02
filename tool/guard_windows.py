"""PowerShell cmdlets and cmd.exe builtins: the Windows half of the closed table, for `guard`.

`PS_ROWS` lists each modelled cmdlet with the parameters it understands, by
full name or a listed alias (no abbreviation is guessed); a parameter outside
the row is `top`, and a cmdlet with no row is a `spawn` only a grant naming it
allows. `CMD_ROWS` does the same for cmd.exe's builtins and their switches.
Expressions run nothing except the members they invoke, judged by
`guard.member_effect`: a name is accepted receiver-free only when it is in
`SAFE_MEMBERS`, read-only for every type that defines it.
"""

from __future__ import annotations

import re

import posixpath

from guard import BLOCK, Word, envs, member_effect, moved_to, states, top
from guard_models import PS_SHELLS, SHELLS, exe, here, into_dir, m_cd, network, run_argv, spawn_of, writes

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
                        r"text\.regularexpressions\.regex|management\.automation\.[\w.]+)$")

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
    "where-object": "property= value= filterscript= inputobject= eq ne gt ge lt le like notlike match notmatch "
                    "contains notcontains in notin is isnot not ceq cne cgt cge clt cle clike cnotlike cmatch "
                    "cnotmatch ccontains cnotcontains cin cnotin",
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
    "measure-command": "expression= inputobject=",
    "start-sleep": "seconds= milliseconds= duration=",
    "get-filehash": "path= literalpath= algorithm= inputstream=",
    "compare-object": "referenceobject= differenceobject= property= includeequal excludedifferent passthru "
                      "syncwindow= casesensitive",
    "new-timespan": "start= end= days= hours= minutes= seconds= milliseconds=",
    "set-variable": "name= value= scope= option= passthru description= visibility= force",
    "get-variable": "name= scope= valueonly include= exclude=",
    "foreach-object": "",               # its own binder, `foreach_object`
    # Writers and the location.
    "remove-item": "path= literalpath= recurse stream= " + FILTERS,
    "set-content": "path= literalpath= value= encoding= nonewline passthru stream= asbytestream " + FILTERS,
    "add-content": "path= literalpath= value= encoding= nonewline passthru stream= asbytestream " + FILTERS,
    "clear-content": "path= literalpath= stream= " + FILTERS,
    "out-file": "filepath= literalpath= encoding= append force noclobber nonewline width= inputobject=",
    "tee-object": "filepath= literalpath= append variable= inputobject= encoding=",
    "export-csv": "path= literalpath= notypeinformation encoding= append force delimiter= noclobber inputobject= "
                  "usequotes=",
    "new-item": "path= name= itemtype= value= force",
    "copy-item": "path= literalpath= destination= recurse container passthru " + FILTERS,
    "move-item": "path= literalpath= destination= passthru " + FILTERS,
    "rename-item": "path= literalpath= newname= passthru force",
    "set-location": "path= literalpath= stackname= passthru",
    "push-location": "path= literalpath= stackname= passthru",
    "pop-location": "stackname= passthru",
    "invoke-webrequest": "uri= method= headers= body= contenttype= outfile= usebasicparsing timeoutsec= "
                         "maximumredirection= useragent= skiphttperrorcheck infile=",
    "invoke-restmethod": "uri= method= headers= body= contenttype= outfile= usebasicparsing timeoutsec= "
                         "maximumredirection= useragent= skiphttperrorcheck infile=",
    "start-process": "filepath= argumentlist= workingdirectory= wait nonewwindow passthru redirectstandardoutput= "
                     "redirectstandarderror= redirectstandardinput= windowstyle=",
    "new-object": "typename= argumentlist= property= strict",
}
# Where the cmdlet's first positional word binds, for the writers above.
PS_TARGETS = {"remove-item": "delete", "set-content": "write", "add-content": "write", "clear-content": "write",
              "out-file": "write", "tee-object": "write", "export-csv": "write"}


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
        return {("spawn", canon)}, None
    if canon == "foreach-object":
        return foreach_object(args), None
    named, pos, unknown = ps_args(args, PS_ROWS[canon])
    if unknown:
        return top(f"{canon} {unknown}: a parameter the table does not model"), None

    def given(*keys) -> list:
        return [w for k in keys for w in named.get(k, [])]

    def arrayed(ws) -> set:
        # `'a','b'` is two paths; the lexer joined them into one word, so the elements
        # cannot be told apart. A comma in a path is left undecided.
        return top(f"{canon} path array") if any("," in w.text and not w.why for w in ws) else set()

    if canon in ("set-location", "push-location"):
        w = (given(*PATH) + pos[:1] or [None])[0]
        if w is not None:
            return set(), moved_to(w, ctx)
        # With no path it moves to the piped item, or (PowerShell 7's `Set-Location`) to the home folder.
        return set(), frozenset({None}) if ctx.piped else states(ctx.cwds | {ctx.home})
    if canon == "pop-location":
        return set(), frozenset({None})
    if canon in ("copy-item", "move-item"):
        srcs = given(*PATH) or pos[:1]
        # Copy-Item/Move-Item default the destination to the current directory when it is omitted.
        target = given("destination") or pos[1:2] or [here(ctx)]
        # A move renames entries as they are, links included: what lands is a link holder.
        kind = "linktree" if canon == "move-item" else "write_tree" if "recurse" in named else "write"
        out = arrayed(srcs + target) | writes(kind, target, ctx) | into_dir(srcs, target, ctx, kind)
        return out | (writes("delete", srcs, ctx) if canon == "move-item" else set()), None
    if canon == "new-item":
        itemtype = (given("itemtype") or [Word("file")])[0]
        if itemtype.why or itemtype.text.lower() not in ("file", "directory"):
            # A symbolic link, junction or hard link redirects later paths: not in the row.
            return top(f"new-item -ItemType {itemtype.text}"), None
        where, leaf = given("path") or pos[:1], given("name")
        if where and leaf:
            # `-Path dir -Name leaf` creates `dir/leaf`.
            where = [Word(posixpath.join(where[0].text.replace("\\", "/"), leaf[0].text), where[0].why or leaf[0].why)]
        target, kind = where or leaf, "dir" if itemtype.text.lower() == "directory" else "write"
        return arrayed(target) | writes(kind, target, ctx) | (top("new-item with no path") if not target else set()), \
            None
    if canon == "rename-item":
        # `-NewName` is a name beside the item, not a path from the current location.
        items, out = given(*PATH) + pos[:1], set()
        for item in items:
            for new in given("newname") + pos[1:2]:
                beside = posixpath.join(posixpath.dirname(item.text.replace("\\", "/")), new.text)
                out |= arrayed([item]) | writes("linktree", [Word(beside, item.why or new.why)], ctx)
        return (out or top("rename-item with a target it cannot place")) | writes("delete", items, ctx), None
    if canon == "where-object":
        return where_member(args), None
    if canon in PS_TARGETS:
        found = given(*PATH, "filepath") + pos[:1]
        if not found and ctx.piped:
            return top(f"{canon} takes its path from the pipeline"), None
        return arrayed(found) | writes(PS_TARGETS[canon], found, ctx), None
    if canon in ("invoke-webrequest", "invoke-restmethod"):
        out = set().union(*(network(w) for w in given("uri") + pos[:1]))
        # Redirects are followed unless `-MaximumRedirection 0`: the host reached would not be this one.
        if [w.text for w in given("maximumredirection")] != ["0"]:
            out |= top(f"{canon} follows redirects")
        return out | writes("write", given("outfile"), ctx), None
    if canon == "start-process":
        w = (given("filepath") + pos[:1] or [None])[0]
        out = writes("write", given("redirectstandardoutput", "redirectstandarderror"), ctx)
        if w is None or w.why:
            return out | top("start-process of an unknown program"), None
        name = exe(w.text)
        return out | (top(f"start-process {name}") if name in SHELLS | PS_SHELLS | {"cmd"}
                      else spawn_of(w.text, ctx)), None
    if canon == "new-object":
        w = (given("typename") + pos[:1] or [None])[0]
        safe = w is not None and not w.why and SAFE_TYPES.match(w.text.lower())
        return (set() if safe else top(f"new-object {w.text if w else ''}".strip())), None
    return set(), None


ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "??="}

# ForEach-Object's parameters, the common ones included, and the aliases PowerShell binds.
FOREACH_PARAMS = """begin process end remainingscripts parallel membername argumentlist inputobject throttlelimit
timeoutseconds asjob usenewrunspace confirm whatif verbose debug erroraction warningaction informationaction
errorvariable warningvariable informationvariable outvariable outbuffer pipelinevariable progressaction""".split()
FOREACH_ALIASES = {"args": "argumentlist", "ea": "erroraction", "wa": "warningaction", "infa": "informationaction",
                   "ev": "errorvariable", "wv": "warningvariable", "iv": "informationvariable", "ov": "outvariable",
                   "ob": "outbuffer", "pv": "pipelinevariable", "proga": "progressaction", "cf": "confirm",
                   "wi": "whatif", "vb": "verbose", "db": "debug"}
FOREACH_BLOCKS = {"begin", "process", "end", "remainingscripts", "parallel"}
FOREACH_SWITCHES = {"asjob", "usenewrunspace", "confirm", "whatif", "verbose", "debug"}


def foreach_param(key: str) -> str | None:
    """The ForEach-Object parameter `-key` binds to, as PowerShell resolves a name, alias or unique prefix."""
    if key in FOREACH_ALIASES or key in FOREACH_PARAMS:
        return FOREACH_ALIASES.get(key, key)
    hits = [p for p in FOREACH_PARAMS if p.startswith(key)]
    return hits[0] if key and len(hits) == 1 else None


def foreach_object(args: list) -> set:
    """ForEach-Object read the way PowerShell binds it, and `top` wherever that reading is not certain.

    Two argument lists are explained. Script blocks, positional or under -Begin/-Process/-End/
    -RemainingScripts/-Parallel, are groups already reduced where they stand. A literal member name
    (`-MemberName X`, `-MemberName:X` or the first positional word) with literal arguments invokes
    that member on every piped object, so it goes through the member gate. Anything else — another
    parameter, a parenthesised value, a variable — can change what is invoked: undecidable.
    """
    def block(w: Word) -> bool:
        return w.why == "group" and w.text == BLOCK

    member, blocks, i = None, False, 0
    while i < len(args):
        w, t = args[i], args[i].text
        i += 1
        if not w.why and re.match(r"^-[A-Za-z]", w.raw or t):
            key, colon, value = t[1:].partition(":")
            p = foreach_param(key.lower())
            if p in FOREACH_SWITCHES:
                continue
            if p not in FOREACH_BLOCKS | {"membername", "argumentlist", "throttlelimit", "timeoutseconds"}:
                return top(f"foreach-object parameter -{key}")
            if colon and value:
                v = Word(value)
            elif i < len(args):
                v, i = args[i], i + 1
            else:
                return top(f"foreach-object -{key} without a value")
            if p in FOREACH_BLOCKS:
                if not block(v):
                    return top(f"foreach-object -{key} given something other than a script block")
                blocks = True
            elif v.why:
                return top(f"{v.why} in foreach-object -{key}")
            elif p == "membername":
                if member is not None:
                    return top("foreach-object given two member names")
                member = v
            elif p != "argumentlist" and not v.text.isdigit():
                return top(f"foreach-object -{key} {v.text}")
            continue
        if block(w):
            blocks = True
        elif w.why:
            return top(f"{w.why} in foreach-object")
        elif member is None and not blocks:
            member = w                  # the first positional word names the member
        elif member is None:
            return top("foreach-object mixes script blocks and a member name")
        # Later literal positional words are the member's arguments.
    if member is None:
        return set()
    if blocks:
        return top("foreach-object mixes script blocks and a member name")
    # The member name here is the operand, not a receiver literal, so the receiver stays unknown.
    return top("foreach-object wildcard member name") if re.search(r"[*?\[]", member.text) \
        else member_effect("", member.text, False)


def where_member(args: list) -> set:
    """Where-Object given a property name rather than a script block.

    A script-block argument is a group, reduced where it appears. The first positional word names
    the property read on every pipeline object; it goes through the member gate (a name not known
    to read only is undecidable), and so does a wildcard name.
    """
    if any(w.why == "group" for w in args):
        return set()
    for w in args:
        if w.why:
            return top(f"{w.why} in where-object")
        if not w.text.startswith("-"):
            return top("where-object wildcard member name") if re.search(r"[*?\[]", w.text) \
                else member_effect("", w.text, False)
    return set()


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
    elif words and not words[0].quoted and re.match(r"^\$?[\w:.-]+=", words[0].text):
        target, _, value = words[0].text.partition("=")
        if "." in target or "[" in target:
            return [], member_assign
        if target.lower().startswith("$env:"):
            out |= envs([Word(target[5:] + "=" + value, words[0].why)], ctx)
        words = ([Word(value, words[0].why)] if value else []) + words[1:]
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


# cmd.exe builtin: the switches it understands (`*` = any; the read-only ones ignore theirs).
CMD_ROWS = {"del": "q f p a", "erase": "q f p a", "rd": "s q", "rmdir": "s q", "md": "", "mkdir": "",
            "copy": "y -y b a v", "move": "y -y", "ren": "", "rename": "", "cd": "d", "chdir": "d", "pushd": "",
            "popd": "", "set": "p", "if": "i", "for": "d l",
            "start": "b wait min max i low normal high belownormal abovenormal",
            **{n: "*" for n in ("type", "echo", "dir", "setlocal", "endlocal", "title", "ver", "vol", "cls", "pause",
                                "exit", "rem", "break", "color", "prompt", "where")}}
# Builtins in no row: a link, a tree copy whose file set the switches decide, or a batch file.
CMD_REFUSED = {"mklink": "creates a link", "xcopy": "copies a tree its switches select",
               "robocopy": "copies a tree its switches select", "call": "runs a batch file"}
CMD_BUILTINS = set(CMD_ROWS) | set(CMD_REFUSED)


def cmd_builtin(name: str, args: list, ctx):
    if name in CMD_REFUSED:
        return top(f"{name} {CMD_REFUSED[name]}"), None
    allowed = CMD_ROWS[name].split()
    for w in args:
        switch = w.text[1:].lower().partition(":")[0]
        if not w.why and w.text.startswith("/") and "*" not in allowed and switch not in allowed:
            # `/s` on `del`, `/a` on `set`, `/f` on `for`: what they reach is not modelled.
            return top(f"{name} {w.text}: a switch the table does not model"), None
    ops = [w for w in args if w.why or not w.text.startswith("/")]
    if name in ("del", "erase", "rd", "rmdir"):
        return writes("delete", ops, ctx), None
    if name in ("md", "mkdir"):
        return writes("dir", ops, ctx), None
    if name == "copy":
        srcs, target = ops[:-1], ops[-1:] if len(ops) > 1 else [here(ctx)]
        return writes("write", target, ctx) | into_dir(srcs, target, ctx), None
    if name in ("move", "ren", "rename"):
        # A rename moves entries as they are, links included; `ren`'s new name sits beside the source.
        target = ops[1:2]
        if name != "move" and target and ops[:1]:
            src = ops[0].text.replace("\\", "/")
            target = [Word(posixpath.join(posixpath.dirname(src), target[0].text), ops[0].why or target[0].why)]
        return writes("delete", ops[:1], ctx) | writes("linktree", target, ctx) | into_dir(
            ops[:1], target, ctx, "linktree"), None
    if name in ("cd", "chdir", "pushd", "popd"):
        return m_cd(name, ops, ctx)
    if name == "set":
        return envs([w for w in ops if "=" in w.text], ctx), None
    if name == "start":
        rest = ops[1:] if ops and ops[0].quoted else ops
        return run_argv(rest, ctx)[0] if rest else set(), None
    if name == "if":
        rest = [w for w in ops if w.text.lower() != "not"]
        skip = 2 if rest and rest[0].text.lower() in ("exist", "defined", "errorlevel", "cmdextversion") else \
            3 if len(rest) > 2 and rest[1].text.lower() in ("==", "equ", "neq", "lss", "leq", "gtr", "geq") else 1
        if not rest[skip:]:
            return set(), None
        # The branch may or may not run: where it moves joins where the shell already is.
        eff, moved = run_argv(rest[skip:], ctx)
        return eff, None if moved is None else states(ctx.cwds | moved)
    if name == "for":
        at = next((k for k, w in enumerate(args) if w.text.lower() == "do"), len(args))
        if not args[at + 1:]:
            return set(), None
        eff, moved = run_argv(args[at + 1:], ctx)
        return eff | (top("a directory change inside a loop, function or group") if moved is not None else set()), \
            None
    return set(), None
