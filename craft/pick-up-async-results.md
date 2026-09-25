---
scope: craft
severity: landmine
repeat: rule
triggers: ["리뷰\\s*루프", "review\\s*loop", "기싸움", "안 ?받", "안 ?읽", "왜 안", "감시", "폴링", "자동으로 (감지|받|보내)", "라운드\\s*\\d+\\s*(결과|보냈|왔)"]
slots: []
sources: []
sources_withheld: true
links: [codex-review-loop, declared-continuation, verify-narrow-then-wide, report-without-stopping]
---

# An async result is picked up the moment it lands

Rule. When sending something that will answer back, arm the watch before
sending, and do not end the turn between the send and the watch. The watch
looks at a directory, not at one path. Do not invent an end condition — a
background task is finished when its completion notice says so, not after a
number of turns or when the output stops. Match the interval to what is being
waited on: a second for a local file, thirty for a remote API.

What goes wrong. The other side answered and nobody read it. The user has to
find that and say so — which is the whole value of the automation, gone.

## How to hold it

- Arm the watch first. Armed after the send, it misses whatever arrived in
  between.
- Send in the same response. End the turn between send and watch and the next
  turn only starts when the user pushes it. Where else that "only starts when
  the user pushes it" comes from is held by [[report-without-stopping]] — the
  place where reporting replaces progress.
- Watch the directory. If the other side writes a file under a neighbouring
  name, polling one path never finishes.
- Do not invent an end condition. A background task is finished when the
  completion notice says so. Hard-coding a number of turns, or deciding from
  "the output stopped", is wrong.
- Match the interval to what is being waited on: a second for a local file,
  thirty for a remote API.

So this page is half a solution. The other half is making the watch itself a
template, so that sending is impossible without it.
