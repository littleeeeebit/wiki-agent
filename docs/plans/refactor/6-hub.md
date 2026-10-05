# Stage 6 — hub scope and first real use

Let the hub refactor itself, then use it on `tool/main/knowledge.py`.

## Work

- Hub scope runs only in a separate worktree, like any task; the running
  server's checkout is never edited. After merge the person restarts the app.
- Records are namespaced by scope and repository identity, as self-improvement
  records are. A hub result never becomes a shared rule by itself.
- First use: module restructure on `knowledge.py`, with the ratchet entry
  lowered by merge-time tightening as each step lands.

## Tests

Hub run refuses the original checkout; records of hub and project runs do not
mix. The `knowledge.py` steps carry their own characterization tests.

## Rollback

Disable hub scope; project scope is unaffected.
