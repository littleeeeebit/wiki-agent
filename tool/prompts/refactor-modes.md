The three refactoring modes. A mode is a purpose, not a stage: a survey and
every spec belong to exactly one mode. Never fold in a change another mode
owns; when one is a real prerequisite, propose it as its own spec with a
dependency and say why. A mode may find nothing worth doing; say so with the
reason instead of inventing work.

## preserve

Behaviour-preserving structure cleanup: split functions, classes and modules
into clear responsibilities while observable behaviour stays identical.

- Read the existing tests first. Add characterization or regression tests
  only for core behaviour they do not cover.
- Tell existing behaviour from known bugs. Never pin a bug as a new contract
  and never change behaviour here, quietly or otherwise.
- Split a large, complex function along meaningful responsibilities, so each
  part can be reused and checked on its own. Do not slice code only to
  shorten it and do not add abstractions for imagined reuse.
- After the split, remove code left without a job, code proven unused and
  misplaced classes or modules, each only with evidence.
- Keep observable results, error handling, side effects and call order.
- One-off comparison scripts, temporary instrumentation and exact duplicate
  tests may go. Never delete valid regression tests because they pass: fold
  a useful check into an existing test or keep a minimal permanent one.
- Verify again on the final state, after temporary code is gone.

## simplify

Unneeded complexity and contract cleanup: duplicated checks, stale test
contracts and unclear names.

- Look for the same fact checked in several layers, conditions already
  guaranteed inside, and unused compatibility branches, fallbacks and
  wrappers.
- For each, find what it protects and where that guarantee holds, then remove
  only the duplicate. Required defence at authentication, permission, input
  validation, data integrity, concurrency and external-system boundaries is
  never "excessive validation".
- Make clear what each test guarantees. Update a stale contract only from
  current requirements and public contracts, and merge tests that detect the
  same failure twice.
- Never change an expectation to make current code pass and never delete a
  failing test. Normal behaviour, boundaries, errors and regressions can be
  different roles.
- Rename variables, functions, classes and modules to their real role and
  domain meaning. Internal names like new, old, v2 or final become names that
  say the role; versions that are real compatibility contracts (external API
  versions, data schemas, migrations) stay.
- A rename checks its callers, configuration, serialization, documents and
  tests in the same change.

## rework

Structural and performance root fixes the other two modes cannot reach. The
goal is to remove the cause of structural debt or a bottleneck actually
found, not a more elegant structure.

- Look for dependency cycles, wrong responsibility boundaries, unclear
  ownership of shared state, needless round trips between pipelines,
  repeated computation, lookups or serialization, inefficient algorithms or
  data structures, and excessive I/O, process creation or resource holding.
- A structural change states the dependencies and responsibility moves
  before and after. A performance change measures a baseline on a
  representative task under the same conditions it will be judged in.
- Choose only the metrics the problem needs: time, memory, disk or network
  I/O, database or API calls, cost, throughput. When it cannot be measured,
  give the hypothesis and how to check it, and claim nothing.
- Prefer the smallest change that removes the cause. Caches, parallelism, new
  layers and new frameworks are not default answers; an option that adds
  complexity states its gain and its new burden.
- A cache is checked for invalidation and consistency, parallelism for order,
  races, cancellation and failure recovery, resource handling for creation,
  sharing and release.
- Move in small steps that keep external behaviour and data contracts. An
  unavoidable contract change or data migration is its own spec, with impact,
  compatibility, migration and rollback, for separate approval.
- Compare before and after under the same conditions and check correctness
  too. Speed bought by dropping safety checks or doing less work is not an
  improvement.
- When there is no meaningful gain or structural evidence, concluding not to
  optimize is a valid answer. Leave no complexity without improvement.
