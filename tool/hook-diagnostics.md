# Hook Timeout Auto-Diagnosis

It is impossible to leave evidence solely through the termination process of a process killed externally after 10 seconds.

## Collection Scope

| Collector | What is recorded | Timing |
| --- | --- | --- |
| `hook_diagnostics.py` | wiki hook name, PID/parent PID, start UTC, full Python stack, elapsed time upon normal termination | 8 seconds for 10-second limit hooks, 13 seconds for session state, 28 seconds for updates |
| `watch_hook_timeouts.ps1` | Actual hook process name, PID/parent PID, start/observed UTC, elapsed time, CPU time, memory, direct child process name/PID | Observed at 0.5-second intervals when it exceeds 8 seconds |

Files are written to `%LOCALAPPDATA%\wiki-hook-diagnostics\` in UTF-8 without BOM.
`wiki-*.log` is the stack, `process-*.log` is external process observation.
Files for normal short-term Python execution are deleted. Slow normal termination leaves `completed_ms`.
External observation adds the time if the process disappears but records that the **reason for termination is unknown**.
The fact that it exceeded 8 seconds is not interpreted as a timeout confirmation by Codex.

Input JSON, utterance, tool arguments, environment variables, and tokens are not stored. The external collector uses the command line only for hook identification and does not store it. The stack includes file paths.
Records excluding files from the last 60 seconds are kept for 7 days or up to the latest 100 entries. This is applied when the wiki hook runs and during hourly cleanup by the external collector. `observer-error.log` only stores collector error types.

## Current Installation and Operation

The five wiki entry points turn on the collector at the first import. Existing hooks command, limit, and trust settings are maintained. Even if the log directory cannot be written to, existing P0 tool blocking still functions.

`pwsh -NoProfile -NonInteractive -WindowStyle Hidden -File <위키>/tool/watch_hook_timeouts.ps1`
Collectors in the same log directory are executed one at a time via mutex.

```powershell
Get-ScheduledTask -TaskName WikiHookTimeoutObserver
Get-ChildItem "$env:LOCALAPPDATA/wiki-hook-diagnostics" -Filter '*.log'
```

To deactivate, execute `Stop-ScheduledTask -TaskName WikiHookTimeoutObserver` and `Disable-ScheduledTask -TaskName WikiHookTimeoutObserver`. If the wiki is moved to a different path, the `-File` path in the scheduled task must also be updated.

The observer identifies native wiki hook scripts and their dispatcher. It does
not launch or depend on an external desktop hook transport. New native hooks
still follow the host's existing trust procedure.

## Verification and Limitations

The stack is only visible after Python starts. External observation identifies
the shell and native hooks, but cannot establish the host's termination judgment.
OS load or permission failures can lose records. Read `process-*.log` and
`wiki-*.log` from the same time together.
