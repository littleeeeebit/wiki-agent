---
scope: craft
severity: landmine
repeat: rule
triggers: ["예산|budget|상한|한도", "마감|deadline|timeout|제한\\s*시간", "측정|계측|재는|재고|길이를|크기를|cost", "끼워|삽입|중간에\\s*넣", "단계를?\\s*(추가|삽입|넣)", "번역(을|이)?\\s*(넣|끼|붙)", "파이프라인|pipeline", "수렴(을|이)?\\s*(못|안)", "라운드마다|매\\s*라운드"]
slots: []
sources: []
sources_withheld: true
links: [gate-the-exit-not-the-callers, diagnose-from-what-ran, client-lifecycle-in-one-scope]
---

# Measure after the value changes for the last time

Rule. If a value is checked, recorded or fitted to a budget, that has to
happen after everything that can change it. Insert one step into a pipeline
and every place below it that measured the same value is now measuring a stale
one. So adding a step means counting every place that reads the value it
changes; if one sits above it, move the step up or the read down. When two
review findings land on the same value, follow that value's life to where it
changes last instead of moving the point again.

What goes wrong. The check keeps passing while measuring the wrong thing.
Worse than not measuring — there is a number, so it looks measured.

## Two faces of one rule

Once it was time and once it was length, and the mistake was the same.

| What was measured | Where | What changed the value after that |
| --- | --- | --- |
| Has the deadline passed | The moment the response landed | Restoring placeholders, writing the cache |
| Does the block fit the budget | Before translating | The translation. English is usually longer |

Both put the measuring code where it read well rather than where the value
finished. Just after the response looks like a good place to check a deadline;
just after rendering looks like a good place to measure length. What neither
looked at is that the value keeps moving afterwards.

## What to count when inserting a step

Adding a step to a pipeline means counting **every place that reads** the
value that step changes. If even one of them is above it, move the step up or
move the read down.

This is what happened from not counting. Putting the translation after
rendering broke three things at once.

1. A block `fit` had trimmed to budget at Korean length overflowed again as
   English
2. `trajectory.cost` recorded the pre-translation number
3. `trigger_audit` had written in its own docstring that this number was the
   injected size, so that sentence became false

Moving the translation ahead of rendering fixed all three. What was fixed was
the position of one line.

## An offline replay cannot produce the same unit — so write that down

Fixing the above turned one health check red. The value the hook recorded and
the value the audit tool measured did not agree.

Investigating showed the check meant to measure whether the two measurements
share a unit, and was actually measuring whether the translation ran. The hook
runs as a subprocess, inherits the environment and translates; the audit tool
is offline and does not.

There are two branches here, and writing down which was chosen is part of the
repair.

| The choice | The cost |
| --- | --- |
| Make the audit tool translate too | A round trip per utterance. The value of replaying is gone |
| Document that the audit tool measures the pre-translation size | Comparing the two numbers means allowing for the difference |

The second was chosen. And the check turns the translation off, so the
assertion measures the relationship it meant to. Removing the key alone was
not enough — the cache answers before the key is read.

## When review points at the same value twice

This defect does not show in one round. The first round arrives as "it does
not look at the deadline", and after the fix the next one arrives as "time
passes after that too". Fixing only where the finding pointed moves the point
each time.

The review loop's "fix only where the finding points" is a rule about
**scope**. Applied to depth, it patches the symptom site. When two findings
land on the same value, do not move the point — follow that value's life and
find where it changes last.

The face of this about picking the wrong place to block is held by
[[gate-the-exit-not-the-callers]]. That one is about what goes out, this one
about what gets measured. Both have the same answer: the single place the
value passes through last.
