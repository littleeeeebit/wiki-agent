---
scope: operator
severity: contract
repeat: rule
triggers: ["\\A(?!\\s*<)[\\s\\S]*?(/compact|compact.{0,10}(해|할|하기|전|문턱|window)|퇴근|토큰.{0,8}(아끼|절약|절감|많이|줄)|자리.{0,6}(비우|비울))"]
slots: []
sources: []
sources_withheld: true
links: [hooks-fail-open, measure-after-the-last-change]
---

# Compact before stepping away, and know where the session compacts

Rule. When the person says they are leaving a session for more than an hour,
tell them to run `/compact` first. The prompt cache lives for one hour, and a
session that comes back cold writes its whole context again — up to 967K
tokens measured — where a compacted one writes about 55K. This PC compacts on
its own at 400K tokens: Claude's `autoCompactWindow` and Codex's
`model_auto_compact_token_limit`, both set by
`python tool/setup_agents.py --compact-window <tokens>`, which is also how
the value changes. In a repository whose `.wiki/adapter.toml` sets
`keep_alive`, the search daemon's ping is the utterance
`keep-alive — reply "ok" and nothing else.` — answer `ok` and nothing else.

What goes wrong. A session left over lunch and picked up again costs as much
as the whole conversation so far, once, and nothing on screen says so. A
session that never compacts reads its whole context on every turn: at 900K,
90K tokens of cache read per turn.

## The arithmetic behind the two numbers

Measured on 2026-09-25 in the public copy (`ai-coding-agent-wiki-public`,
whose history this repository carries) by replaying its and ai-nara-shop's
trajectories. Tokens at the API's cache rates (read 0.1, write 2) stand in
for the subscription limit, whose own weighting is not published.

The summary size is the median context of the first response after a real
compact: 59K in the public copy, 54K in ai-nara-shop. It includes the system
prompt and the tools, which every turn carries anyway.

| W | Public copy: sessions / added compacts / net | ai-nara-shop |
| ---: | --- | --- |
| 200K | 10 / 37 / 128.2M | 29 / 88 / 189.3M |
| 300K | 6 / 18 / 111.6M | 19 / 42 / 156.6M |
| 400K | 5 / 13 / 92.4M | 15 / 27 / 130.7M |
| 600K | 3 / 5 / 67.7M | 8 / 10 / 89.0M |

400K was chosen: about 70% of 200K's saving for a third of its added
compacts. What a summary drops cannot be counted, so the added compacts are
the number to watch.

## Why keep-alive is opt-in per repository

A ping is a short turn that reads the context once (0.1C) so the cache
survives another hour. It pays only when the person comes back after an hour.
A session that ends without a return loses every ping.

| Cap | Public copy, net | ai-nara-shop, net |
| ---: | ---: | ---: |
| 1 | −396,066 | 6,748,666 |
| 2 | −792,131 | 8,901,757 |

Harness utterances left out, gaps measured from the agent's last response.
The public copy had two returns after an hour in its whole history, so every
ping is a loss; ai-nara-shop had 52. A repository opts in with
`keep_alive = 2`. The daemon's side is `tool/search/daemon.py` (`Keeper`),
the hooks' side `tool/keepalive.py`. Anything uncertain — a restarted daemon,
a screen that is not an empty input box — sends nothing, the same direction
[[hooks-fail-open]] takes. The ping is in `workspace.INJECTED`, so no count
of a person's utterances takes it for one.

## Why the trigger skips `<`

`compact` alone matched a `diskpart` command and a `<task-notification>`,
and the pattern below it matched two more harness rows carrying shell
commands. A person's utterance does not open with `<`; the harness's own
rows do.
