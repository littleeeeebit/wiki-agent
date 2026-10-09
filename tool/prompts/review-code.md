## Code criteria

The question: does the changed implementation behave correctly? Evidence is
the diff, its behaviour when run, focused tests and the gate results listed
under `Already run`.

| Grade | Criterion |
| --- | --- |
| P0 | Data corruption or loss, security, crash, contract violation |
| P1 | A reproducible correctness defect, a regression, an unsafe state transition, invalid evidence or permissions this PR introduced |
| P2 | A suggestion, style, a follow-up candidate |

Test cleanup is part of the change. A deleted test that still guarded current
behaviour or a live regression is P1. A test this PR made dead or superseded
and left in place is P2.
