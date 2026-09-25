"""lint — find the places a wiki rots.

Every finding is printed for a person, so those strings are Korean.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import re
import subprocess
import sys
import tokenize
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import markdown_emphasis  # noqa: E402
from wiki import (  # noqa: E402
    SCOPES, WIKI, hub_pages, links_of, metadata_errors, resolve,
)


def check(
    wiki: Path = WIKI,
    adapters: Path | None = None,
    repos: list[Path] | None = None,
) -> tuple[dict[str, tuple[dict, str, Path]], set[frozenset[str]], list[tuple[str, str]]]:
    """Read the pages and produce findings. `main` does the printing.

    `wiki` is a parameter so this can be run against a throwaway wiki and each
    check watched actually going red. A green with zero findings is not
    evidence that a check ran.
    """

    adapters = adapters if adapters is not None else wiki / "adapters"
    repos = repos or []

    loaded = hub_pages(wiki)
    names = set(loaded)
    findings: list[tuple[str, str]] = []
    for name, (_meta, _body, path) in loaded.items():
        findings.extend(("페이지 형식 오류", f"`{name}`: {error}") for error in metadata_errors(path))
    if wiki.resolve() == WIKI.resolve():
        from apply import wiring_drift
        findings += wiring_drift(wiki)
    findings += fragile_io(wiki)
    findings += loud_emphasis(wiki)
    # Repositories given with `--repo` are looked at too. `repo_lint` holds
    # the same checks, but it runs inside the target repository; skipping them
    # on a hub-wide sweep would make "everything here was looked at" false.
    for repo in repos:
        findings += loud_emphasis(repo)
    findings += missing_hook_guards(wiki, loaded)
    findings += pipeline_imports(wiki)
    findings += pipeline_surface(wiki)

    # --- 1. Broken links
    inbound: dict[str, set[str]] = {name: set() for name in names}
    for name, (meta, body, _path) in loaded.items():
        for target in links_of(meta, body):
            hit = resolve(target, names)
            if hit is None:
                findings.append(("끊어진 링크", f"`{name}` → `[[{target}]]` 가 없다"))
            else:
                inbound[hit].add(name)

    # --- 2. Orphan pages
    for name in sorted(names):
        if not inbound[name]:
            findings.append((
                "고아 페이지",
                f"`{name}` 를 아무도 링크하지 않는다",
            ))

    # --- 3. Stale statements, and only the ones a machine is sure of
    for name, (meta, body, _path) in loaded.items():
        severity = str(meta.get("severity") or "")
        # Public exports retain severity while withholding the private source records.
        if severity == "landmine" and not (meta.get("sources") or []) and meta.get("sources_withheld") is not True:
            findings.append((
                "근거 없는 landmine",
                f"`{name}` 이 `landmine` 인데 `sources` 가 비었다. "
                "무엇을 태웠는지 못 대면 등급을 내려라",
            ))
        if severity in ("landmine", "contract") and not (meta.get("triggers") or []):
            findings.append((
                "낡은 서술",
                f"`{name}` 이 `{severity}` 인데 `triggers` 가 없다. "
                "주입 대상인데 걸릴 발화가 없으니 아무 때도 안 실린다",
            ))
        for source in meta.get("sources") or []:
            path = wiki / str(source).split("#")[0]
            if not path.exists():
                findings.append(("낡은 서술", f"`{name}` 의 근거 `{source}` 가 없다"))
        refs = set(re.findall(r"`((?:feat|fix|chore|claude)/[a-z0-9-]+)`", body))
        for repo in repos:
            for ref in refs:
                if not git_ok(repo, ref):
                    findings.append((
                        "낡은 서술",
                        f"`{name}` 가 `{ref}` 를 가리키는데 `{repo.name}` 에 없다",
                    ))

    # --- 4. Contradictions, and only the ones nobody declared
    declared: set[frozenset[str]] = set()
    for name, (meta, _body, _path) in loaded.items():
        for entry in meta.get("conflicts_with") or []:
            other = entry.get("page") if isinstance(entry, dict) else str(entry)
            if other:
                declared.add(frozenset({name, other}))

    slot_values: dict[str, dict[str, str]] = {}
    if adapters.is_dir():
        for path in sorted(adapters.glob("*.toml")):
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for slot, value in (data.get("slots") or {}).items():
                slot_values.setdefault(slot, {})[path.stem] = str(value)

    for slot, by_project in sorted(slot_values.items()):
        distinct = set(by_project.values())
        if len(distinct) > 1:
            where = " · ".join(f"{k}={v!r}" for k, v in sorted(by_project.items()))
            findings.append((
                "모순(슬롯)",
                f"`{slot}` 이 프로젝트마다 다르다 — {where}. "
                "형태만 공유하고 값은 다른 것이 정상이면 그대로 두라. "
                "슬롯은 원래 그러라고 있다",
            ))

    # --- 5. Missing links
    #
    # Measured by whether two pages are injected into the same utterance. Two
    # rules that arrive side by side in one turn and do not know about each
    # other leave the reader unable to see the relation. That is the link
    # that is missing.
    #
    # Not measured by shared grounds. When the grounds are a census corpus
    # file every page shares it, and everything looks connected to everything.
    # A corpus is the whole body of material, not a particular claim.
    triggers_of = {
        name: [str(t) for t in (meta.get("triggers") or [])]
        for name, (meta, _b, _p) in loaded.items()
    }
    for a in sorted(names):
        for b in sorted(names):
            if a >= b or not triggers_of[a] or not triggers_of[b]:
                continue
            if b in inbound[a] or a in inbound[b]:
                continue
            if frozenset({a, b}) in declared:
                continue
            shared = sorted(set(triggers_of[a]) & set(triggers_of[b]))
            if shared:
                findings.append((
                    "빠진 연결",
                    f"`{a}` 와 `{b}` 가 같은 발화에 함께 실리는데"
                    f"(공유 트리거 {shared[:2]}) 서로 링크하지 않는다",
                ))

    # --- 7. Tools that leave their encoding to the environment
    for name in fragile_tools(wiki):
        findings.append((
            "인코딩 미고정",
            f"`tool/{name}` 이 stdout 에 쓰는데 인코딩을 고정하지 않는다 — "
            'cp949 에서 한 글자에 죽는다. `sys.stdout.reconfigure(encoding="utf-8")`',
        ))

    # --- 8. Line breaks that split one breath to fit a width
    for where, before, after in broken_wraps(wiki):
        findings.append((
            "끊긴 줄바꿈",
            f"`{where}` 이 `{before} / {after}` 사이에서 끊긴다 — "
            "폭이 아니라 문장·절 경계에서 끊어라",
        ))

    # --- 9. Comments and docstrings still written in Korean
    if markdown_emphasis.parser() is None:
        # A gate that cannot run one of its checks says so in its report and
        # finishes the rest. Letting the exception out took the header, the
        # findings already gathered and the reason with it, and left a
        # traceback in their place — red, but saying nothing about what was
        # examined. `loud_emphasis` settled this shape already.
        findings.append(("주석이 한국어다", NO_PARSER))
    else:
        for where, line in korean_prose(wiki):
            findings.append((
                "주석이 한국어다",
                f"`{where}`: {line} — `operator/english-progress` 는 에이전트가 "
                "쓰는 것을 영어로 둔다. 인용하는 한국어는 한 줄 안에서 백틱으로 감싸라",
            ))

    return loaded, declared, findings




HANGUL = re.compile(r"[가-힣]")

# A Korean example being quoted, as opposed to a comment written in Korean.
# One notation marks it, and the notation is the backtick.
#
# Three rounds tried to read ordinary quotation marks as citation and each one
# found another member missing from the set: single quotes, then curly ones,
# then a prime after a digit and after an underscore. The fourth round ended
# the argument. A quote mark is not a marker — it is ordinary English
# punctuation, so pairing two of them is a guess about which two belong
# together, and the guess is wrong exactly where it hurts:
#
#     The output starts with " but has no closing delimiter.
#     `한국어 산문이다.`
#     Later the comment names "done" as a separate token.
#
# The first `"` paired with the one in front of `done`, the Korean between the
# two vanished, and the gate went green over a violation. That is the failure
# this check was built to end. No amount of adjacency rules fixes it, because
# the information needed — did the author mean to quote — is not in the
# characters.
#
# A backtick carries no other meaning in a comment. Writing one says "this is
# a string, not my sentence", which is the distinction being asked about.
#
# What a code span *is* comes off a CommonMark parse, not off a regex here.
# The one-backtick regex that replaced the quote rule refused a Korean
# citation written in a doubled span: it erased the two opening backticks as
# an empty span, erased the two closing ones the same way, and left the
# Korean between them looking like prose. `test_lint` has the input, where it
# can be written without being read as one itself. A doubled span is how
# CommonMark writes a citation containing a backtick, so the delimiter run
# that stage 2 deferred as a suggestion turned into a gate refusing correct
# work the moment backticks became the only marker left.
# `markdown_emphasis` learned this over six rounds of a hand-written
# scanner and wrote down the conclusion; there is no reason to spend them
# again one module over.
#
# The parse runs per line. A span that wraps is already a finding of its own
# (`끊긴 줄바꿈`), so nothing legitimate crosses a line break, and one line at
# a time is what makes the position exact — a span left open cannot reach past
# its own line, and an unclosed backtick simply forms no span, which leaves
# the Korean beside it visible rather than hidden.
CODE = "code_inline"

# Its own sentence, not `markdown_emphasis.MISSING`. That one names the
# emphasis check, and a reader told the emphasis check did not run while this
# one was the one that stopped goes looking in the wrong place. The recovery
# command is shared, because there is only one thing to install.
NO_PARSER = ("markdown-it-py 가 없어 주석 검사를 돌리지 못했다 — "
             + markdown_emphasis.recovery(sys.executable))


def docstring(node) -> ast.Constant | None:
    """The string literal a docstring is, or `None` where there is none.

    `ast.get_docstring` is not used, because what comes back from it is the
    evaluated value and this check needs the source. Implicitly joined
    literals arrive here as one `Constant` spanning every line they were
    written on, which is what makes the line count come out right.
    """

    body = getattr(node, "body", None)
    if not body or not isinstance(body[0], ast.Expr):
        return None
    value = body[0].value
    ok = isinstance(value, ast.Constant) and isinstance(value.value, str)
    return value if ok else None


def cited(line: str, md) -> str:
    """The code spans of one line, concatenated — the part that is quoted.

    Only this is read off the parse. An earlier version did the reverse and
    gathered the text of everything that was *not* a code span, which meant
    deciding what every other token type contributes — and `html_block` was
    decided wrong: `<div>` around a Korean line hid it from the gate
    completely. Reading out what a parse means, token kind by token kind, is
    the hand-written lexer coming back through the parser's own door.

    So the line stays whole and only the spans are subtracted from it. A
    token kind this function has never heard of cannot hide anything, because
    nothing but a code span is ever taken away.

    Indentation goes first, and it goes twice — once before the `#` and once
    after it. Four spaces in front of a line is an indented code block to
    CommonMark, which parses no inline markup at all, so the line would carry
    no code span and every Korean character on it would read as prose. This
    check caught its own explaining comment that way, where an example is
    written indented under a `#`: stripping only in front of the marker left
    five spaces behind it.

    Indentation is meant to carry nothing here. The backtick is the only
    marker, in a comment as in a docstring, at whatever depth it is written.
    """

    spans: list[str] = []

    def gather(token) -> None:
        if token.type == CODE:
            spans.append(token.content)
        for kid in token.children or []:
            gather(kid)

    for token in md.parse(line.lstrip().lstrip("#").lstrip()):
        gather(token)
    return "".join(spans)


def korean_prose(wiki: Path = WIKI) -> list[tuple[str, str]]:
    """`tool/*.py` comments and docstrings still written in Korean.

    One notation separates the two cases, not a ratio and not a set of
    punctuation marks. A comment that cites `올리겠습니다` is describing the
    data a regex matches and has to keep it; a comment written in Korean is
    the thing `english-progress` asks to move. A first attempt scored the
    share of Hangul per line and could not tell them apart at any threshold —
    the citations landed at 0.40 to 0.47, in among real violations. A second
    read ordinary quotation marks as citation and spent three review rounds
    adding members to that set before an unmatched `"` hid a violation
    outright. See `cited` for why the backtick is the whole rule and why a
    parser, not a regex, decides where one begins.

    What is left is counted character by character, not as a share. The line
    keeps every Hangul character it was written with and the ones the parse
    says are inside a code span are taken away; anything still standing is
    prose. Subtracting is what makes an unfamiliar token kind harmless.

    Read through `ast` and `tokenize` rather than by matching `#` against raw
    lines. A regex on `#` sees no docstring at all, and that is exactly how
    this was reported complete while 104 lines were still Korean: the
    measurement answered a narrower question than the claim made.

    A docstring is read as **source**, through `get_source_segment`, not as
    the value `get_docstring` evaluates. The value is not the text on the
    page: `\\n` written as an escape is one source line and two evaluated
    ones, two implicitly joined literals are two source lines and one
    evaluated one, and either way the reported line pointed somewhere the
    reader has to go looking. Source text counts lines the way the file does.
    """

    directory = wiki / "tool"
    if not directory.is_dir():
        return []

    md = markdown_emphasis.parser()
    if md is None:
        # Loud, never quiet. `check` catches this case ahead of the call and
        # turns it into a finding so the report still prints; reaching here
        # means somebody called this directly, and an empty list would tell
        # them the tools are clean when nothing was read at all.
        raise RuntimeError(NO_PARSER)

    found: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            # Unreadable or unparsable is a finding of its own, raised by the
            # checks that own it. Silence here would make this one green.
            continue

        pieces: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                literal = docstring(node)
                if literal is None:
                    continue
                # The literal's own line, not the `def` above it and not the
                # `Expr` that may open on an earlier line with a parenthesis.
                segment = ast.get_source_segment(source, literal)
                if segment:
                    pieces.append((literal.lineno, segment))
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.COMMENT:
                    pieces.append((token.start[0], token.string))
        except tokenize.TokenError:
            continue

        for at, piece in pieces:
            for n, line in enumerate(piece.splitlines()):
                rest = Counter(HANGUL.findall(line))
                rest -= Counter(HANGUL.findall(cited(line, md)))
                if rest:
                    found.append((f"tool/{path.relative_to(directory).as_posix()}:{at + n}", line.strip()[:60]))
    return found


def fragile_tools(wiki: Path = WIKI) -> list[str]:
    """`tool/*.py` that writes to stdout without pinning its encoding.

    Whether the file contains Korean is deliberately not part of the test. A
    tool that emits only ASCII today dies tomorrow when one line is added, and
    pinning it costs one line — narrow the condition and the narrowed place is
    the next incident. What made this check necessary was `lint.py` itself,
    which was already reading files with `encoding="utf-8"`. Fixing the read
    does not fix the write.

    `craft/hooks-fail-open` wrote this failure down as being about hooks, but
    the condition that fails is not being a hook — it is being Python whose
    stdout is a pipe. Four hooks got fixed and eight CLIs under the same
    condition did not, and that gap is what the narrowing cost.
    """
    directory = wiki / "tool"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.rglob("*.py")):
        calls = [n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                 if isinstance(n, ast.Call)]
        writes = any(ast.unparse(n.func) in ("print", "json.dump", "sys.stdout.write") for n in calls)
        fixed = any(ast.unparse(n.func) == "sys.stdout.reconfigure" and any(
            k.arg == "encoding" and isinstance(k.value, ast.Constant) and k.value.value == "utf-8"
            for k in n.keywords
        ) for n in calls)
        if writes and not fixed:
            found.append(path.relative_to(directory).as_posix())
    return found


def missing_hook_guards(wiki: Path, loaded: dict) -> list[tuple[str, str]]:
    """Check the entry-point guard on shared event hooks and page-declared ones."""
    names = {"inject.py", "session_state.py", "sync.py", "declared_continuation.py", "codex_pretool.py"}
    for meta, _body, _path in loaded.values():
        enforce = meta.get("enforce") or {}
        if isinstance(enforce, dict) and enforce.get("pretooluse"):
            names.add(str(enforce["pretooluse"]))
    found = []
    for name in sorted(names):
        path = wiki / "tool" / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        entries = [node for node in tree.body if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "__name__ == '__main__'"]
        guarded = any(
            isinstance(node, ast.Try) and any(
                isinstance(call, ast.Call)
                for statement in node.body for call in ast.walk(statement)
            ) and any(
                handler.type and ast.unparse(handler.type) == "Exception" and (any(
                    isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
                    and statement.value.value == 0 for statement in handler.body
                ) or not any(isinstance(statement, ast.Raise) for statement in ast.walk(entry)))
                for handler in node.handlers
            ) for entry in entries for node in entry.body
        )
        if not guarded:
            found.append(("훅 가드 누락", f"`tool/{name}`: 진입점 예외를 통과시키는 가드가 없다"))
    return found


def git_ok(repo: Path, ref: str) -> bool:
    """Does that ref actually exist in this repository?"""

    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, timeout=10,
        ).returncode == 0
    except Exception:
        return False


# One folder per pipeline under `tool/`. `common` is the one place all of them
# may share, and it may import none of them.
PIPELINES = ("wiki", "translate", "agent", "workspace")


def reached(node: ast.AST, depth: int) -> list[str]:
    """The modules an import reaches, named as if from inside `tool/`.

    The same module has three spellings. `translate`, `tool.translate` —
    importable whenever the repository root is on the path, which it is under
    pytest — and `..translate` from a file one folder down. Reading only the
    first let the other two through a green gate. `depth` is how many folders
    below `tool/` the file sits; a relative import that climbs that many or
    more lands on the root.
    """

    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level <= depth:
            return []  # still inside this pipeline
        # `from tool import x` and `from .. import x` name modules in the list.
        names = ([node.module] if node.module and node.module != "tool"
                 else [alias.name for alias in node.names])
    else:
        return []
    return [name.removeprefix("tool.") for name in names]


def pipeline_imports(wiki: Path = WIKI) -> list[tuple[str, str]]:
    """A pipeline folder importing another pipeline, or a module at the `tool/` root.

    Pipelines do not know each other; only a main weaves them. The `tool/`
    root is where the mains live — the hook entry points and the server — so
    a pipeline reaching into it is a way around the boundary, not a way along
    it. Imports inside functions count as much as those at the top: this code
    base defers imports often, and a check that saw only the top would pass
    most of them. See `docs/plans/done/wiki-agent/0-overview.md`.
    """

    tool = wiki / "tool"
    # `main` is the program's main, a package rather than a file since phase 6.
    roots = {path.stem for path in tool.glob("*.py")} | {"main"}
    found = []
    for name in (*PIPELINES, "common"):
        allowed = {name} if name == "common" else {name, "common"}
        for path in sorted((tool / name).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            depth = len(path.relative_to(tool).parts) - 1
            for node in ast.walk(tree):
                for target in reached(node, depth):
                    top = target.split(".")[0]
                    if top not in allowed and (top in PIPELINES or top == "common" or top in roots):
                        where = path.relative_to(tool).as_posix()
                        found.append((
                            "파이프라인 경계",
                            f"`tool/{where}:{node.lineno}`: `{name}` 가 `{target}` 를 부른다 — "
                            "파이프라인끼리는 서로를 모른다. 엮는 일은 메인(`tool/` 루트)이 한다",
                        ))
    return found


def exported(init: Path) -> set[str] | None:
    """The literal `__all__` of a pipeline's `__init__.py`, or `None` without one."""

    try:
        tree = ast.parse(init.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            try:
                return {str(name) for name in ast.literal_eval(node.value)}
            except ValueError:
                return None
    return None


def pipeline_surface(wiki: Path = WIKI) -> list[tuple[str, str]]:
    """A `tool/` root module using a pipeline name its `__all__` does not export.

    A pipeline that declares `__all__` has gathered its public entry point, and
    everything else in it is free to change. A main that reached past the list
    would break on that change, so the list is held here rather than in a
    comment. Pipelines without one are not gathered yet and are not read.
    Tests are exempt: proving a pipeline's insides is what they are for.
    """

    tool = wiki / "tool"
    # The mains: the `tool/` root, where the hook entry points live, and the
    # program's `tool/main/`.
    mains = sorted([*tool.glob("*.py"), *(tool / "main").rglob("*.py")])
    found = []
    for name in PIPELINES:
        public = exported(tool / name / "__init__.py")
        if public is None:
            continue
        for path in mains:
            if path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            bound: set[str] = set()   # names this file binds to the pipeline itself
            # Names bound to `tool` itself. `import tool.translate` binds `tool`,
            # not `translate`, and `tool.translate._ask()` walked past a check
            # that only looked for the second.
            package: set[str] = set()
            used: list[tuple[int, str]] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "tool" or (alias.name.startswith("tool.") and not alias.asname):
                            package.add(alias.asname or "tool")
                        target = alias.name.removeprefix("tool.")
                        if target == name:
                            if alias.asname or alias.name == name:
                                bound.add(alias.asname or name)
                        elif target.startswith(name + "."):
                            used.append((node.lineno, target))
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    module = node.module or ""
                    if module == "tool":
                        bound |= {a.asname or a.name for a in node.names if a.name == name}
                        continue
                    target = module.removeprefix("tool.")
                    if target == name:
                        used += [(node.lineno, f"{name}.{a.name}") for a in node.names
                                 if a.name not in public]
                    elif target.startswith(name + "."):
                        used.append((node.lineno, target))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr in public:
                    continue
                owner = node.value
                if (isinstance(owner, ast.Name) and owner.id in bound) or (
                        isinstance(owner, ast.Attribute) and owner.attr == name
                        and isinstance(owner.value, ast.Name) and owner.value.id in package):
                    used.append((node.lineno, f"{name}.{node.attr}"))
            for line, target in sorted(set(used)):
                found.append((
                    "공개 진입점",
                    f"`tool/{path.relative_to(tool).as_posix()}:{line}`: `{target}` 는 `{name}` 의 `__all__` 밖이다 — "
                    f"`{name}` 은 `{', '.join(sorted(public))}` 로만 부른다",
                ))
    return found


def tracked_markdown(root: Path) -> list[str]:
    """The `.md` this repository considers its own. No hand-written exclusions.

    The first version filtered `web/`, `artifacts/`, `raw/` and `node_modules`
    by name. Those are the hub's circumstances and not a target repository's,
    so in someone else's repository real documents disappeared wholesale — and
    so did files that merely started the same way, like
    `node_modules-guide.md`.

    "Is this file ours" is a question git already answers. What is tracked,
    plus what is not yet `git add`ed and not ignored either: build output and
    vendored code are in `.gitignore`, so they drop out, and nobody reviews
    them anyway.
    """

    # `-c` is the index, `-o` is what has not been `git add`ed, and
    # `--exclude-standard` removes what is ignored. With `-c` alone a document
    # just created by `Write` is invisible, and once fragment edits pile onto
    # that file no check ever looks at it.
    #
    # The extension is not filtered with a pathspec. `*.md` is case-sensitive
    # and would drop `UPPER.MD`, while the hook lowercases before deciding —
    # and then the two checks are looking at different sets of files.
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
        capture_output=True, check=False,
    )
    if done.returncode == 0:
        names = done.stdout.decode("utf-8", "replace").split("\0")
    else:
        # Not a git repository: look at everything. This path is the tests,
        # which run against a temporary directory.
        names = [p.relative_to(root).as_posix() for p in root.rglob("*")]
    # Drop what is still in the index but gone from the working tree.
    # Reporting a file being deleted as unreadable turns an ordinary deletion
    # into a red gate.
    return sorted(
        name for name in names
        if name.lower().endswith(".md") and (root / name).is_file()
    )


SHA256 = re.compile(r"\b[0-9a-f]{64}\b")


def pinned_originals(root: Path, names: list[str]) -> set[str]:
    """`.md` whose bytes the repository has pinned. No style findings, because
    there is no way to act on one.

    A repository holding competition material or someone else's original
    writes that file's SHA-256 into its own documents to claim "identical
    before and after the move". One backtick added to such a file makes that
    record false, so a style finding against it cannot be fixed — it sits in
    the health check forever and the next person makes the same judgement
    again.

    Not matched by name. As `tracked_markdown` records, a list of names like
    `archive/` wiped out real documents in other repositories. What decides it
    here is the hash that repository actually wrote down — remove the pin and
    the check runs again. A file cannot record its own hash, so there is no
    circularity either.
    """

    digests: dict[str, str] = {}
    recorded: set[str] = set()
    for name in names:
        try:
            raw = (root / name).read_bytes()
        except OSError:
            continue
        digests[name] = hashlib.sha256(raw).hexdigest()
        recorded.update(SHA256.findall(raw.decode("utf-8", "replace").lower()))
    return {name for name, digest in digests.items() if digest in recorded}


def loud_emphasis(wiki: Path = WIKI) -> list[tuple[str, str]]:
    """`.md` where emphasis has become noise. What the hook cannot see is seen here.

    The `markdown_emphasis` hook is called before the write, so it never sees
    the document an `Edit` or a patch is about to produce. A version that
    tried to predict it leaked through a different input shape in each of
    three review rounds — multiple hunks, `replace_all`, four backticks. The
    prediction is gone and this reads the real file instead. What it reads is
    already on disk, so there is nothing left to get wrong.
    """

    import markdown_emphasis

    found = []
    if markdown_emphasis.parser() is None:
        # Not having been able to run the check and having run it clean are
        # different things. Returning an empty list quietly here leaves the
        # gate green with this one rule switched off inside it.
        return [("강조 과다", markdown_emphasis.MISSING)]
    names = tracked_markdown(wiki)
    pinned = pinned_originals(wiki, names)
    for name in names:
        if name in pinned:
            continue
        path = wiki / name
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as error:
            # Skipping a file that could not be read makes "all of them were
            # looked at" false. Writing UTF-8 is the rule here, so failing to
            # read one is itself the finding.
            found.append(("강조 과다", f"`{name}`: 읽지 못했다 ({type(error).__name__})"))
            continue
        try:
            lines = markdown_emphasis.findings(text)
        except Exception as error:  # noqa: BLE001
            # A hook passes here and says so on screen. A gate has to stop
            # instead — but crashing does not mean "this one file went
            # unchecked", it means "nobody looked at the rest either". So it
            # becomes a finding and the sweep continues.
            found.append(("강조 과다", f"`{name}`: 검사가 실패했다 ({type(error).__name__})"))
            continue
        for line in lines:
            found.append(("강조 과다", f"`{name}`: {line.lstrip('- ')}"))
    return found


def fragile_io(wiki: Path = WIKI) -> list[tuple[str, str]]:
    """stdin, and the text output of child processes in the running tools.

    A test's strict decoding is left alone — there, a decode error is the
    result being looked for.
    """
    found = []
    for path in sorted((wiki / "tool").rglob("*.py")):
        where = path.relative_to(wiki / "tool").as_posix()
        calls = [n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                 if isinstance(n, ast.Call)]
        reads = any(ast.unparse(n.func).startswith("sys.stdin.read") or (
            ast.unparse(n.func) == "json.load" and n.args and ast.unparse(n.args[0]) == "sys.stdin"
        ) for n in calls)
        fixed = any(ast.unparse(n.func) == "sys.stdin.reconfigure" and any(
            k.arg == "encoding" and isinstance(k.value, ast.Constant) and k.value.value == "utf-8"
            for k in n.keywords
        ) for n in calls)
        if reads and not fixed:
            found.append(("인코딩 미고정", f"`tool/{where}`: stdin UTF-8 고정이 없다"))
        if path.name.startswith("test_"):
            continue
        for call in calls:
            if ast.unparse(call.func) not in ("subprocess.run", "subprocess.Popen"):
                continue
            kw = {k.arg: ast.literal_eval(k.value) for k in call.keywords if isinstance(k.value, ast.Constant)}
            if (kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")) and (
                kw.get("encoding") != "utf-8" or kw.get("errors") != "replace"
            ):
                found.append(("인코딩 미고정", f"`tool/{where}:{call.lineno}`: 자식 출력의 UTF-8/replace 누락"))
    return found


# This replaced a Korean rule that looked for an adnominal ending followed by a
# bound noun (`쓰는 것`, `없을 때`, `한 줄`) — a pair written with a space that
# is one breath, and unreadable split. English has no such pair.
#
# The first English version tried the obvious translation: a line ending on an
# article, a preposition, a conjunction or an auxiliary. It found 437 places in
# 1,515 line pairs. Almost none were defects — ending a line on `the` is
# ordinary typesetting, and a check at 29% is not a check, it is a thing people
# switch off.
#
# Measuring what actually cannot survive a line break left two shapes, and both
# are about a span rather than a word:
#
#   - an inline code span cut in half. `a` and `b` on separate lines is not one
#     span any more, and in the source it reads as two broken ones. Three in
#     this repository, two of them written the same afternoon
#   - a number parted from its unit: `21` on one line and `pages` on the next
#
# Neither needs judgement, which is why they can be counted.
UNIT = re.compile(
    r"^(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?"
    r"|bytes?|chars?|characters?|lines?|rows?|columns?|pages?|files?"
    r"|rounds?|turns?|times?|places?|cases?|items?|per)\b",
    re.I,
)
BARE_NUMBER = re.compile(r"^\d[\d,.]*$")


def orphan_tail(line: str) -> bool:
    """Does this line end mid-span, with nothing able to close it?

    Backtick parity, not a word list. An odd count means a span opened here and
    has to reach the next line to close, and that is the break this catches.
    """

    return line.count("`") % 2 == 1


def splits_a_phrase(before: str, after: str) -> bool:
    """Do these two lines belong on one? `before` and `after` are whole lines."""

    if not before.strip() or not after.strip():
        return False
    if orphan_tail(before) and "`" in after:
        return True
    tail, head = before.split()[-1], after.split()[0]
    return bool(BARE_NUMBER.match(tail.strip("`*_")) and UNIT.match(head.strip("`*_,.")))


def prose_lines(path: Path) -> list[tuple[int, str]]:
    """Prose lines only, as `(line number, text)`. Not code, tables, lists or
    front matter.

    In a `.py` that means comments and triple-quoted strings, under the same
    rule. Splitting a breath to fit a width does not care which one it is in.
    """

    text = path.read_text(encoding="utf-8")
    rows: list[tuple[int, str]] = []

    if path.suffix == ".md":
        lines = text.splitlines()
        start = 0
        if lines and lines[0].strip() == "---":
            closing = next(
                (i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0
            )
            start = closing + 1
        fenced = False
        for number, raw in enumerate(lines[start:], start + 1):
            line = raw.strip()
            if line.startswith("```"):
                fenced = not fenced
                continue
            if fenced or not line or line.startswith(("|", "#", "-", "*", ">", "1.")):
                continue
            rows.append((number, line))
        return rows

    lines = text.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    for token in tokens:
        if token.type == tokenize.COMMENT:
            body = lines[token.start[0] - 1].strip()
            if body.startswith("#"):
                rows.append((token.start[0], body.lstrip("# ").strip()))
        elif token.type == tokenize.STRING and token.string.lstrip(
            "rbuRBUfF"
        ).startswith(('"""', "'''")):
            for offset, body in enumerate(token.string.splitlines()):
                stripped = body.strip().strip("\"'")
                if stripped:
                    rows.append((token.start[0] + offset, stripped))
    return sorted(rows)


def broken_wraps(wiki: Path = WIKI) -> list[tuple[str, str, str]]:
    """Where a line break split a phrase. `(where, before, after)`.

    Page prose is read too. There were five places where the document holding
    a rule broke that rule, and one of them was the paragraph explaining how
    this wiki wraps. A place a check cannot see itself lives a long time.
    """

    targets = sorted((wiki / "tool").rglob("*.py"))
    for scope in SCOPES:
        targets += sorted((wiki / scope).glob("*.md"))

    found: list[tuple[str, str, str]] = []
    for path in targets:
        rows = prose_lines(path)
        for (number, first), (following, second) in zip(rows, rows[1:]):
            if following != number + 1:
                continue
            if not first.split() or not second.split():
                continue
            # Whole lines, not the two words. Backtick parity is a property of
            # the line, and the word-level version of this silently counted
            # the tick that closed a span as one that opened it.
            if splits_a_phrase(first, second):
                where = path.relative_to(wiki).as_posix()
                found.append(
                    (f"{where}:{number}", first.split()[-1], second.split()[0])
                )
    return found


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="위키의 썩은 자리를 찾는다")
    parser.add_argument("--wiki", type=Path, default=WIKI)
    parser.add_argument("--adapters", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="게이트용: 슬롯 값 차이만 종료 코드에서 제외")
    parser.add_argument(
        "--repo", action="append", type=Path, default=[],
        help="같이 검진할 저장소. 낡은 서술과 그 저장소의 `.md` 강조를 본다. "
             "무시 대상이 아닌 `.md` 는 아직 `git add` 안 한 것도 본다. "
             "여러 번 줄 수 있다",
    )
    args = parser.parse_args()

    loaded, declared, findings = check(args.wiki, args.adapters, args.repo)

    print(f"# lint — 페이지 {len(loaded)}장\n")

    if declared:
        print(f"## 선언된 갈림 {len(declared)}쌍 — lint 가 지나간다\n")
        for pair in sorted(declared, key=lambda s: sorted(s)):
            print(f"- {' ↔ '.join(sorted(pair))}")
        print()

    if not findings:
        print("새 발견 없음.")
        return 0

    print(f"## 발견 {len(findings)}건\n")
    kinds: dict[str, list[str]] = {}
    for kind, message in findings:
        kinds.setdefault(kind, []).append(message)
    order = (
        "훅 배선 드리프트", "훅 가드 누락", "파이프라인 경계", "공개 진입점", "페이지 형식 오류", "인코딩 미고정", "강조 과다", "끊어진 링크", "근거 없는 landmine", "낡은 서술",
        "모순(슬롯)", "고아 페이지", "빠진 연결", "끊긴 줄바꿈",
    )
    # A kind missing from `order` still turns the exit code red, so it has to
    # be printed too — or the gate fails with nothing on screen to say why.
    for kind in order + tuple(k for k in kinds if k not in order):
        if kind not in kinds:
            continue
        print(f"### {kind} — {len(kinds[kind])}건\n")
        for message in kinds[kind]:
            print(f"- {message}")
        print()
    return int(any(kind != "모순(슬롯)" for kind, _message in findings)) if args.check else 1


if __name__ == "__main__":
    raise SystemExit(main())
