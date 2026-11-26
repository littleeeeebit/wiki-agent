## This machine is shared

{machine}

The checks below are enforced by the hooks and the server. This section only explains their refusals before you meet them.

- GPU jobs queue. A command that runs Python from an environment with CUDA, or any program built inside this repository, its worktrees or temp, is wrapped by the hook into the machine's GPU queue. On Claude and DeepSeek it is moved to the background, and you decide it the way you decide any moved background command. On Codex it waits in the foreground and prints the command that leaves the queue. A job of yours that already holds the GPU must end before another starts: the hook refuses it and names the process. When this turn ends, every GPU job it queued ends with it.
- The owner's real data folders are refused, and the server watches them over every build turn and every server launch. Any change there fails the turn or the measurement. These folders are:
{roots}
- Launches of the product during a turn use `{agent}` for their data, never the owner's folders.
