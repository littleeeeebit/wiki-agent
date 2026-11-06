---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "fix: make the running process visible — turn until the gate, clock while running, actual command, finding the source"
pr: 28
merged: 2026-09-26
branch: "fix/work-visibility"
---

# fix: make the running process visible — turn until the gate, clock while running, actual command, finding the source

What. P0 — It is unknown if it has stopped. Even after the agent responds, the server holds the work tree and runs the specification gate (`python -m pytest tool`, approx. 6 minutes). Since the screen ended the turn at the response, the next instruction written during that time went out as a new turn and received 409 "The agent for this work tree is still running". There was nothing on the screen indicating what was running. - Clicking the source often resulted in "That file does not exist". …

Why. A turn is considered to be running until the server execution ends (when the stream closes). Steps (gates) that come after the response are placed below the response. - On the most recent step, "⟳ Running · m minutes s seconds". Include the server time `ts` in all turn events so that the clock continues even if reconnected. Open approval is "Waiting for response", and after the response is "Finishing". The two hosts are the same. - Instructions written after the response are set to "Pending · Will be sent when this turn ends" and then sent as the next turn when it finishes (cancellable). - Actual command next to the description in the Claude tool bar: `Bash · Run the tests · $ python -m pytest -q tool`. - `/api/file`: Checkout criteria → Hub document → One file ending with that name. …

Source. PR #28 · `fix/work-visibility`
