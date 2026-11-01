"""PowerShell cmdlets and cmd.exe builtins as models of their arguments, for `guard`.

PowerShell binds parameters by name or position and lets a name be
abbreviated, so `ps_args` reads both; a parameter it does not know may or may
not have taken the next word, and then every positional word counts as a
possible target. Expressions run nothing except the methods they call, which
must be in `SAFE_METHODS`.
"""

from __future__ import annotations

import re

from guard import Word, envs, moved_to, top
from guard_models import PS_SHELLS, SHELLS, exe, here, m_cd, network, run_argv, writes

SAFE_METHODS = set("""trim trimend trimstart replace split substring tostring tolower toupper tolowerinvariant
toupperinvariant contains startswith endswith indexof lastindexof padleft padright join combine getfullpath
getfilename getfilenamewithoutextension getdirectoryname getextension gettemppath exists readalltext readalllines
readallbytes readlines matches match ismatch escape unescape parse tryparse format equals compareto gettype add
addrange insert containskey containsvalue get getvalue getstring getbytes normalize isnullorempty
isnullorwhitespace toarray where foreach sort reverse distinct toint32 toint64 todouble round floor ceiling max min
abs newguid tobase64string frombase64string getenvironmentvariable getcurrentdirectory waitforexit readtoend
readline peek close dispose invoke getenumerator movenext count sum average totalseconds totalmilliseconds
addseconds adddays addhours addminutes tolocaltime touniversaltime getname getnames getvalues isdefined utf8
getencoding fromfiletime haskey test values keys item getfiles getdirectories enumeratefiles enumeratedirectories
getrelativepath ispathrooted haschanged clone copyto indexofany wait result getawaiter getresult""".split())
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
PATH = ("path", "literalpath")
PS_TARGETS = {
    "remove-item": [("delete", PATH, 0)], "set-content": [("write", PATH, 0)], "add-content": [("write", PATH, 0)],
    "clear-content": [("write", PATH, 0)], "out-file": [("write", ("filepath", "literalpath"), 0)],
    "tee-object": [("write", ("filepath", "literalpath"), 0)], "export-csv": [("write", PATH, 0)],
    "export-clixml": [("write", PATH, 0)], "new-item": [("write", ("path", "name"), 0)],
    "set-item": [("write", PATH, 0)], "set-itemproperty": [("write", PATH, 0)],
    "new-itemproperty": [("write", PATH, 0)], "remove-itemproperty": [("write", PATH, 0)],
    "clear-item": [("write", PATH, 0)], "set-acl": [("write", PATH, 0)],
    "copy-item": [("write", ("destination",), 1)],
    "move-item": [("delete", PATH, 0), ("write", ("destination",), 1)],
    "rename-item": [("delete", PATH, 0), ("write", ("newname",), 1)],
    "expand-archive": [("write", ("destinationpath",), 1)], "compress-archive": [("write", ("destinationpath",), 1)],
}
PS_VALUES = ("path", "literalpath", "destination", "destinationpath", "filepath", "value", "encoding", "itemtype",
             "name", "newname", "filter", "include", "exclude", "uri", "outfile", "infile", "method", "body",
             "headers", "contenttype", "argumentlist", "workingdirectory", "credential", "delimiter", "depth",
             "first", "last", "skip", "property", "pattern", "id", "processname", "seconds", "milliseconds",
             "scriptblock", "inputobject", "erroraction", "warningaction", "outvariable", "errorvariable",
             "informationaction", "computername", "hostname", "targetname", "port", "timeoutsec", "typename",
             "comobject", "proxy", "session", "count", "tail", "totalcount", "readcount", "stream", "verb",
             "windowstyle", "redirectstandardoutput", "redirectstandarderror", "redirectstandardinput",
             "maximumredirection", "useragent", "format", "attributes", "jobname", "expandproperty", "groupby",
             "culture", "width", "context")
PS_SWITCHES = ("force", "recurse", "raw", "nonewline", "append", "passthru", "whatif", "confirm", "verbose",
               "debug", "noclobber", "asbytestream", "wait", "nonewwindow", "usebasicparsing", "skipcertificatecheck",
               "file", "directory", "hidden", "readonly", "system", "container", "allmatches", "casesensitive",
               "quiet", "unique", "descending", "notmatch", "list", "asjob", "compress", "asstring", "ashashtable")
PS_READ_PREFIX = ("get-", "test-", "select-", "format-", "convertto-", "convertfrom-", "measure-", "write-",
                  "wait-", "read-", "out-string", "out-null", "out-host", "out-default", "group-", "sort-",
                  "where-", "foreach-", "compare-", "join-", "split-", "resolve-", "start-sleep", "clear-host",
                  "receive-job", "remove-job", "set-variable", "new-variable", "remove-variable", "clear-variable",
                  "set-strictmode", "import-csv", "import-clixml", "import-powershelldatafile", "new-timespan",
                  "new-guid", "add-member", "update-typedata", "update-formatdata", "out-gridview", "show-")


def cmdlet(raw: str) -> str | None:
    """The cmdlet a PowerShell command name runs, or `None` for an external program."""
    if re.search(r"[\\/]|\.\w+$", raw):
        return None
    canon = PS_ALIAS.get(raw.lower(), raw.lower())
    return canon if canon in PS_TARGETS or canon in PS_TOP or re.match(r"^[a-z]+-[a-z]", canon) else None


def ps_args(args: list) -> tuple[dict, list, bool]:
    """`(named, positional, ambiguous)`; an unknown parameter may or may not have taken the next word."""
    named, pos, ambiguous, i = {}, [], False, 0
    while i < len(args):
        w, t = args[i], args[i].text
        if re.match(r"^-[A-Za-z]", t) and not w.quoted and not w.why:
            k, colon, v = t[1:].partition(":")
            k = k.lower()
            full = next((p for p in PS_VALUES if p.startswith(k)), None)
            if colon:
                named.setdefault(full or k, []).append(Word(v, w.why))
            elif full and i + 1 < len(args):
                named.setdefault(full, []).append(args[i + 1])
                i += 1
            elif not any(s.startswith(k) for s in PS_SWITCHES):
                ambiguous = True
            i += 1
            continue
        pos.append(w)
        i += 1
    return named, pos, ambiguous


def ps_cmdlet(canon: str, args: list, ctx):
    named, pos, ambiguous = ps_args(args)

    def one(*keys):
        return next((named[k][0] for k in keys if named.get(k)), None)

    def reach(*keys) -> set:
        out = set()
        for w in [w for k in keys for w in named.get(k, [])] + pos[:1]:
            out |= network(w)
        return out

    if canon in PS_TOP:
        return top(f"{canon} runs code the guard cannot read"), None
    if canon in ("set-location", "push-location"):
        w = one(*PATH) or (pos[0] if pos else None)
        return set(), moved_to(w, ctx) if w is not None else ctx.cwds
    if canon == "pop-location":
        return set(), frozenset({None})
    if canon in PS_TARGETS:
        out = set()
        for kind, keys, index in PS_TARGETS[canon]:
            found = [w for k in keys for w in named.get(k, [])] + (pos if ambiguous else pos[index:index + 1])
            if not found and ctx.piped and (kind == "delete" or index == 0):
                out |= top(f"{canon} takes its path from the pipeline")
            for w in found:
                # `'a','b'` is two paths; the lexer joined them into one word, so the
                # elements cannot be told apart. A comma in a path is left undecided.
                if "," in w.text and not w.why:
                    out |= top(f"{canon} path array")
            out |= writes(kind, found, ctx)
        return out, None
    if canon in ("invoke-webrequest", "invoke-restmethod"):
        return reach("uri", "proxy") | writes("write", named.get("outfile", []), ctx), None
    if canon == "start-process":
        w = one("filepath") or (pos[0] if pos else None)
        out = writes("write", named.get("redirectstandardoutput", []) + named.get("redirectstandarderror", []), ctx)
        if w is None or w.why:
            return out | top("start-process of an unknown program"), None
        name = exe(w.text)
        return out | (top(f"start-process {name}") if name in SHELLS | PS_SHELLS | {"cmd"} else {("spawn", name)}), None
    if canon in ("invoke-command", "start-job", "start-threadjob"):
        if named.get("filepath") or named.get("session"):
            return top(f"{canon} outside this process"), None
        return set().union(*(network(w) for w in named.get("computername", []) + named.get("hostname", []))), None
    if canon == "new-object":
        w = None if named.get("comobject") else one("typename") or (pos[0] if pos else None)
        safe = w is not None and not w.why and SAFE_TYPES.match(w.text.lower())
        return (set() if safe else top(f"new-object {w.text if w else ''}".strip())), None
    if canon in ("test-netconnection", "test-connection", "resolve-dnsname"):
        return reach("computername", "targetname", "name"), None
    if canon.startswith(PS_READ_PREFIX):
        return set(), None
    return {("spawn", canon)}, None


ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "??="}


def ps_statement(words: list, ctx) -> tuple[list, set]:
    """The command a PowerShell statement runs: assignments stripped, expressions running nothing."""
    out = set()
    if len(words) >= 2 and words[1].text in ASSIGN_OPS and not words[1].quoted:
        if words[0].text.lower().startswith("$env:"):
            value = words[2] if len(words) > 2 else Word()
            out |= envs([Word(words[0].text[5:] + "=" + value.text, value.why)], ctx)
        words = words[2:]
    elif words and not words[0].quoted and re.match(r"^\$?[\w:.-]+=", words[0].text):
        target, _, value = words[0].text.partition("=")
        if target.lower().startswith("$env:"):
            out |= envs([Word(target[5:] + "=" + value, words[0].why)], ctx)
        words = ([Word(value, words[0].why)] if value else []) + words[1:]
    if words and (words[0].text.startswith(("$", "[")) or words[0].quoted or re.match(r"^-?\d", words[0].text)):
        return [], out
    return words, out


CMD_BUILTINS = {"del", "erase", "rd", "rmdir", "md", "mkdir", "copy", "xcopy", "robocopy", "move", "ren", "rename",
                "cd", "chdir", "pushd", "popd", "set", "start", "if", "for", "call", "type", "echo", "dir",
                "setlocal", "endlocal", "title", "ver", "vol", "cls", "pause", "exit", "rem", "break", "color",
                "prompt", "where", "mklink"}


def cmd_builtin(name: str, args: list, ctx):
    ops = [w for w in args if w.why or not w.text.startswith("/")]
    if name in ("del", "erase", "rd", "rmdir"):
        return writes("delete", ops, ctx), None
    if name in ("md", "mkdir", "mklink"):
        return writes("write", ops[:1] if name == "mklink" else ops, ctx), None
    if name in ("copy", "xcopy", "robocopy"):
        dest = ops[1:2] if name == "robocopy" else ops[-1:] if len(ops) > 1 else [here(ctx)]
        return writes("write", dest, ctx), None
    if name in ("move", "ren", "rename"):
        return writes("delete", ops[:1], ctx) | writes("write", ops[1:2], ctx), None
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
        return run_argv(rest[skip:], ctx)[0] if rest[skip:] else set(), None
    if name == "for":
        at = next((k for k, w in enumerate(args) if w.text.lower() == "do"), len(args))
        return run_argv(args[at + 1:], ctx)[0] if args[at + 1:] else set(), None
    if name == "call":
        return top("call runs a batch file"), None
    return set(), None
