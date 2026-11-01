---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "feat: Add local execution verification and merge gate for cloud implementation"
pr: 58
merged: 2026-10-01
branch: "feat/cloud-local-review"
---

# feat: Add local execution verification and merge gate for cloud implementation

What. Cloud implementation cannot access local `.env`, test APIs, and private datasets, making it difficult to verify actual behavior solely through repository inspection. We have distinguished between local/cloud implementation environments and ensured that PRs implemented in Claude Code Cloud can only be merged after passing local execution evidence, independent read-only reviews, and a final gate. The existing local implementation flow and `plan`·`code`·`mixed` review criteria are maintained.

Why. Commit API/data contracts, lists of key flows, and verification commands to the repository. Local settings link approved specification hashes with dedicated test environments. Check settings, resumption, and execution evidence on the review screen, and restrict merging via GitHub required status `wiki-agent/local-verification` as well. If commits, base, or known environments change, existing passes are invalidated. If preparation is insufficient, wait, and return actual failures to the cloud with refined evidence. If the same invariant fails in the initial commit and a twice-modified commit, re-analysis is required. Do not complete after a failure in the same commit/environment without verifying the cause. Manually resume interrupted tasks, and reuse only evidence with no impact along with an explicit reason. …

Source. PR #58 · `feat/cloud-local-review`
