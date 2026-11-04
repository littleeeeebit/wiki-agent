import { getLoops } from '@/lib/api'
import { LOOPING } from '@/lib/tasks'

/** `promise`, or a failure after three seconds. A read that never answers — a stalled server, or a
 *  window whose first channel list failed and so never named its project — would otherwise hold
 *  the close and the restart for good (review R1-2). */
const within = <T,>(promise: Promise<T>) => Promise.race([promise,
  new Promise<never>((_, fail) => window.setTimeout(() => fail(new Error('timed out')), 3000))])

/** Asks before the shared server goes down, with what dies with it in every window: running
 *  turns, review loops and answers being generated (`/api/loops`). Closing one of several
 *  windows takes nothing down and asks nothing; closing the last one and the update card's
 *  restart ask the same question. */
export async function leave(closing = false): Promise<boolean> {
  if (closing) {
    try {
      const { getAllWindows } = await import('@tauri-apps/api/window')
      if ((await within(getAllWindows())).length > 1) return true
    } catch {
      // Not knowing about other windows only means asking, as for the last one.
    }
  }
  let what = '도는 작업이 있는지 읽지 못했다. 있다면 멈춘다'
  try {
    const { loops, turns, answers } = await within(getLoops())
    const looping = loops.filter((l) => LOOPING.test(l.state)).length
    what = [turns.length && `도는 작업 ${turns.length}개`, looping && `리뷰 루프 ${looping}개`,
      answers && `만드는 답 ${answers}개`].filter(Boolean).join(', ')
    if (!what) return true
    what += '가 멈춘다'
  } catch {
    // Unread is asked about: a turn may still be running.
  }
  return window.confirm(`${what}. 다시 띄우면 루프는 [계속] 으로 잇는다. 닫을까?`)
}
