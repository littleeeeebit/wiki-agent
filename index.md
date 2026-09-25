# Index

The agent reads this file first and decides which page it needs. A local
search model serves the wiki chat; it never decides what the hook injects.
`landmine` and `contract` do not even wait for
that — the `UserPromptSubmit` hook reads the utterance and injects them
directly ([`ENFORCEMENT.md`](ENFORCEMENT.md)).

The schema is [`SCHEMA.md`](SCHEMA.md); diagnosis and updating are
[`MAINTENANCE.md`](MAINTENANCE.md). Read them before writing or changing a page.

```
python tool/census.py --project <path> --out raw/census-<name>.jsonl
python tool/intersect.py raw/census-*.jsonl
```

## operator — follows the person

| Page | Severity |
| --- | --- |
| [The Codex review loop](operator/codex-review-loop.md) | `landmine` |
| [The agent writes English, the person reads the mirror](operator/english-progress.md) | `landmine` |
| [Edit files as diffs](operator/edit-files-as-diffs.md) | `contract` |
| [Cleanup after a merge](operator/after-merge-cleanup.md) | `contract` |
| [Ask as options](operator/ask-with-arrow-key-options.md) | `contract` |
| [Do not multiply subagents](operator/agent-delegation.md) | `contract` |

## craft — follows the technique

| Page | Severity |
| --- | --- |
| [Pick up async results immediately](craft/pick-up-async-results.md) | `landmine` |
| [Emphasis is only emphasis when it is scarce](craft/emphasis-is-scarce.md) | `landmine` |
| [Do the whole instruction](craft/do-the-whole-instruction.md) | `contract` |
| [Block the git that cannot be undone](craft/destructive-git-guards.md) | `contract` |
| [A hook never stops the session](craft/hooks-fail-open.md) | `landmine` |
| [Comments carry the why, not the history](craft/comments-carry-why.md) | `contract` |
| [An error names the symptom site](craft/error-names-the-symptom-site.md) | `landmine` |
| [Gate the exit, not the callers](craft/gate-the-exit-not-the-callers.md) | `landmine` |
| [Measure after the value changes last](craft/measure-after-the-last-change.md) | `landmine` |
| [A screen follows its purpose, polish follows an order](craft/screen-follows-the-purpose.md) | `contract` |

A census cannot see a rule that works — the detail is under "there are two
doors into a page" in `SCHEMA.md`.

## skills — repeated instructions set into a procedure

| Skill | What |
| --- | --- |
| [`review-loop`](skills/review-loop/SKILL.md) | One round. Arming the watch and sending are one procedure |
| [`after-merge`](skills/after-merge/SKILL.md) | The seven cleanup steps plus the diagnosis |
| [`retrospect`](skills/retrospect/SKILL.md) | Count where today went wrong, into wiki candidates |
| [`design-pass`](skills/design-pass/SKILL.md) | The five design steps, in an order that cannot move |

### Skills brought in from outside — the wiki decides which

`design-pass` calls two skills that are not ours. What gets called when is
decided by a page, not by a skill, so that the hook is read first.

```
npx skills add jakubkrehel/skills -g -s '*' -y   # still screens
npx skills add emilkowalski/skill  -g -s '*' -y  # anything that moves
```

The boundary and the table are in
[a screen follows its purpose](craft/screen-follows-the-purpose.md).

## The map

The wiki map in `web/` draws this index. Node size is injection cost, colour
is the enforcement layer, and a dotted line is how often two rode together in
a real utterance.

```
python tool/graph.py
```
