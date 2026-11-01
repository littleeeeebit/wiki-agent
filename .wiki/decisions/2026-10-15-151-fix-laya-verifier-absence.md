---
scope: project
severity: preference
triggers: []
domain: ''
title: "Laya guideline labeling: Adopt agreed-upon answers even if evidence cannot be cited"
pr: 151
merged: 2026-10-15
branch: "fix/laya-verifier-absence"
---

# Laya guideline labeling: Adopt agreed-upon answers even if evidence cannot be cited

What. Fix the issue where, in guideline labeling, if both the labeler and the validator provided the same answer with a confidence of 0.7 or higher, it was discarded if the validator's judgment field was `unclear`.

Why. The validation prompt (`verify.md`) instructs to write `unclear` as the judgment if there is no evidence to cite. The 'No' for coverage (requirement not in the paragraph) originally has no evidence to cite. Therefore, 983 coverage items where both models answered 'No' were discarded, 823 of which had a validator confidence of 1.0. As a result, the coverage label was biased toward 'Yes' (train 'Yes' 946 / 'No' 158). The trained models also collapsed to 'Yes'. The module description and the 3-step plan originally stated, "Adopt if both answers are the same with 0.7 or higher confidence." Only the code was stricter than that.

Source. PR #151 · `fix/laya-verifier-absence`
