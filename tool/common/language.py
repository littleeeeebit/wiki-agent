"""Which language a text's prose is in: `en`, `ko` or `und`.

Shared by `translate`, which decides whether English normalization has
anything to do, and `search`, which labels evidence when no translator was
handed in. A word count, not a language model: it tells Korean prose from
English prose and notices a third script, and it says nothing about whether a
translation kept the meaning.
"""

from __future__ import annotations

import re
import unicodedata

# A fenced block — a screen sketch, a command — or a code span: kept as written, never prose.
CODE = re.compile(r"^```.*?^```|`[^`\n]*`", re.M | re.S)
# A bracketed label: a button or a state, the name of a Korean thing.
LABEL = re.compile(r"\[[^\]\n]{1,40}\]")
HANGUL = re.compile(r"[가-힣]")


def language(text: str, names: tuple[str, ...] = ()) -> str:
    """`ko` when Korean is left as prose. An English sentence keeps the
    Korean names of Korean things (`operator/english-progress`) — in a code
    span, a bracketed label like `[받아들임]`, or one of `names` (the
    glossary's `keep_korean`) — and is still English; any other Hangul is a
    Korean clause, however much English is around it. `und` with letters of
    another script and no Korean; `en` otherwise, code, numbers and paths
    alone included: there is nothing to translate."""

    prose = LABEL.sub(" ", CODE.sub(" ", text))
    for name in sorted(names, key=len, reverse=True):
        if name:
            prose = prose.replace(name, " ")
    if HANGUL.search(prose):
        return "ko"
    other = any(c.isalpha() and not c.isascii() and "LATIN" not in unicodedata.name(c, "") for c in prose)
    return "und" if other else "en"
