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

CODE = re.compile(r"`[^`\n]*`")
HANGUL_WORD = re.compile(r"[가-힣]+")
LATIN_WORD = re.compile(r"[A-Za-z]+")


def language(text: str) -> str:
    """`en` when English words outnumber Korean ones, code spans aside — an
    English sentence keeps the Korean names of Korean things, `[받아들임]`,
    and is English (`operator/english-progress`), as `translate` weighs it
    toward Korean. `ko` otherwise when there is any Korean; `und` with
    letters of another script and no Korean; `en` for code, numbers and paths
    alone: there is nothing to translate."""

    prose = CODE.sub(" ", text)
    korean = len(HANGUL_WORD.findall(prose))
    if korean:
        return "en" if len(LATIN_WORD.findall(prose)) > korean else "ko"
    other = any(c.isalpha() and not c.isascii() and "LATIN" not in unicodedata.name(c, "") for c in prose)
    return "und" if other else "en"
