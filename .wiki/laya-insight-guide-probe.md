---
scope: project
severity: preference
triggers: ["(?i)laya.*(guide|지침|probe|점검 세트|adopt|채택)"]
reads: [laya/guided.py]
slots: []
sources: []
links: [laya, laya-exp-guided-labelling]
---

# A small probe of easy agreed labels cannot show a labeller guide helping

Part of [laya](laya.md).

Rule. Judge a proposed labeller guide on items where the labeller actually
errs, enough of them that a few questions' gain clears the noise floor; a
probe of 60 earlier two-labeller agreements is neither.

Why. In the guided pilot ([laya-exp-guided-labelling](laya-exp-guided-labelling.md)) Haiku already scored 0.883
on the 60-question probe, the floor was 0.05–0.08 (three to five questions),
and none of five guides moved it beyond −0.033 to +0.017. The probe holds
only items two frontier models agreed on, the easy ones; the verifier's
rejections come from the hard ones the probe lacks. The other thirteen
kinds have no earlier labels at all, so a gold probe cannot be the general
check.

How to apply. Compare guides on fresh items both guides label, judged by the
verifier with mirrors (paired reject rates), or on a larger gold set where
one exists (`useful`: 600 in validation and calibration). Keep the verifier
from ever seeing which guide wrote an answer.
