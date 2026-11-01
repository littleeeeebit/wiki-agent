---
scope: project
severity: contract
repeat: rule
triggers: ["PROMPTS", "PROMPT_VERSION", "policy\\.json", "Jev.{0,20}(질문|프롬프트|문구)", "프롬프트를? ?(바꾸|고치|수정)", "임계", "threshold", "provisional"]
slots: []
sources: [tool/main/knowledge.py#L191, tool/decision/policy.py#L83, eval/jev/policy.json]
links: []
---

# Changing a Jev question's wording unfits `eval/jev/policy.json`

Rule. Treat the text of `PROMPTS` in `tool/main/knowledge.py` as bound to
the fitted policy. `PROMPT_VERSION` is a digest of it
(`tool/main/knowledge.py:191`). `decision.policy` falls back to the
provisional 0.2/0.8 thresholds for every kind when the artifact's
`prompt_version` or `model` differs (`tool/decision/policy.py:102-109`).
The only trace is a `problem` field. A change to a question's wording, or
a new question placed in `PROMPTS`, therefore needs a refit
(`python tool/eval/policy.py`) or an explicit note in the PR that the
policy is now provisional. A question asked beside the route but outside
`PROMPTS` (like `ASK`) stays provisional until fitted
(`tool/main/knowledge.py:192-197`).

Why. Thresholds are fitted per kind on a labelled calibration split for one
model and one prompt digest (`tool/decision/policy.py:1-13`). The digest
check exists so that a fit on other wording is never trusted.

What goes wrong. Jev's yes/no cut-offs silently revert to untested defaults,
and decisions shift with no failing test.
