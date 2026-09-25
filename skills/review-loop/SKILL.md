---
name: review-loop
description: >-
  Run one round of the external code-review loop against an already-open reviewer
  terminal. Use when the user says "리뷰 루프", "review loop", "라운드 N 보내",
  "codex 리뷰", or asks to send/receive a review round. Writes the round
  instruction to a file, arms the result watch BEFORE sending, sends one sentence
  to the terminal, and reads the result from the file — never from the terminal.
---

# One round of the review loop

This skill exists for the ordering. When arming the watch and sending are two
separate steps, the order gets it wrong. Here they are one procedure, so there
is nowhere to get it wrong.

The rule behind it is `operator/codex-review-loop`. That page carries this
repository's values — the review folder (`review_dir`, `artifacts/review/` in
most repositories), the offline gate and the live run. A skill is the same
file in every repository, so those values are read off the injected page, not
written here.

## Steps

### 1. Pick the round number and topic

```
artifacts/review/<topic>-round-<n>.md
```

Scan `artifacts/review/` and take the number after the most recent instruction
that has no `-result.md` yet. Do not reuse a previous round's path — a new
result would overwrite the old one.

### 2. Write the instruction

What it must contain, in this order.

- The result file's path, in the first section. "Write to this file, say
  nothing on the terminal, and if that file is not written this round did not
  happen."
- Worktree protection as an *allow* list. Written as prohibitions first, a
  prohibition swallows the exception and the review ends up on screen only.
  - Allowed: reading · `git log`/`show`/`diff` · `gh pr view`/`diff` ·
    running tests · writing that one result file
  - Forbidden: `checkout`, `switch`, `stash`, `merge`, `rebase`, `reset`,
    `cherry-pick`, `commit`, `push` · installing packages · every edit other
    than that result file
- What changed since the last round. The commit range and the size confirmed
  with `git diff --shortstat`.
- What has already been run. Do not make them derive it again.
- What became of each earlier finding, including what was rejected and why.
- That a P2 already under `Deferred P2` is not reported again without new
  grounds or a severity change.
- The report format and the grades.

  ```
  [P0|P1|P2] file:line — what / when / why
  ```

  | Grade | Criterion |
  | --- | --- |
  | P0 | Data corruption or loss, security, crash, contract violation |
  | P1 | A correctness defect, a regression, wrong behaviour this PR introduced |
  | P2 | A suggestion, style, a follow-up candidate |

- An instruction not to invent findings without grounds. With nothing wrong,
  one line: `새 발견 없음`.
- A last line of `머지 허용` or `머지 불가 — <reason>`.

Check before sending.

- Did you measure the size with `git diff --shortstat` and write the real number
- Did you write what became of each finding from the last round, refusals and
  reasons included
- Did you write what has already been run, so it is not derived again

### 3. Arm the watch first — never move this step after step 4

```
Monitor: a *-result.md under artifacts/review/ newer than the send
```

- Watch the directory, not one path. If the reviewer writes under a neighbouring
  name, polling a single path never finishes. A session that answered an
  earlier round sometimes reuses the previous path — and then the new result
  overwrites the old one.
- Poll at one second. These are local files.
- Do not end the turn after arming it. Do the next step in the same response.

### 4. Send one sentence

```
orca terminal send --terminal <handle> --text '그 파일을 읽고 리뷰하라' --enter
```

Look the handle up again on every send — it goes stale when the reviewer
restarts. Never open a new window.

### 5. Read the result when it lands

Read it from the file. Do not scrape the terminal. Check that the first line
names that round; if it does not, that session reviewed whatever it had been
looking at instead of reading the instruction.

### 6. Reproduce a finding before treating it as fact

- P0 and serious P1: reproduce first. Fix what reproduces, and write down what
  does not.
- When fixing, check three things:
  1. Remove each judgement one at a time and see which test goes red
  2. Whether this is a false green measured before the response arrived
  3. Whether you counted every place that rule has to apply
- Fix only where the finding points. Fixing broadly breaks something else.
- If you disagree, write the evidence and ask back.
- Do not fix P2. Record it under `Deferred P2` at the bottom of the round file.

When there are several ways to repair it, hand the choice to the user — with
arrow-key options, not prose (`operator/ask-with-arrow-key-options`). Put the
consequence in each option, not a label: what it costs and what it rules out.

### 7. Gate, then live, then commit, then push

The offline gate and the live run are the page's `gate_cmd` and `live_cmd`.
Carry the live result in the next instruction.

Push without asking. Twenty rounds piled up locally leave the PR looking
exactly as it did before the review started, and from outside that is
indistinguishable from abandoned work.

### 8. Next round

Write what changed, the mutation results, new `Deferred P2` entries, and whether
any existing P2 was promoted, into the next instruction. Then go back to step 1.

## When the rounds stop shrinking — sort by family first

A similar number of findings each round is not a relentless reviewer; it is a
signal that the repairs are not reaching the cause. Before sending the next
round, pair up the findings so far into a table. One row is
`finding → the repair made → what came back next round`.

A raw list does not show it. The families only appear once they are paired.

| Family | How to recognise it | What to do |
| --- | --- | --- |
| The wrong model | Findings cluster in one file and every one is "it does not look at this input either" | Re-decide what that code models — `craft/gate-the-exit-not-the-callers` |
| Where it measures | Two findings arrive about the same value (time, length, cost) | Follow the value's life and find where it changes last — `craft/measure-after-the-last-change` |
| The claim is wrong | The code is right and the commit message, comment or test is written too broadly | Fix the sentence and the test, not the code |
| Knew and skipped | Something written under "what I suspect" in the instruction comes back as a finding | Having written the suspicion, reproduce it in that round |

The first two are what stops convergence. The other two close in one go.

In one real PR, eleven findings had four causes. Five were faces of one model,
three were where it measured, and the last two were the other two families.
Only two were genuinely new defects the repairs had created — the rest were
patches on symptom sites.

"Fix only where the finding points", in step 6, is a rule about scope, not
about depth. Not fixing broadly and fixing shallowly are different things, and
applying that sentence to depth is what turned that PR into three more rounds.

## When to stop

Stop when there are no new findings at P0 or serious P1 and every test passes.
Leftover P2 entries do not prevent stopping.

When it ends, go through `Deferred P2` once and leave only what is worth doing,
as a single PR comment. Discard minor style and taste. `artifacts/` is scratch
space, not a permanent record.
