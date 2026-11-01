---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# overview/ — the Korean introduction site (story, five pipeline pages, usage, install)

## What it is for

A static Korean site for a developer arriving from a community link. It is
published at `littleeeeebit.github.io/wiki-agent/` and linked from the
README (`README.md:14-16`, `README.md:22-26`). `overview.html` at the
repository root is the story page. `overview/` holds the five pipeline
pages (`wiki`, `task`, `review`, `ask`, `improve`), `guide.html` (usage),
`install.html` (install) and the shared `site.css`
(`overview.html:18-27`, `DESIGN.md:295-304`). The screenshots are WebP
files in `overview/img/`.

## Entry points

- `overview.html` is the root. Every page has the same top bar with eight
  links (`overview.html:16-30`).
- `overview/site.css` is the one stylesheet. Its header comment points to
  `DESIGN.md` "Overview site" for the reasons behind its values
  (`overview/site.css:1-3`). The two-column split is `--split`
  (`overview/site.css:17-18`).
- There is no build, no script and no bundler. The pages are plain HTML.
  Fonts come from Google Fonts and the Pretendard CDN
  (`overview.html:8-12`).

## Depends on, depended on by

- It depends on `DESIGN.md:293-340`: page set, layout, type, colour and
  voice. The accent `#3f6b8f` is the app's, picked by the user on
  2026-10-26 (`DESIGN.md:337-340`, `overview/site.css:9`).
- It depends on the English sources of the install page.
  `docs/chat-setup.md:6-8`, `docs/hooks-setup.md:7` and
  `docs/mobile.md:9-12` each say that `overview/install.html` is their
  Korean copy, and that a change to the people-facing steps there changes
  that page in the same pull request.
- It is depended on by `README.md`, whose documentation tables link to these
  pages for every people-facing topic (`README.md:100-110`).
- No code under `tool/` or `web/` reads it.

## What trips a newcomer

- This is the only part of the repository written in Korean for people. It
  is not translated from English. The tone is polite plain Korean (해요체),
  and jargon is explained where it first appears (`DESIGN.md:321`).
- Editing `docs/chat-setup.md`, `docs/hooks-setup.md` or `docs/mobile.md`
  without updating `overview/install.html` breaks a stated contract
  (`docs/chat-setup.md:6-8`). No check catches it.
- Usage and install never share a page (`DESIGN.md:305-311`, the user's
  call, 2026-10-28).
- Only real material goes in: real captures from a demo server, real hook
  output, real review rounds. An example that is not a verbatim record is
  marked 예시 (`DESIGN.md:322-326`).
- The layout is PC first, for 16:10, 16:9 and 21:9. It collapses to one
  column under 960px (`DESIGN.md:312-320`, `overview/site.css:1-3`). The
  earlier single centred column was rejected (`DESIGN.md:318-320`).
- Images carry explicit `width`/`height` and Korean `alt` text, for example
  `overview/guide.html:63`. Keep both when replacing a capture.
