---
scope: project
severity: preference
triggers: []
domain: ''
title: "A human reads and approves by name the test definitions that have changed outside the contract in the refactoring card, and rejects keys that the specification change cannot reflect."
pr: 255
merged: 2026-10-29
branch: "fix/refactor-test-contract-channel"
---

# A human reads and approves by name the test definitions that have changed outside the contract in the refactoring card, and rejects keys that the specification change cannot reflect.

What. A `[테스트 계약 변경 승인]` appears on the card. For each existing Python test file changed outside the contract, it shows the diff from the starting commit and the definition name (e.g., `_Repository::list_bindings`) describing that change. If approved, only those names are declared in `tests.contract`, so modifying other parts of the same file will still be rejected. …

Why. The `semantic-index-cleanup-97d92512` card of ai-companion-voice-agent did not reach the gate. The `change` of the investigation specification instructed to remove `generation` from the module-level test doubles (`_Repository.list_bindings`, `_Repository.record_deleted`) of `tests/unit/memory_indexer/test_worker.py`, but since `tests.contract` was empty, the execution immediately rejected that change. The agent sent `tests` in `spec-update`, but the server silently discarded that key, and the next gate also failed for the same reason. …

Source. PR #255 · `fix/refactor-test-contract-channel`
