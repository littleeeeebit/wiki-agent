---
scope: craft
severity: landmine
repeat: rule
triggers: ["마크다운", "markdown", "애스터리스크", "강조", "굵게", "\\*\\*", "문서를? (쓰|만들|생성)", "\\.md 문서"]
slots: []
enforce:
  pretooluse: markdown_emphasis.py
sources: []
sources_withheld: true
links: [comments-carry-why, screen-follows-the-purpose]
---

# Emphasis only works while it is scarce

Rule. One or two bolds per Markdown document, used only where the text reads
wrong without them. Never on a paragraph label, a command, a path, an
identifier or a status name — labels stay plain, the rest take backticks.

When it is broken. Emphasis turns into noise. Bold claims that a phrase
outranks the ones around it, and on every paragraph that claim is false
everywhere, so the reader stops seeing the marking at all. Then the one line
that really did matter goes past with the rest. That is worse than never having
emphasised anything.

## Why this page is on the second rung

It is the kind of rule that should have held as prose, and prose did not hold
it. One session put sixty bolds into four planning documents. Measuring the
21 pages already in the repository gave a median of 11.8% of prose lines and a
maximum of 40.9%, so the habit was learned from the corpus — which is exactly
why the corpus cannot set the threshold.

Two checks carry it, because one cannot. `tool/markdown_emphasis.py` runs
before a write and sees only what the call carries; `lint.loud_emphasis` runs
afterwards and reads the file.

The hook watches every editing tool on both hosts — `Write`, `Edit`,
`MultiEdit` from Claude, `apply_patch` from Codex, with or without a
`functions.` prefix — and only when the target path ends in `.md`. A patch is
judged at its `Move to:` destination, not at the file it came from.

| What it refuses | Whole document | Fragment |
| --- | --- | --- |
| Two or more bolds in one paragraph | yes | yes |
| A bold run holding a line break | yes | yes |
| A bolded paragraph label | yes | no |
| Over 15% of prose lines | yes | no |

`Write` and a patch's `Add File` carry a whole document. An `Edit`, a
`MultiEdit` and a patch hunk carry a fragment, and the last two checks need
surrounding text to be right — a ratio needs the whole document, a label needs
to know a block begins there. Guessing either from a fragment refuses correct
prose, and a hook that refuses correct prose gets switched off.

Each fragment is judged by itself. A `MultiEdit`'s edits and a patch's runs of
added lines are not put next to each other, because whatever separates them in
the real file is not in the call — joining them built a paragraph no file
contains and refused two bolds that were landing in two different paragraphs.

So what a fragment could push over the limit is caught afterwards.
`lint.loud_emphasis` reads every `.md` in the hub, and `repo_lint` runs the
same check in each target repository, which is where `sync` calls it on Stop.
A file it cannot read is itself a finding: skipping one quietly would make
"every `.md` passes" false while the gate stayed green.

## What the counter counts

Whatever CommonMark calls strong emphasis — `**bold**` and `__bold__` alike —
outside fences, tables, front matter and inline code. A parser decides that,
not a regex over the source.

Six rounds of review went into deciding it by hand, and every one of them said
the same thing in a different shape: a tilde fence, a four-backtick fence, an
info string, U+00A0, an escaped backtick, a code span crossing a line break,
`** not bold **`, `foo__bar__baz`. Each fix bought exactly one shape and the
next round found the next one. An inline lexer written a clause at a time
inside a style hook is not a job that ends, and each wrong clause either let
the rule be bypassed or refused correct prose.

Counted per paragraph, never per line. Inline tokens carry no source position,
and reconstructing one by stepping on each soft break is wrong: a code span's
newlines are already spaces by then, so every line it swallowed is lost and
every count after it is off. A block is a unit the parser hands over exactly,
so that is the unit.

A newline the block covers is in one of three places: a break token, a token
that still carries it — an inline tag written across lines keeps its own — or
a code span, which turned it into a space and is the only one that cannot be
counted directly. What is left over after counting the first two is charged to
a bold only when every code span in the block is inside it.

Both of those counts reach the same depth, into nested tokens, and that is not
an incidental detail: they decide together who owns a folded line, so one
reaching deeper than the other leaves a line nobody can own. A multi-line code
span inside an image's alt text was exactly that — its line in the residue,
chargeable to no bold, and chargeable to the wrong one nearby.

A bold holds a line break when a break token is inside it, or when a token
inside it carries one from the source — judged off the same function the
counting uses, so the two cannot drift apart. Naming one token type instead let
a multi-line image be subtracted from the residue and then judged by nothing.
A newline in decoded content is not a source newline: `**a&#10;b**` is one
line, and reading every token's content for one refused it.

An inline tag never joins a label's text, because a label is what the reader
sees. Whether a bold *behind* one opens its block is a question the parse
cannot answer — the token stream for a tag that draws something and one that
draws nothing is identical — so from the first inline tag onward a block is not
judged for labels. Three rounds went into answering it anyway: every tag
invisible refused `<img src=x> **Rule.**`, every tag visible let
`<span>**Rule.**</span>` through, and the void list called
`<input type=hidden>` visible when it renders nothing at all. No page in this
repository uses inline HTML.

The parser is checked where the wiring is written — `apply.py`, which both
documented installs go through — and it probes the interpreter the hooks will
run under, not the one doing the installing. It checks the version floor and
the stdlib that floor brings, not only the package list: swapping the probe for
that list once dropped `tomllib`, and a 3.10 interpreter passed as a target for
hooks that import it.

The command it prints is one a reader can run: absolute, quoted for paths with
spaces, naming the interpreter that will run the hook rather than whatever
`python` resolves to, and prefixed with `&` for PowerShell with a note for the
shells that do not take it. Each of those four was its own review finding, so
one function writes it and both the hook and `apply` print that.

`markdown-it-py` is in `requirements-hooks.txt`, the one file that says what a
hooks install needs and which the chat and dev requirements both pull in. It is
not optional. When it is missing the hook lets the write through and says so on
screen, and `lint` raises it as a finding. Any other way the parse can fail
reports the same way. A check that cannot run and reports nothing reads exactly
like a check that ran and found nothing — that is how a gate stays green with
the rule switched off.

The parse is CommonMark with the table rule on, and nothing else. The
`gfm-like` preset would also turn on linkify, which needs `linkify-it-py` — an
*extra* of markdown-it-py that requiring the package does not install. Without
it that preset builds fine and raises inside `parse`, which is past the check
for a missing parser and into the blanket except that keeps the hook from
stopping the work. It was installed on the machine this was written on only
because an unrelated package wanted it.

## What neither of them refuses

- Whether a given phrase deserves emphasis. That is taste, and a hook that
  argues about taste gets switched off — and then nothing is enforced at all.
- Anything inside a fence, a table or front matter. A bolded table cell often
  works as a label.
- A document with fewer than four bolds or fewer than eight prose lines. A
  ratio over that little says nothing either way.
- A bold inside a table cell — but the table has to be one. A block of
  pipe-shaped lines with no delimiter row is a paragraph, and CommonMark
  renders its pipes literally, so its emphasis counts like any other.
- A label in a block that holds inline HTML before it. Whether the reader sees
  anything where the tag is cannot be read off the parse, and guessing it
  wrong either refuses correct prose or claims a cover this does not have.
- Files that are not `.md`. Emphasis in code comments belongs to
  [[comments-carry-why]].

## The procedure lives in the skill

What to reach for instead — headings, tables, backticks, a rewritten sentence —
is in `skills/write-markdown`. The hook refuses only what is certain; the skill
carries the rest.

The same shape appears on screen. Emphasise everything and the hierarchy is
gone, and a screen without hierarchy cannot say what to look at first —
[[screen-follows-the-purpose]].
