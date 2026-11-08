## Requirements in a spec

A `spec` block may also carry `requirements`: optional, a list of numbered
requirements when the request has distinct behaviours worth checking one by
one. Each item:
`{"id": "R1", "text": "...", "source": {"kind": "original", "locator": "the person's second message"}, "status": "confirmed"}`.

- `id` is `R1`, `R2`, ... and unique.
- `source.kind` is `original` (the person's own words), `policy` (a rule
  page), `repository` (the code or a document) or `decision` (settled in this
  conversation); `locator` says where.
- `status` is `confirmed`, `assumed`, `undecided` or `not_applicable`.
- Optional: `preconditions`, `postconditions`, `invariants`, `failures`
  (lists of lines), `depends_on` (ids defined in the same list), `context`
  (repository paths), `verify` (`[{"item": "a check", "expected": "its result"}]`)
  and `global: true` for a requirement every task must carry.

Any other key, a repeated id or a `depends_on` naming no requirement refuses
the block. Do not copy the person's request: the server keeps their words
verbatim.
