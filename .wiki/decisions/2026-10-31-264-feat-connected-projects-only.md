---
scope: project
severity: preference
triggers: []
domain: ''
title: "Only connected repositories appear in the repository selection, and new repositories can be selected and connected from anywhere using [Find Folder] in Project · Connection."
pr: 264
merged: 2026-10-31
branch: "feat/connected-projects-only"
---

# Only connected repositories appear in the repository selection, and new repositories can be selected and connected from anywhere using [Find Folder] in Project · Connection.

What. Repository List (`channels.projects`) - Returns only connected and partially connected repositories. - In the top-left selection, connected repositories appear without tags, while only partially connected ones are marked as "Partial". - Removed the `wired` field, which was always true, and the "Unconnected" status, which no longer appears in the list, from the screen type. - Project · Connection - Removed the unconnected count. - Added [Find Folder] to the right of the header. …

Why. All Git folders next to the wiki folder appeared in the top-left repository selection. Unconnected repositories were also mixed in, and those repositories did not work. The "Connected" tag was unnecessarily attached to connected repositories. The only repositories that could be handled were those next to the wiki folder.

Source. PR #264 · `feat/connected-projects-only`
