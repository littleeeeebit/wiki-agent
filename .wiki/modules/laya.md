---
scope: project
severity: preference
triggers: []
slots: []
sources: []
links: []
---

# laya/ — data, labels, training and the error notebook for the Laya decision models

## What it is for

This folder holds everything that turns production Jev decision requests
into per-kind fine-tuned Laya (ModernBERT) models: pooling states, reserving
the gate test, generating and labelling candidates, training, the RRSI
improvement loop and the error notebook (`laya/README.md:1-5`). It does not
serve the models. Serving is planned for `tool/decision`
(`docs/plans/laya/0-overview.md:23`). Plan and status are in
`docs/plans/laya/0-overview.md:61-66`. Measured results are in the local
pages `.wiki/laya*.md`.

## Entry points

Every file is a script, run from the repository root (`laya/README.md:7-9`).
The table at `laya/README.md:11-27` says what each one does. The main ones:

- `laya/pool.py` builds `data/pool.jsonl` from production requests in the
  local Langfuse (`laya/pool.py:24-45`; it defaults to
  `http://localhost:3001`, `laya/pool.py:37`).
- `laya/gate.py` reserves and checks the gate test `eval/laya/tests.json`
  (`laya/gate.py:25`).
- `laya/generate.py` generates candidate states, `laya/label.py` labels them
  by two-labeller agreement, and `laya/guided.py` does guided labelling.
- `laya/train.py` fine-tunes, calibrates and scores; `pack` zips code and
  labels for Colab (`laya/train.py:244-248`).
  `laya/notebooks/train.ipynb` is the GPU-session path.
- `laya/loop.py` is the RRSI loop for one kind (`laya/loop.py:167`).
- `laya/jev_gate.py` compares Laya with Jev on the reserved gate. Results go
  to `raw/eval/laya/gate-<kind>-<timestamp>.json` (`laya/jev_gate.py:22`).
- `laya/support.py` and `laya/joint.py` are derived coverage kinds that
  production never asks (`laya/kinds.py:22-29`).
- Example commands are at `laya/README.md:121-126`.

## Depends on, depended on by

- It depends on the installed `laya` package and `torch`
  (`laya/stage1.py:252`). Installing them and the base checkpoint needs
  approval (`laya/README.md:129-131`).
- It depends on host CLIs (Claude/Codex) as labellers, prompted from
  `laya/prompts/` (`laya/README.md:34-35`).
- It reads the local Langfuse on `localhost:3001` (`laya/pool.py:37`).
- It reaches into `tool/` by appending it to `sys.path`, for example to use
  production's language detection (`laya/generate.py:57-59`).
- It reads `eval/jev/policy.json` to score Jev's side
  (`laya/jev_gate.py:41`).
- Depended on by `tool/eval/arms.py`, which takes `laya/data/coverage/gate.jsonl`
  and `laya/jev_gate.py` results as inputs (`tool/eval/arms.py:436-437`).
  `tool/eval/policy.py:103` writes Laya policies to `eval/laya/policy/`.
  `tool/verify.py:65` counts `laya` as a whole-suite root.

## What trips a newcomer

- This is not a Python package. The modules import each other by bare name
  (`import gate`, `from store import read`, `laya/augment.py:27-31`), so
  `import laya` always means the installed model package
  (`laya/README.md:7-9`).
- Its tests run separately and serially: `python -m pytest laya -n 0`.
  `pytest.ini:2` sets `testpaths = tool`, and CI runs a separate job because
  "Laya and the application have distinct bare module namespaces"
  (`.github/workflows/verify.yml:99-115`). Never mix `tool` and `laya` in
  one pytest run.
- The kind lists have drifted. `laya/kinds.py:9-11` has 14 kinds (nine
  Noul, five Choice). `tool/decision/policy.py:31-34` also has `relevance`,
  which makes 15. A production `relevance_*` question gets no Laya kind.
- `laya/README.md:4` says the serving side in `tool/decision` reads this
  folder's checkpoints and policies. Today nothing in `tool/decision/`
  mentions Laya. The provider contract moved to rollout stage 6, step 0
  (`docs/plans/laya/0-overview.md:61`).
- `laya/data/`, `laya/notes/` and `laya/checkpoints/` are ignored and exist
  only on this machine (`.gitignore:31-33`). The error notebook quotes
  production states (`laya/README.md:29-30`). A fresh clone cannot reproduce
  a run.
- Labelling, generation and note writing are paid host turns. Each run needs
  approval with a turn limit, and a rerun needs approval again
  (`laya/README.md:129-131`). The ledger makes a rerun pay only for turns
  not yet made (`laya/README.md:38-40`).
- The gate test never enters the loop (`laya/README.md:62`). Reading it
  repeatedly erodes it. See `.wiki/laya.md` for why the next coverage
  verdict needs freshly reserved units.
