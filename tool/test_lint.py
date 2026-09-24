"""Prove each of `lint`'s checks actually goes red."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import markdown_emphasis  # noqa: E402
from lint import check, korean_prose  # noqa: E402

PAGE = """---
scope: {scope}
severity: {severity}
triggers: {triggers}
slots: []
sources: {sources}
links: {links}
---

# {name}

규칙. 한 줄.
{extra}
"""


def build(root: Path, pages: dict[str, dict]) -> None:
    """Write a table of pages as a throwaway wiki, grounds files and all."""

    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "c.jsonl").write_text("{}\n", encoding="utf-8")
    for full, spec in pages.items():
        scope, stem = full.split("/", 1)
        (root / scope).mkdir(parents=True, exist_ok=True)
        (root / scope / f"{stem}.md").write_text(
            PAGE.format(
                scope=scope,
                name=stem,
                severity=spec.get("severity", "contract"),
                triggers=spec.get("triggers", '["ㄱ"]'),
                sources=spec.get("sources", "[raw/c.jsonl]"),
                links=spec.get("links", "[]"),
                extra=spec.get("extra", ""),
            ),
            encoding="utf-8",
        )


CLEAN = {
    "operator/a": {"triggers": '["가"]', "links": "[b]"},
    "craft/b": {"triggers": '["나"]', "links": "[a]"},
}


def _tool(root: Path, name: str, source: str) -> None:
    """Plant one tool in the throwaway wiki. The encoding check looks in `tool/`."""
    path = root / "tool" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


FIXED = 'import sys\nsys.stdout.reconfigure(encoding="utf-8")\n'


def _clean_tool(root: Path, name: str, source: str) -> None:
    """Plant a tool with its encoding already pinned.

    One defect turning two checks red leaves no way to tell which check
    caught it.
    """

    _tool(root, name, FIXED + source)


SURFACE = '__all__ = ("translate", "KO_EN")\n'


def _surface(root: Path, source: str) -> None:
    """A pipeline that has gathered its entry point, and a main that uses it."""

    _clean_tool(root, "translate/__init__.py", SURFACE)
    _clean_tool(root, "main.py", source)


def kinds_and_messages(root: Path) -> list[tuple[str, str]]:
    _loaded, _declared, findings = check(wiki=root, adapters=root / "none")
    return findings


def kinds(root: Path) -> set[str]:
    return {kind for kind, _msg in kinds_and_messages(root)}


def run(label: str, mutate, expect: str) -> bool:
    """Plant a defect and watch that check go red."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pages = {name: dict(spec) for name, spec in CLEAN.items()}
        mutate(pages, root)
        build(root, pages)
        found = kinds(root)
    ok = expect in found
    print(f"  {'RED  ' if ok else '초록 '} {label:<28} → {sorted(found) or '없음'}")
    return ok


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, CLEAN)
        base = kinds(root)
    print(f"기준 (결함 없음) → {sorted(base) or '없음'}")
    if base:
        print("기준이 이미 빨갛다. 아래 결과는 못 믿는다.")
        return 1

    checks = [
        (
            "끊어진 링크",
            lambda p, r: p["operator/a"].update(links="[nowhere]"),
            "끊어진 링크",
        ),
        (
            "고아 페이지",
            lambda p, r: p["operator/a"].update(links="[]"),
            "고아 페이지",
        ),
        (
            "근거가 사라짐",
            lambda p, r: p["operator/a"].update(sources="[raw/gone.jsonl]"),
            "낡은 서술",
        ),
        (
            "landmine 인데 근거 없음",
            lambda p, r: p["operator/a"].update(severity="landmine", sources="[]"),
            "근거 없는 landmine",
        ),
        (
            "contract 인데 트리거 없음",
            lambda p, r: p["operator/a"].update(triggers="[]"),
            "낡은 서술",
        ),
        (
            "트리거 공유하는데 링크 없음",
            lambda p, r: (
                p["operator/a"].update(triggers='["같은말"]', links="[]"),
                p["craft/b"].update(triggers='["같은말"]', links="[]"),
            ),
            "빠진 연결",
        ),
        (
            "도구가 인코딩을 안 고정한다",
            lambda p, r: _tool(r, "talky.py", 'print("한글")\n'),
            "인코딩 미고정",
        ),
        (
            # What the first version missed. The finding message contained
            # that name as the way to fix it, so `lint.py` itself was judged
            # "already fixed". This line is entirely about whether the check
            # counts the call or the name.
            "이름만 문자열에 있고 호출은 없다",
            lambda p, r: _tool(
                r,
                "sneaky.py",
                'print("고치려면 sys.stdout.reconfigure(encoding=\'utf-8\') 를 써라")\n',
            ),
            "인코딩 미고정",
        ),
        (
            # The judgement moved from a Korean adnominal ending to a span cut
            # in half. A `the` at the end of a line is ordinary English
            # typesetting, and that version flagged 437 of 1,515 pairs — 29%
            # is not a check, it is noise that gets switched off. The samples
            # had to move with it.
            "주석의 코드 스팬이 줄바꿈에 잘린다",
            lambda p, r: _clean_tool(
                r,
                "wrapped.py",
                "# Fitting the width cut the span: `subprocess.run(cmd,\n"
                "# check=True)` is one span and now it is two.\n",
            ),
            "끊긴 줄바꿈",
        ),
        (
            # Page prose is read by a different path — front matter skipped,
            # tables and code fences filtered out. If that path quietly reads
            # nothing the check stays green, and that green looks like
            # evidence the check ran.
            "페이지 산문에서 숫자와 단위가 갈린다",
            lambda p, r: p["operator/a"].update(
                extra="\nMeasuring the corpus gave a median across the 21\npages already here."
            ),
            "끊긴 줄바꿈",
        ),
        (
            # A comment written in Korean. This check exists because the
            # completion claim was asserted from a regex over `#` lines that
            # counted no docstring at all.
            "주석이 한국어로 적혀 있다",
            lambda p, r: _clean_tool(r, "korean.py", "# 이 주석은 한국어 산문이다.\n"),
            "주석이 한국어다",
        ),
        (
            # The same, in a docstring rather than a comment. A regex on `#`
            # sees nothing here, which is exactly how 104 lines stayed under a
            # green gate.
            "docstring 이 한국어로 적혀 있다",
            lambda p, r: _clean_tool(
                r, "korean_doc.py", 'def f():\n    """이 설명은 한국어다."""\n',
            ),
            "주석이 한국어다",
        ),
        (
            # The tool checks walk the pipeline folders too. Reading only
            # `tool/*.py` let a moved file drop out of them without a word.
            "하위 폴더의 한국어 주석",
            lambda p, r: _clean_tool(r, "wiki/korean.py", "# 이 주석은 한국어 산문이다.\n"),
            "주석이 한국어다",
        ),
        (
            "파이프라인이 다른 파이프라인을 부른다",
            lambda p, r: _clean_tool(r, "wiki/a.py", "import translate\n"),
            "파이프라인 경계",
        ),
        (
            # Most imports here are deferred into functions. A check that read
            # only the top of the file would pass them.
            "함수 안에서 부른다",
            lambda p, r: _clean_tool(r, "wiki/a.py", "def f():\n    from agent import chat_session\n"),
            "파이프라인 경계",
        ),
        (
            # The root is where the mains live. Through it a pipeline reaches
            # any other pipeline.
            "파이프라인이 루트 모듈을 부른다",
            lambda p, r: (_clean_tool(r, "apply.py", ""), _clean_tool(r, "wiki/a.py", "import apply\n")),
            "파이프라인 경계",
        ),
        (
            # With the repository root on the path, as under pytest, `tool.`
            # spells the same module. Review round 1 got through with it.
            "tool. 접두어로 다른 파이프라인",
            lambda p, r: _clean_tool(r, "wiki/a.py", "from tool import translate\n"),
            "파이프라인 경계",
        ),
        (
            "tool. 접두어로 루트 모듈",
            lambda p, r: (_clean_tool(r, "apply.py", ""), _clean_tool(r, "wiki/a.py", "import tool.apply\n")),
            "파이프라인 경계",
        ),
        (
            "from tool.<파이프라인> import",
            lambda p, r: _clean_tool(r, "wiki/a.py", "from tool.translate import x\n"),
            "파이프라인 경계",
        ),
        (
            # Two dots from one folder down climb out to the root.
            "상대 import 로 루트까지 올라간다",
            lambda p, r: _clean_tool(r, "wiki/a.py", "from .. import translate\n"),
            "파이프라인 경계",
        ),
        (
            # `tool/main/` is a main too, and a pipeline reaching into it
            # reaches every pipeline it weaves.
            "파이프라인이 main 을 부른다",
            lambda p, r: (_clean_tool(r, "main/app.py", ""), _clean_tool(r, "wiki/a.py", "from main import app\n")),
            "파이프라인 경계",
        ),
        (
            "common 이 파이프라인을 부른다",
            lambda p, r: _clean_tool(r, "common/c.py", "import wiki\n"),
            "파이프라인 경계",
        ),
        (
            "메인이 __all__ 밖을 부른다",
            lambda p, r: _surface(r, "import translate\ntranslate._ask()\n"),
            "공개 진입점",
        ),
        (
            "별칭으로 __all__ 밖",
            lambda p, r: _surface(r, "def f():\n    import translate as T\n    return T.protect\n"),
            "공개 진입점",
        ),
        (
            # The program's main is a package. Reading only `tool/*.py` let it
            # use anything.
            "tool/main/ 이 __all__ 밖을 부른다",
            lambda p, r: (_clean_tool(r, "translate/__init__.py", SURFACE),
                          _clean_tool(r, "main/app.py", "import translate\ntranslate._ask()\n")),
            "공개 진입점",
        ),
        (
            "from 으로 __all__ 밖",
            lambda p, r: _surface(r, "from translate import protect\n"),
            "공개 진입점",
        ),
        (
            "하위 모듈을 부른다",
            lambda p, r: _surface(r, "from translate.x import y\n"),
            "공개 진입점",
        ),
        (
            "tool. 접두어로 __all__ 밖",
            lambda p, r: _surface(r, "from tool import translate as T\nT._key\n"),
            "공개 진입점",
        ),
        (
            # Deferred from PR #4: `import tool.translate` binds `tool`.
            "import tool.x 로 __all__ 밖",
            lambda p, r: _surface(r, "import tool.translate\ntool.translate._ask()\n"),
            "공개 진입점",
        ),
        (
            "import tool 로 __all__ 밖",
            lambda p, r: _surface(r, "import tool as t\nt.translate._ask()\n"),
            "공개 진입점",
        ),
    ]

    print(f"\n결함을 하나씩 심는다 ({len(checks)}건)\n")
    failed = [label for label, mutate, expect in checks if not run(label, mutate, expect)]

    # Does declaring it let it pass — the heart of how contradictions work
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pages = {name: dict(spec) for name, spec in CLEAN.items()}
        pages["operator/a"].update(triggers='["같은말"]', links="[]")
        pages["craft/b"].update(triggers='["같은말"]', links="[]")
        build(root, pages)
        text = (root / "operator" / "a.md").read_text(encoding="utf-8")
        (root / "operator" / "a.md").write_text(
            text.replace("links: []", "links: []\nconflicts_with: [craft/b]"),
            encoding="utf-8",
        )
        after = kinds(root)
    passed = "빠진 연결" not in after
    print(f"\n  {'통과 ' if passed else '실패 '} 선언하면 지나가는가        → {sorted(after) or '없음'}")
    if not passed:
        failed.append("선언 무시")

    # What a pipeline may import stays green: itself, relatively or by name,
    # `common`, and the standard library.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, CLEAN)
        _clean_tool(root, "apply.py", "")
        _clean_tool(root, "wiki/a.py", "import re\nfrom . import b\nfrom .b import c\nimport wiki.match\nfrom common import c\n"
                    "from tool import wiki\nimport tool.common\n")
        _clean_tool(root, "wiki/sub/d.py", "from .. import match\nfrom ..match import x\nfrom .. import translate\n")
        _clean_tool(root, "common/c.py", "import json\nfrom . import d\n")
        crossed = [msg for kind, msg in kinds_and_messages(root) if kind == "파이프라인 경계"]
    print(f"  {'통과 ' if not crossed else '실패 '} 허용된 import 는 안 잡는다  → {crossed or '없음'}")
    if crossed:
        failed.append("경계 오탐")

    # The exported names stay green, tests may look inside, and a pipeline
    # without `__all__` is not read yet.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, CLEAN)
        _surface(root, "import translate\nfrom translate import KO_EN\ntranslate.translate([], KO_EN, 0)\n"
                       "import tool.translate\ntool.translate.translate([], KO_EN, 0)\n")
        _clean_tool(root, "test_x.py", "import translate\ntranslate._ask()\n")
        _clean_tool(root, "wiki/__init__.py", "")
        _clean_tool(root, "other.py", "from wiki import match\nimport wiki\nwiki.anything\n")
        leaked = [msg for kind, msg in kinds_and_messages(root) if kind == "공개 진입점"]
    print(f"  {'통과 ' if not leaked else '실패 '} 공개 이름은 안 잡는다        → {leaked or '없음'}")
    if leaked:
        failed.append("진입점 오탐")

    # Citing Korean is not writing Korean, and the backtick is what says so.
    # A check that stops correct work is the one that gets switched off, so
    # the false-positive side is asserted as hard as the true-positive side.
    # The doubled span is here and not in `lint.py`'s own comment, because
    # written there this check reads it and goes red on itself. It is how
    # CommonMark writes a citation that contains a backtick, and a one-
    # backtick regex erased its two opening marks as an empty span and left
    # the Korean standing in the open.
    tick = chr(96)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _clean_tool(root, "cited.py", f"# The marker {tick}왜.{tick} is parsed.\n")
        _clean_tool(root, "run.py", f"# The marker {tick*2}왜.{tick*2} is parsed.\n")
        _clean_tool(
            root,
            "cited_doc.py",
            f'def f():\n    """English first.\n\n    It cites {tick}왜.{tick} and stops.\n    """\n',
        )
        quiet = korean_prose(root)
    print(f"  {'통과 ' if not quiet else '실패 '} 백틱 인용은 안 잡는다        → {quiet or '없음'}")
    if quiet:
        failed.append("인용 오탐")

    # A quotation mark is punctuation, not a marker, so pairing two of them
    # is a guess. Three rounds added members to a set of quote characters and
    # the fourth found the guess going wrong the expensive way: an unclosed
    # `"` pairs with a later one and the Korean between the two disappears
    # from the gate. Every one of these has to be caught.
    #
    # The HTML lines are here because a version that gathered the text of
    # everything the parser did *not* call a code span had to decide what
    # every other token kind contributes, and decided `html_block` wrong: a
    # `<div>` around a Korean line hid it from the gate outright. Reading a
    # parse out token kind by token kind is the hand-written lexer returning
    # through the parser's own door. The line is kept whole now and only the
    # spans are taken away, so a token kind nobody thought about cannot hide
    # anything.
    unmarked = {
        "double quotes": '# The marker "왜." is parsed.',
        "single quotes": "# The marker '왜.' is parsed.",
        "an apostrophe": "# It doesn't parse 왜. and won't either.",
        "an HTML block": "# <div>한국어 산문이다.</div>",
        "an HTML tag inline": "# <span>한국어 산문이다.</span>",
        "an image's alt text": "# ![한국어 산문이다.](x)",
        "a fence marker": f"# {tick * 3}한국어 산문이다.",
        "a table row": "# | 한국어 산문이다. |",
        "indented under the marker": (
            'def f():\n    """English first.\n\n        한국어 산문이다.\n    """\n'
        ),
        "indented behind a `#`": "#     한국어 산문이다.",
        "one of two, the other cited": f"# {tick}왜{tick} 왜.",
        "an unclosed quote": (
            'def f():\n'
            '    """The output starts with " but never closes it.\n'
            '    한국어 산문이다.\n'
            '    Later it names "done" as a separate token.\n'
            '    """\n'
        ),
        "a span cut by a line wrap": (
            f"# Fitting the width cut the span: {tick}왜.\n"
            f"# 그리고 다음 줄{tick} was one span.\n"
        ),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for n, (label, source) in enumerate(unmarked.items()):
            _clean_tool(root, f"unmarked{n}.py", source)
        caught = {at.split(":")[0] for at, _line in korean_prose(root)}
    ok = len(caught) == len(unmarked)
    print(f"  {'통과 ' if ok else '실패 '} 표지 없는 한국어는 다 잡는다 → {len(caught)}/{len(unmarked)}")
    if not ok:
        failed.append("표지 없는 한국어 미탐")

    # The line number names the line the Korean is on — not the `def` above
    # the docstring, and not a line that exists only after the literal is
    # evaluated. A docstring is read as source for exactly this reason: `\n`
    # written as an escape is one line on the page and two in the value, and
    # two implicitly joined literals are two lines on the page and one in the
    # value. Both pointed the reader somewhere they had to go looking.
    slash = chr(92)
    where = {
        "a plain docstring": (
            'def f():\n    """English first.\n    한국어 산문이다.\n    """\n', 5,
        ),
        "an escape, not a line": (
            'def f():\n    """English' + slash + 'n한국어"""\n', 4,
        ),
        "two literals joined": (
            'def f():\n    ("English "\n     "한국어")\n', 5,
        ),
        "a parenthesis on the line above": (
            'def f():\n    (\n        """한국어 산문이다."""\n    )\n', 5,
        ),
    }
    lines = []
    for label, (source, line) in where.items():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _clean_tool(root, "where.py", source)
            found = korean_prose(root)
        lines.append((label, [at for at, _text in found] == [f"tool/where.py:{line}"]))
    ok = all(hit for _label, hit in lines)
    print(f"  {'통과 ' if ok else '실패 '} 발견이 그 줄을 가리킨다     → "
          f"{[label for label, hit in lines if not hit] or f'{len(lines)}/{len(lines)}'}")
    if not ok:
        failed.append("발견 줄 번호 어긋남")

    # Without the parser the check cannot run, and a gate that cannot run a
    # check says so in its report and finishes the rest. Raising took the
    # header, the findings already gathered and the reason with it and left a
    # traceback — red, but silent about what was examined.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, CLEAN)
        real, markdown_emphasis.parser = markdown_emphasis.parser, lambda: None
        try:
            reported = [msg for kind, msg in kinds_and_messages(root)
                        if kind == "주석이 한국어다"]
        finally:
            markdown_emphasis.parser = real
    ok = len(reported) == 1 and "markdown-it-py" in reported[0]
    print(f"  {'통과 ' if ok else '실패 '} 파서가 없으면 보고하고 계속한다 → {reported or '없음'}")
    if not ok:
        failed.append("파서 부재가 보고 안 됨")

    print()
    if failed:
        print(f"검사 {len(failed)}건이 심은 결함을 못 잡았다: {failed}")
        return 1
    print("검사가 전부 자기 결함에서만 빨개진다.")
    return 0


if __name__ == "__main__":
    shutil.rmtree  # noqa: B018  (tempfile does the cleaning up)
    raise SystemExit(main())
