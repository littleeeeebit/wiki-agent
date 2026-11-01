---
scope: project
severity: preference
triggers: []
domain: ''
title: "Refactoring investigation: The specification accepts a newly created common file as a shared: boundary."
pr: 233
merged: 2026-10-25
branch: "refactor-shared-creates"
---

# Refactoring investigation: The specification accepts a newly created common file as a shared: boundary.

What. `shared:<경로>` is accepted if it is in the `shared` of the map or if the path is in the `creates` of that specification. `creates` is checked as usual: it must not exist in the base commit, the folder must exist, and it must be within the repository. - Write both the acceptable pipeline ID and the shared path in the rejection message. The repair turn looks at this list and fixes it. - The same rules were also written in the `pipeline` description of the integration prompt.

Why. In ai-companion-voice-agent, investigation 7357ebbe finished all 19 pipelines. However, it stopped at the integration stage after 85 minutes. Two specifications (`speech-shape-owner`, `mounted-component-test-support`) tried to create a new common file to use as a shared boundary. The `pipeline` of both specifications was `shared:<그 새 파일>`. The check only accepts paths that are in the `shared` list of the map. Files that do not exist yet cannot be included in the map, so they were rejected. The rejection message did not inform about the acceptable values. Therefore, the same block appeared again in the repair turn, causing it to stop.

Source. PR #233 · `refactor-shared-creates`
