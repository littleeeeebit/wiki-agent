---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix: Limit repo_lint page format check to .md files managed by git"
pr: 61
merged: 2026-10-02
branch: "fix/repo-lint-ignored-pages"
---

# Fix: Limit repo_lint page format check to .md files managed by git

What. The page format check of `repo_lint` was reading all `.md` under `.wiki/` as a glob, including folders that were gitignored. In project-codeit-mid, the review round results of `.wiki/review/` and archived receipts (no front matter) were reported as 313 broken pages. …

Why. Now both checks see the same scope. Files that are tracked, and files that are not yet added but are not ignored. - New test `test_a_page_without_front_matter_is_reported_unless_git_ignores_it`: Fails in the code before the fix and passes after the fix - `pytest test_repo_lint.py test_wiki_health.py test_connect.py test_markdown_emphasis.py`: 87 passed - `tool/lint.py --check`: No new findings - project-codeit-mid: `. …

Source. PR #61 · `fix/repo-lint-ignored-pages`
