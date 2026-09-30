## Plan criteria

The question: can an implementer carry this plan out and verify the outcome it
intends? Evidence is the requirement-to-stage mapping, the sources, the
alternatives weighed, the gates, who owns what, and the rollback.

| Grade | Criterion |
| --- | --- |
| P0 | A contradictory safety or authority contract; a destructive design without the boundary it needs |
| P1 | A required behaviour missing; an impossible dependency; an adopted claim its sources do not support; an acceptance nobody could implement or observe |
| P2 | An optional alternative, wording, a nonessential optimisation |

- Complete means every requirement listed under `Requirements` maps to a stage
  with observable acceptance. It does not mean the code already exists: never
  raise a finding because planned code is not written yet.
- A preference for another library or design is not a defect unless the one
  chosen cannot meet a stated constraint. Name that constraint, or grade it P2.
- Cite the plan's own lines: `path:line` points at the document.
