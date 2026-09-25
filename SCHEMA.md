# Schema — what this wiki is and how it is shaped

This file is the contract. Read it before writing or changing a page.

So the bottleneck is neither storage nor search. It is that what is written
does not govern behaviour.

This wiki is worth two things and no more.

1. It sets a repeated instruction into a habit — the instruction becomes one
   skill.
2. It turns the rules that can be into checks — the rules actually held were
   not prose, they were guards that threw and gates that went red.

The rest stays as sentences. Mechanise something that needs judgement and
false positives stop the work.

## Three layers (adapted from Karpathy's spec)

| Layer | Owned by | Here |
| --- | --- | --- |
| Raw Sources | The person | `raw/` — immutable measurements taken from session logs, commits and live archives |
| Wiki | The agent | `operator/` `craft/` — pages, woven with links, contradictions marked |
| Schema | Person ↔ agent | This file |

One difference from the original. Karpathy's raw sources are external
documents such as papers and articles; here they are conversation logs and
execution records. So ingesting is not "read and summarise" but "count and
compare".

## Scope — what goes where

This is the most important decision in this wiki. A landmine from project A
leaking into project B is itself an incident. Share nothing, on the other
hand, and the same lesson has to be taught again per project.

| Scope | What it follows | Examples |
| --- | --- | --- |
| `operator` | The person | The review loop protocol · ask as options · edit files as diffs · report every round |
| `craft` | The agent's technique | Reproduce first · doubt a green signal · one witness, one judgement · prove a dead branch before deleting it |
| `project` | The repository | Gate commands · launchers · ports · architectural invariants |

There are no `project` pages in this repository. They live in the target
repository's `.wiki/`, and the hook reads that far. Other people's landmines
do not come in here.

### Only a few pages are hand-written — a listing holds the rest

`corpus` builds a listing of every document and injects it once at session
start. Not the bodies. The agent can open a file.

The choosing is not done by a machine.

This only works with rules and documents separated. Rules are injected by the
hook — the hook runs before the model, and a rule's failure mode is "it was
not read", which must not be left to choice. A document's failure mode is "I
did not know it existed", for which a listing is enough. Two different
problems, two different mechanisms.

### The `project` scope gets machinery too — it did not at first

Putting only rules on the ladder and leaving knowledge as prose was the
mistake. Forgetting does not distinguish a rule from a piece of knowledge.

So the project layer uses the same layer 2. Thinly, though — an authoritative
document is not carried whole. A page carries the few lines that must be in
the context and points at the authority with `reads:`. `lint` checks that path
exists every time.

```markdown
---
scope: project
severity: landmine
triggers: ["왜 (안|못|틀)", "원인", "진단"]
reads: [CLAUDE.md, docs/architecture/pipeline.md]
---
```

### Slots — the shape is shared, the project fills the values

The shape is the same and the values differ. Share the values and both break;
share nothing and the same lesson is learned twice. So a page declares the
blanks and an adapter fills them.

```markdown
---
scope: craft
slots: [env_name, activate_cmd, verify_cmd]
---
Do not trust a bare `python`. Name the environment.
Confirm: `{verify_cmd}` has to work after `{activate_cmd}`.
```

Slot values live in the target checkout's `.wiki/adapter.toml`. They are not
copied to the hub and never written into a page. Only an existing install with
no local adapter falls back to the hub's `adapters/<project>.toml`. The hosts
the team installer chose are recorded in the target's git-excluded
`.wiki/installed-agents.json`. The wiring check prefers that per-machine
expectation, and follows the adapter's `agents` when there is no record.

## The minimum shape of a page

```markdown
---
scope: operator | craft
severity: landmine | contract | preference
triggers: ["regex", ...]      # inject on seeing this utterance. required for landmine/contract
repeat: rule                  # once seen in a session, later turns carry only the Rule paragraph.
                              #   every binding clause must be in it, 1,200 characters at most
slots: []                     # an empty list when there are none
enforce:                      # when it can move into a check. omit otherwise
  deny: ["Bash(sed -i*)"]     #   layer 1. apply merges it into settings.json
  pretooluse: <a script in tool/>  #   layer 4. only when the judgement needs computation
sources: [raw/xxx.md#L12]     # where this page came from
links: [other-page]
---

# Title — what it says, in one line

Rule. One paragraph. Imperative.

Why. What actually burned. In a reproducible form.

What goes wrong. What happens when it is broken.
```

### Severity is a cost, not a preference

| Severity | Criterion | Where it rides |
| --- | --- | --- |
| `landmine` | Breaking it actually burned something (a live run, time, data) | Eligible for injection |
| `contract` | Breaking it makes the product give a wrong answer | Eligible for injection |
| `preference` | Taste and formatting. Broken, the product is still right | Demoted to the index |

This is not "always loaded". Severity only grants *eligibility* to be
injected; the full text rides when that utterance matched the `triggers`. A
`landmine` that does not match does not ride — which is why `triggers` is
required on `landmine` and `contract`.

There is one exception and it too is written as a trigger.
`operator/agent-delegation` is written to match every non-empty utterance.

What `SessionStart` carries is a different path — the document listing, the
plans and a summary of recent decisions, not a page's full text. Counting the
two as one gets both the cost and the hit rate wrong.

`landmine` cannot be applied without grounds. `sources` has to say what it
burned.

### There are two doors into a page

| Door | Grounds | Example |
| --- | --- | --- |
| Repetition | The user retyped it in two or more repositories | The review loop · post-merge cleanup |
| Enforcement | It already stands as a check or a block somewhere, and it travels | Blocking `git reset --hard` |

Without the second door, layer 1 never gains anything. A census counts what
the user retyped, and a rule already blocked produces no retyping. Enforcement
that works erases its own trace.

## A contradiction is not necessarily a fault

Two pages saying different things is one of three cases. Telling them apart is
`lint`'s job, and **fixing before telling them apart erases information.**

| What | Meaning | What to do |
| --- | --- | --- |
| Drift | Two answers to one question. One is stale or wrong | Fix it |
| An intended split | Different questions that look alike | Do not fix it, record it |
| Unresolved | Which one is right is not known yet | Keep both and write that it is unknown |

So a page declares the split. A declared split is not raised by `lint` again.

```yaml
---
conflicts_with:
  - page: craft/other-page
    kind: intended        # intended | unresolved
    why: The two tables answer different questions.
    measured: raw/census-x.jsonl
---
```

`kind: unresolved` is a declaration that it is not yet known. That is a record
too — better than unifying something unknown as though it were known.

`lint` raises only values that differ with no declaration. The declaration is
written by a person, from judgement.

## The workflows

| Name | What | When |
| --- | --- | --- |
| `census` | Counts a target repository's session logs and produces what actually breaks | First thing when attaching a new project |
| `harvest` | Mines "what was chosen and why" out of PR bodies into `.wiki/decisions/` | Once, when attaching a project |
| `corpus` | Builds a listing of every document in the repository | Whenever the documents change |
| `repo_graph` | Lays links over that listing and counts documents nobody points at | When the listing changes. `sync` calls it |
| `sync` | Brings the listing, the decisions and the diagnosis up to the repository's state | Automatic. The `Stop` hook calls it |
| `apply` | Merges a page's `enforce` and the three hooks into the target's `.claude/settings.json` | Whenever a page or a slot changes |
| `trigger_audit` | Measures trigger hits and **how much rides in one turn** | After adding a page or a trigger |
| `lint` | The hub wiki — broken links, orphans, stale claims, undeclared contradictions, missing connections, a `landmine` with no grounds | After a merge · before `apply` · after changing a page |
| `repo_lint` | An attached repository — a stale listing, broken pointers, rules that never ride | Automatic. The `Stop` hook calls it through `sync` |
| `graph` | Emits the policy graph as `graph.json`. The drawing is done by `web/` | Whenever a page changes |

`lint` is not optional, it is the main event. A wiki that is not maintained is
worse than a stale one, because it says wrong things confidently.

When to run them, what to do with the findings, and which layer something new
goes to, are held by [`MAINTENANCE.md`](MAINTENANCE.md).

## What is not done

- No retrieval model in front of the hook. A local one serves the wiki chat's
  search (`tool/search/`) and never decides what the hook injects; the regex
  triggers keep that authority. In the public copy a one-line hint for pages
  the triggers missed was built and measured against recall labels, and no
  threshold was precise enough to switch it on. The four failure sites an
  embedding used to add are closed — no key (a local ONNX model), no network
  (localhost only), no quota, and no stale vectors (each chunk is keyed by its
  text's hash and recomputed when it changes). The one it brings, a daemon
  process, fails to BM25 in the asking process.
- A target repository's knowledge is not brought here. The `project` scope
  lives over there.
- Nothing that needs judgement is mechanised. False positives stop the work.
- No page is written that will not be read. More pages means `apply` carries
  more, and that is precisely the problem this wiki was built for.

## Grounds in the public copy

The original's rules, skills, applicability and severities are kept; only the
actual conversations, cases and measurements are withheld.
`sources_withheld: true` marks a rule whose grounds are private. `lint`
exempts the missing grounds only for existing rules carrying that mark, and it
does not substitute for a new rule's grounds. Installing and signing in with
your own CLI follows the [install guide](docs/chat-setup.md); managing the
public copy follows the [update guide](docs/publishing.md); what was verified
follows the [check record](docs/verification.md).
