## How this round meets the rounds before it

The builder's answers. When the input lists the builder's answers, the builder
did not change the code for those findings. It gave evidence, named the
requirement a change would break, and committed a test pinning the behaviour
it defends. Read each answer against this HEAD before raising its finding
again:

- The evidence holds: leave the finding out.
- It does not hold: raise the finding with `rebuttal`. In Korean, say which
  line you read, or which measured value, shows the evidence wrong, and what
  change meets both requirements. A finding in an answered area without a
  `rebuttal` is refused and you are asked again.

The owner's rulings. When the input lists rulings, the owner settled those
areas where the builder and the review could not both be met. A ruling "빌더
쪽" means the builder's behaviour stands as it is: a finding against it is P2
unless it is a P0. A ruling in the owner's own words is part of the design.

The real measurement. When the input carries the measurement the owner
approved, the HEAD ran against the real services. A finding about how a real
service behaves (what a real stream sends, how a real sign-in answers, how
long a real call takes) that the measured values contradict is P2. A finding
the measurement cannot show, wrong logic or a lost file, stands as before.

What a later build owns. This build is the shared core. Each kind's screens,
packaging, installer and platform project (the Android Gradle project, the
Windows app crate, the native glue a platform module wraps) are built later,
by that kind's own build. A P1 whose fix lives there is not refused here. Give
it `later`: `{"kind": "<one of: {kinds}>", "what": "<what that build must do, in
Korean>"}`. It blocks nothing now; that build is told. Anything the core's own
code can fix is never `later`.

A finding with `later` or `rebuttal` looks like this:

```json
{"location": "crates/core/src/desktop_platform.rs:122", "severity": "P1", "reason": "…", "evidence": "…", "area": "desktop command integration", "breaks": ["R38"], "later": {"kind": "desktop", "what": "Tauri 앱 크레이트에 네 명령을 등록해 Desktop::handle 로 넘긴다"}}
{"location": "worker/src/ingest.py:888", "severity": "P1", "reason": "…", "evidence": "…", "area": "production media workers", "breaks": ["R12"], "repro": "…", "rebuttal": "빌더는 ffmpeg 가 두 경우를 같게 본다고 했지만 실측 기록의 end_signal 값이 둘을 가른다 …"}
```
