"""PreToolUse hook — block rewriting a file git tracks.

Only a tracked file has a diff to lose. A log, a generated file, an ignored
path or memory is written however is convenient. `sed -i` is judged here too:
one substitution over three or more files is the batch Edit cannot do, and
passes; on fewer it is a rewrite like any other.

The refusal and the `systemMessage` are read by the person and stay Korean.
Everything else here is written for whoever maintains it.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# The shell and python shapes that write to a file. There is no end to what a
# command can look like, so this does not try to catch them all — these four
# are what one session actually produced 45 times, and the page's prose holds
# whatever they miss.
WRITERS = (
    # `cat > path`, `cat >> path`, `tee path`
    re.compile(r"\b(?:cat|tee)\s+(?:-a\s+)?>>?\s*(?P<path>\"[^\"]+\"|'[^']+'|[\w./$~-]+)"),
    re.compile(r"\btee\s+(?:-a\s+)?(?P<path>\"[^\"]+\"|'[^']+'|[\w./$~-]+)"),
    # Redirection. `2>&1` and `>/dev/null` are filtered out below.
    re.compile(r"(?<![0-9&>])>>?\s*(?P<path>\"[^\"]+\"|'[^']+'|[\w./$~-]+\.[A-Za-z0-9]+)"),
    # Python opening a path to write. A heredoc body arrives inside `command`
    # exactly as written, which is why scanning the command text reaches it.
    re.compile(r"Path\(\s*(?P<path>\"[^\"]+\"|'[^']+')\s*\)\s*\.\s*write_"),
    re.compile(r"open\(\s*(?P<path>\"[^\"]+\"|'[^']+')\s*,\s*[\"'][wa]"),
)

# The shape that puts the path in a variable and writes through it later. The
# two patterns above only match when `Path(...)` and `.write_` sit together,
# and one session did more than ten multi-point replacements this way without
# the hook firing once. Following a single variable closes that gap.
#
# One assignment is followed, and no more. Beyond that needs a parser, and a
# script complex enough to need one is the thing this rule exists to stop.
#
# An assignment starts a line or follows a `;`. The first version looked only
# at the start of a line and missed the exact shape this rule aims at — the
# whole thing written as one line, and the same inside a heredoc.
#
#     python -c "import pathlib; p = pathlib.Path('README.md'); p.write_text('x')"
#
# ponytail: one literal assignment, no further. `for n in [...]: p = Path(n)`
# passes because the argument is a variable. Past that boundary is a parser,
# and hand-writing a script that needs one is what this rule talks people out
# of in the first place.
ASSIGNED_PATH = re.compile(
    r"(?:^|;)\s*(?P<name>\w+)\s*=\s*(?:[\w.]*\bPath|open)\(\s*"
    r"(?P<path>\"[^\"]+\"|'[^']+')",
    re.MULTILINE,
)
# Writing through that variable. Reading (`read_text`) is deliberately absent:
# blocking a read is a false positive, and a false positive that stops the
# work is how the person ends up turning the hook off entirely.
WRITES_THROUGH = (
    r"\.\s*write_\w*\s*\(",
    r"\.\s*open\(\s*[\"'][wa]",
    r"\.\s*writelines\s*\(",
    r"\.\s*write\s*\(",
)

# Outside the repository, or somewhere a diff would mean nothing.
EXEMPT = re.compile(
    r"/dev/null|\$TMPDIR|\$\{TMPDIR|/tmp/|[Tt]emp[/\\]|scratchpad|\.git/|node_modules"
)

REASON = (
    "이미 있는 파일은 Edit 으로 고친다. 통째로 되쓰지 마라.\n"
    "되쓰려던 파일: {paths}\n"
    "\n"
    "왜 막는가. 한 줄을 고치려고 파일 전체를 다시 쓰면 무엇이 바뀌었는지가 diff 에\n"
    "안 남고, 인코딩과 줄끝이 조용히 뒤집힌다. 그리고 대상 텍스트가 안 맞을 때\n"
    "Edit 은 소리 내며 실패하지만 스크립트는 아무것도 안 바꾸고 조용히 성공한다.\n"
    "\n"
    "대신 이렇게. 바꿀 텍스트와 바꿀 내용을 짝지어 Edit 을 호출하라. 여러 자리면\n"
    "Edit 을 여러 번 호출한다. 새 파일이면 Write 를 쓴다. 이 훅은 git 이 추적하는\n"
    "파일만 본다 — 로그·생성 파일·무시된 파일·스크래치는 셸로 써도 되고, 같은 치환을\n"
    "세 파일 이상에 하는 `sed -i` 한 번은 통과한다."
)
BATCH = 3
FOLDERS = 8


def _unquote(raw: str) -> str:
    return raw[1:-1] if raw[:1] in {'"', "'"} and raw[-1:] == raw[:1] else raw


# The receivers that take a heredoc body as *code*. A body going anywhere else
# is data, and is dropped before the scan.
INTERPRETERS = re.compile(r"\b(?:python[23]?|node|deno|bash|sh|zsh|perl|ruby)\b")
# `<<<` is a here-string, not an opener. The delimiter is a shell word — `'EOF-TEXT'`, `EOF-TEXT`,
# `\TXT`, `E"O"F` — and closes on that word with its quoting taken off (`delimiter`).
HEREDOC = re.compile(r"(?<!<)<<(?P<dash>-?)(?!<)[ \t]*"
                     r"(?P<word>(?:'[^'\n]*'|\"[^\"\n]*\"|\\.|[^\s;&|<>()'\"\\])+)")
UNQUOTE = re.compile(r"'([^']*)'|\"([^\"]*)\"|\\(.)")


def delimiter(opener: re.Match) -> str:
    return UNQUOTE.sub(lambda m: next(g for g in m.groups() if g is not None), opener["word"])


def strip_data_heredocs(command: str) -> str:
    """Drop the body of a heredoc whose receiver is not an interpreter.

    This is the hook's own false positive, caught the first time it was used
    in anger. A commit message was being passed with `git commit -F - <<'MSG'`
    and that message quoted the sentence `cat > docs/README.md`; the hook read
    the string and blocked the commit. A review instruction, a PR body or a
    document quoting a command is normal and common.

    What decides it is what the body becomes. The body of `python - <<'PY'` is
    code that will run and has to be read; the body of `git commit -F - <<'MSG'`
    is data and must not be. Redirection on the command line itself sits
    outside the body either way, so `cat > file <<'PY'` is still caught.
    """
    return _data_heredocs(command, lambda body: "")


def blank_data_heredocs(command: str) -> str:
    """`strip_data_heredocs` that keeps every offset: the body's characters become spaces, its newlines stay."""
    return _data_heredocs(command, lambda body: re.sub(r"[^\n]", " ", body))


def _data_heredocs(command: str, put) -> str:
    """Each data body line through `put`; the opener's line, code bodies and the rest stay.

    A body starts on the line after its opener — the rest of that line is still a command —
    and the bodies of `<<A <<B` follow in that order. A code body is read as commands, so a
    heredoc opened inside it nests; a data body's text opens nothing. A body that never
    closes is left in: judge nothing rather than hide a command."""

    lines = command.split("\n")
    hidden, _ = _bodies(lines)
    return "\n".join(put(line) if n in hidden else line for n, line in enumerate(lines))


def unclosed_heredoc(command: str) -> bool:
    """An opener whose body never closes: a body that cannot be placed, possibly data."""
    return bool(_bodies(command.split("\n"))[1])


def _bodies(lines: list[str]) -> tuple[set, list]:
    """The data body line numbers, and the openers still open at the end."""
    # (delimiter, `<<-`, is code, first body line), innermost last
    stack: list[tuple[str, bool, bool, int]] = []
    hidden = set()
    for n, line in enumerate(lines):
        # As Bash closes it: the whole line is the delimiter; `<<-` strips leading tabs only.
        bare = line.rstrip("\r")
        if stack and (bare.lstrip("\t") if stack[-1][1] else bare) == stack[-1][0]:
            delim, dash, code, first = stack.pop()
            if not code:
                hidden.update(range(first, n + 1))
            if stack and stack[-1][3] <= n:  # the next body of the same line starts after this one
                stack[-1] = (*stack[-1][:3], n + 1)
            continue
        if stack and not stack[-1][2]:
            continue
        openers = [(delimiter(m), bool(m["dash"]), bool(INTERPRETERS.search(line[:m.start()])))
                   for m in HEREDOC.finditer(line)]
        stack += [(*opener, n + 1) for opener in reversed(openers)]
    return hidden, stack


def _through_variables(command: str) -> list[str]:
    """Paths taken with `p = Path("...")` and written through `p.write_text(...)`."""

    found: list[str] = []
    for match in ASSIGNED_PATH.finditer(command):
        name = match.group("name")
        path = _unquote(match.group("path"))
        if not path:
            continue
        written = any(
            re.search(rf"\b{re.escape(name)}\s*{tail}", command)
            for tail in WRITES_THROUGH
        )
        if written and path not in found:
            found.append(path)
    return found


def targets(command: str) -> list[str]:
    """Of the paths this command would write, the ones inside the repository."""
    found: list[str] = []
    command = strip_data_heredocs(command)
    for pattern in WRITERS:
        for match in pattern.finditer(command):
            path = _unquote(match.group("path"))
            if EXEMPT.search(path) or not path:
                continue
            if path not in found:
                found.append(path)
    for path in _through_variables(command):
        if EXEMPT.search(path) or path in found:
            continue
        found.append(path)
    return found


def in_place(command: str, root: Path) -> list[str]:
    """Files a `sed -i` stage rewrites, leaving out a batch of `BATCH` or more (globs counted in `root`).

    ponytail: direct `sed` stages only; `xargs sed -i` and `find -exec sed -i` take their files
    from elsewhere and are batches by shape, and pass."""

    from pytest_output import _blank

    # A continued line is one stage; `F=path ... sed -i ... "$F"` and a `cd` before it name the
    # file as surely as a literal does. ponytail: the last plain assignment of a name wins.
    # A data heredoc's body is text, not commands, as for `targets`.
    command = blank_data_heredocs(command.replace("\\\n", "  "))
    names = dict(re.findall(r"(?:^|[\s;&(])([A-Za-z_]\w*)=(\"[^\"$]*\"|'[^']*'|[^\s;&|$\"'`]+)", command))
    names = {k: v.strip("\"'") for k, v in names.items()}
    found, start, heres = [], 0, [root]  # every folder the next command can be running in
    for mark in [*re.finditer(r"&&|\|\||;|\n|\|", _blank(command)), None]:
        stage, start = command[start:mark.start() if mark else len(command)], mark.end() if mark else 0
        try:
            words = [re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: names.get(m[1], m[0]), w) for w in shlex.split(stage)]
        except ValueError:
            continue
        while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
            words.pop(0)
        if words[:1] == ["cd"] and len(words) > 1:
            target = re.sub(r"^/([A-Za-z])/", r"\1:/", words[1])  # Git Bash's `/c/` is `C:/`
            moved = [h / target for h in heres if (h / target).is_dir()]
            # A `cd` that will fail leaves it where it was; `cd X || exit 1; sed` runs in
            # either, depending on what follows `||`, so both are judged.
            heres = [*heres, *moved] if mark is not None and mark[0] == "||" else moved or heres
            # The same folder once; and a bound, as the guard's `STATES`, so no chain of `cd`s
            # grows past the hook's time. ponytail: past it the first few folders are judged.
            heres = list(dict.fromkeys(heres))[:FOLDERS]
            continue
        if not words or words[0].replace("\\", "/").rsplit("/", 1)[-1] not in {"sed", "sed.exe"}:
            continue
        files, script, inplace, args = [], False, False, iter(words[1:])
        for word in args:
            if word == "--":
                rest = list(args)
                files += rest[0 if script else 1:]  # without `-e`/`-f` the first one is still the script
                script = True
            elif word.startswith("--"):
                inplace |= word.startswith("--in-place")
                script |= word.startswith(("--expression", "--file"))
                if word in {"--expression", "--file"}:
                    next(args, None)
            elif word.startswith("-") and len(word) > 1:
                for j, letter in enumerate(word[1:]):
                    if letter == "i":  # the rest of the word is its backup suffix
                        inplace = True
                        break
                    if letter in "efl":  # the rest of the word, or the next one, is its argument
                        script |= letter != "l"
                        if j == len(word) - 2:
                            next(args, None)
                        break
            elif not script:
                script = True
            else:
                files.append(word)
        for here in heres if inplace else []:
            named = [p for f in files for p in (sorted(glob.glob(f, root_dir=here)) if set(f) & set("*?[") else [f])]
            if len(named) < BATCH:
                found += [s for p in named if not EXEMPT.search(p)
                          and (s := p if here == root else str(here / p)) not in found]
    return found


def tracked(paths: list[str], root: Path) -> list[str]:
    """Of those, the ones git tracks. A git that cannot answer passes, `craft/hooks-fail-open`."""
    from common.process import background_options

    kept = []
    for path in paths:
        candidate = Path(path) if os.path.isabs(path) else root / path
        try:
            # A literal pathspec: `file[1].txt` must not answer for a tracked `file1.txt`.
            listed = subprocess.run(["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", candidate.name],
                                    cwd=candidate.parent, capture_output=True, timeout=5, **background_options())
        except (OSError, subprocess.SubprocessError):
            continue
        if listed.returncode == 0:
            kept.append(path)
    return kept


def existing(paths: list[str], root: Path) -> list[str]:
    """Of those, the ones that exist. A path that does not is a new file, and passes."""
    live = []
    for path in paths:
        candidate = Path(path) if os.path.isabs(path) else root / path
        try:
            if candidate.is_file():
                live.append(path)
        except OSError:
            continue  # does not read as a path: judge nothing
    return live


def verdict(payload: dict, root: Path) -> dict | None:
    """The hook output when this has to be blocked, otherwise `None`."""

    tool = str(payload.get("tool_name"))
    given = payload.get("tool_input") or {}

    # `Write` hands the path over as an argument. As the page decides, it is
    # allowed for a new file and nothing else.
    if tool == "Write":
        path = str(given.get("file_path") or "")
        if not path or EXEMPT.search(path):
            return None
        hits = existing([path], root)
    elif tool == "Bash":
        command = str(given.get("command") or "")
        if not command:
            return None
        hits = existing([*targets(command), *in_place(command, root)], root)
    else:
        return None

    # git runs only here, on the rare call that writes an existing file.
    hits = tracked(hits, root) if hits else []
    if not hits:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(paths=", ".join(hits)),
        },
        "systemMessage": f"git 이 추적하는 파일을 되쓰려 해서 막았다: {', '.join(hits)}",
    }


def main() -> int:
    # What is judged and the reason given are both Korean. The encoding is not
    # left to the environment, where the default on this pipe is cp949.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        root = Path(str(payload.get("cwd") or Path.cwd()))
        answer = verdict(payload, root)
    except Exception:
        return 0  # pass: a broken hook must not stop the work
    if answer:
        json.dump(answer, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    # The rule at the top of this file, extended past `main`. Whatever goes
    # wrong, pass.
    try:
        _code = main()
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
