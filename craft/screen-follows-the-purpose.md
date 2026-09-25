---
scope: craft
severity: contract
repeat: rule
triggers: ["프[런론]트 ?엔드|front-?end", "(?<![A-Za-z])design(?:[.]md|ing|s)?(?![A-Za-z])|디자인", "(?<![A-Za-z_-])UI(?![A-Za-z_-])|(?<![A-Za-z_-])UX(?![A-Za-z_-])", "레이아웃|layout|여백|패딩|ai ?slop", "타이포|typograph|폰트|글꼴", "색 ?(배합|조합|감)|팔레트|palette|컬러|color ?(scheme|palette|token)", "대시보드|dashboard|랜딩 ?페이지|landing ?page|상세 ?페이지", "애니메이션|animation|모션|(?<![A-Za-z])motion(?![A-Za-z])|트랜지션", "화면을? ?(짜|그리|만들|다듬|손보)", "Tailwind|shadcn|(?<![A-Za-z])CSS(?![A-Za-z])|스타일링"]
slots: []
sources: []
sources_withheld: true
links: [screen-ownership-before-wiring, ask-with-arrow-key-options, do-the-whole-instruction, run-inside-this-session]
---

# A screen follows its purpose, and polish follows an order

Rule. Asked to build a screen,
**do not start with a detail page and a dashboard.**
First write one line saying who comes here and what for, and let that line
decide what goes on one screen and what waits. Once the sketch stands, walk
the order below with the `design-pass` skill: a screen list that came out of
that line, against `DESIGN.md`; padding, width, alignment and working buttons,
answered with measurements in a browser; typographic hierarchy; colour, handed
to the user as options; and last the whitespace left, filled only where there
is a value to put. The order does not change, and the fourth step goes to the
user. Motion is not among these steps: building or fixing movement skips this
process and goes straight to emil's skills; a still screen goes to jakub's.

| # | What is examined | With | Passes when |
| --- | --- | --- | --- |
| 1 | A screen list that fits this project's purpose | `DESIGN.md` · `better-layout` | The list came out of that one line |
| 2 | Are padding, width and alignment consistent; do the buttons fire | A browser (`claude-in-chrome`) | Answered with measurements, not with eyes |
| 3 | Typographic hierarchy | `better-typography` | The steps can be counted and no two share a use |
| 4 | Colour | `better-colors` → to the user, as options | The user chose |
| 5 | The whitespace left | Typography · infographics | Only places with a value to put there were filled |

The steps themselves are held by the `design-pass` skill. This page holds why
that order.

What goes wrong. The unit of reverting is a screen. Built without settling the
purpose, a detail page and a dashboard appear and no screen shows what the
project is for. Change the order and an earlier step gets redone — choose the
colour first and it is chosen again when the layout moves; fill the whitespace
first and what was whitespace is no longer knowable.

## Why that order

- Step 2 comes before step 3. Hierarchy can only be judged with the screen
  actually standing. Adjusting sizes while the alignment is off makes it
  impossible to tell what is wrong. And this step is judged on values, not
  impressions — "looks consistent" is not a judgement.
- Step 4 is the user's turn. Colour is taste and it is brand. An agent that
  picks one has the user pick again, so build candidates and hand them over
  with [[ask-with-arrow-key-options]]. Each option states the consequence, not
  the label — what comes forward and what recedes.
- Step 5 is last. Filling happens after confirming something is empty. And
  where there is nothing to put, nothing goes in. A number invented to fill
  space is not an infographic.

Do all five. Do not wait to be told one at a time —
[[do-the-whole-instruction]].

## Which skill — the use decides

| Work | Skill | Whose |
| --- | --- | --- |
| Grouping · alignment · reading order · progressive disclosure | `better-layout` | jakub |
| Type scale · wrapping · truncation | `better-typography` | jakub |
| Palette · semantic tokens · contrast | `better-colors` | jakub |
| Radius · optical alignment · surface depth · hit areas | `better-ui` | jakub |
| Accessibility · keyboard · ARIA | `better-accessibility` | jakub |
| Button text · error copy · empty states | `better-writing` | jakub |
| All six at once | `better-interface` · `interface-review` | jakub |
| Building new motion | `animate` | emil |
| Judging or auditing existing motion | `review-animations` · `improve-animations` | emil |
| Finding where motion belongs | `find-animation-opportunities` | emil |
| The judgement behind polish | `emil-design-eng` · `apple-design` | emil |
| Putting several options side by side | `prototype` · `variant` | Both |

One boundary: a still screen is jakub, anything that moves is emil.

Motion is not among these five steps. Building or fixing movement skips this
process and goes straight to emil — it is different work.

## The values belong to `DESIGN.md` — this page holds only the shape

What is written here is the order and the criteria. The actual colours, fonts
and spacings differ per project, and writing them onto this page makes them
wrong on the next one. The same reason slots exist.

The values belong to `DESIGN.md` at the project root. It is Google Labs' open
format: tokens in the front matter (`colors` · `typography` · `spacing` ·
`rounded` · `components`), and the reasons in the body.

- Pick one — <https://getdesign.md/>
- The format — <https://github.com/google-labs-code/design.md>

If it is missing, make it in step 1; if it exists, read it in step 1. Steps 2
to 5 are all judged against that file. "A different blue on every page" is not
a matter of taste, it means there was nothing to judge against.

## Where this page ends

This far is what a screen shows. Once that screen writes to a server or reads
back from it there are three more things to settle, and
[[screen-ownership-before-wiring]] holds those. Opening a browser takes the
rule from [[run-inside-this-session]] unchanged — inside this cell.
