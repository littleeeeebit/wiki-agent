---
scope: craft
severity: landmine
repeat: rule
triggers: ["missing|not found|_missing", "state_missing", "왜 (안 되|안 나|없)", "어댑터|adapter", "선택자|selector", "DOM", "안 그려", "렌더", "리셋|reset", "건너뛰|스킵|skip"]
slots: []
sources: []
sources_withheld: true
links: [diagnose-from-what-ran, do-the-whole-instruction, verify-narrow-then-wide]
---

# An error names where the symptom appeared, not where the cause is

Rule. When `X_missing` comes back, first find out what the thing looking for X
was actually looking at. Before concluding that X is absent, check that the
lookup was pointed at the target you had in mind — start from the
constructor, setting or argument that decides it, and look at the target
yourself rather than inferring it from code. Suspect a fallback that says "use
the other one if it is missing"; it renames the failure. And if what the
failure blocked was a rule, do not skip the rule and do not route around it.

What goes wrong. **Something that was fine gets fixed.**

## The more expensive half — the blocked rule got skipped

A rule blocked by a tool failure is a defect in the tool, not an exception to
the rule. When [[do-the-whole-instruction]] says to name what was blocked and
why, it does not mean say it and move on.

## The order

1. Check the lookup's target first. Not what it failed to find — where it
   looked. If a constructor, a setting or an argument decides that target,
   start there.
2. Look at the target yourself. Do not infer it from reading code; open that
   screen, that response. [[diagnose-from-what-ran]] says the same thing about
   execution records.
3. Suspect the fallback. A branch that says "use the other one if it is
   missing" renames the failure. A silent fallback is a factory for
   misdiagnosis.
4. Do not route around a blocked rule. A run made that way has to be thrown
   away later, so it does not save the time, it spends it twice.

## What stands beside it

The same shape, a fallback hiding a failure, is in [[hooks-fail-open]] and in
`run-inside-this-session`. All three come down to one sentence: worse than
something not running is something believed to be running.
