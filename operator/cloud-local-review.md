---
scope: operator
severity: contract
repeat: rule
triggers: ["클라우드.*(구현|리뷰|검증)", "cloud.*(implement|review|verif)", "claude.*cloud", "로컬.*(리뷰|검증)", "local.*(review|verif)"]
slots: []
sources: []
links: [codex-review-loop, agent-delegation, verify-narrow-then-wide, name-the-build-on-screen]
---

# Cloud implements, local verifies the actual behavior

Rule. A cloud implementation hands off its exact commit, changes, run instructions,
completed checks and unverified behavior in the PR. Keep `.env`, credentials and
private datasets local; commit nonsecret API/data contracts and each repository's
own major-flow checklist. A person starts a separate local read-only review in a
dedicated PR folder with locally configured test scope and browser tools. Execute
all major flows initially, including actual API requests and browser actions/UI
results; a build or mock alone does not prove them. Missing prerequisites remain
pending. Return sanitized failures to cloud, never to a local fixer. Bind passes
to commit, base and environment; reuse only unaffected checks with recorded impact
reasons. Preserve failures, interrupted work and repair counts; a later pass on
the same failing identity stays unstable. After two failed repair cycles for the
same invariant, require re-analysis. Only required local evidence, independent
review and the final gate allow merge through the app and GitHub. Pure documents
need document review; locally implemented work keeps its existing process.

What goes wrong. Repository-only checks pass while real API, dataset and screen
behavior remain unknown. Calling that complete transfers undisclosed verification
work to the person and lets incompatible implementation assumptions reach merge.

The product procedure, formats and environment setup are in
[local verification](../docs/local-verification.md). Shared rules carry the
boundary, never another repository's flow list, ports, commands or credentials.
The existing review transport remains [[codex-review-loop]], and independent
roles remain [[agent-delegation]].
