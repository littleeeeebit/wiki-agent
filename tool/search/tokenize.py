"""Tokens used by both BM25 indexing and query scoring."""

import re

WORD = re.compile(r"[a-z0-9_]+|[가-힣]+")


def terms(text: str) -> list[str]:
    """English words in lower case, Hangul as overlapping bigrams.

    Both, because the hub's pages are English while a repository's are often
    Korean, and so is the question. Bigrams catch a Korean stem through its
    endings without a morphological analyser.
    """

    out = []
    for word in WORD.findall(text.lower()):
        if "가" <= word[0] <= "힣":
            out += [word[i:i + 2] for i in range(len(word) - 1)] or [word]
        else:
            out.append(word)
    return out
