---
scope: craft
severity: landmine
repeat: rule
triggers: ["async|비동기", "이벤트\\s*루프|event\\s*loop", "커넥션\\s*풀|연결\\s*풀|connection\\s*pool", "클라이언트(를|가|는|도)?\\s*(만들|들이|공유|캐시|닫|재사용)", "리팩터|리팩토링", "SDK|httpx|AsyncOpenAI", "라이프\\s*스팬|lifespan|생명\\s*주기", "누수|leak"]
slots: []
sources: []
sources_withheld: true
links: [screen-ownership-before-wiring, diagnose-from-what-ran, comments-carry-why, verify-narrow-then-wide, gate-the-exit-not-the-callers]
---

# Bringing in a client means designing creation, sharing, closing and ownership

Rule. When a refactor brings a resource holding a pool or a connection into
the codebase — an SDK client, a session, a handle — answer four questions
**within the same change**. Do not answer one and push the rest to the next
round. Creation: who makes it, in which context — inside the running loop or
outside it. Sharing: what key hands it out, and does that key carry the owner.
Closing: who closes it, where, under what name, and how a failure to close is
noticed. Ownership: whose resource it is, and who collects what is left when
the owner dies. "It is not shared" is a fine answer. Hang the checks on an
object that actually holds the resource, in the same context production
creates it.

| Question | What has to be decided |
| --- | --- |
| Creation | Who makes it, **in which context**. Inside the running loop or outside it |
| Sharing | What key hands it out. Does that key **carry the owner** |
| Closing | Who closes it, where, **under what name**. How is a failure to close noticed |
| Ownership | Whose resource is it. When the owner dies, who collects what is left |

Each answer decides the next. A connection pool belongs to the loop that made
it, so closing decides ownership and ownership decides sharing. Answered one
at a time, every answer invalidates the one before. "It is not shared" is a
fine final answer — answering four questions is not the same as building four
mechanisms.

What goes wrong. Review finds one adjacent face per round, and this round's
repair becomes the next round's defect. Through all of it the offline suite
stays green.

## A check that does not hold the real resource cannot see this family

So half of this rule lives in the checks. Hang them on an object that actually
holds the resource, in the same context production creates it. Trusting green
without confirming it is the failure [[diagnose-from-what-ran]] wrote down
about red.

## The page beside it

Both pages say one thing: ownership is not a layer added later, it is an
answer decided as the resource comes in.

The same shape appears when stopping something from going out. Put the gate on
each caller and review finds one remaining exit per round —
[[gate-the-exit-not-the-callers]].

Change a contract and the comment describing it is part of that change —
[[comments-carry-why]].
