---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya generator: Make long queries as long as production"
pr: 190
merged: 2026-10-19
branch: "laya-generator-long-queries"
---

# Laya generator: Make long queries as long as production

What. The shape of the pilot (`shape1`, 99 queries, $1.09) matched the development pool, but the query length p90 was 615 characters, which was half of production (1,173 characters). As per the owner's decision (2026-10-20), we will write long queries longer.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #190 · `laya-generator-long-queries`
