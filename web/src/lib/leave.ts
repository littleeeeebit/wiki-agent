/** What dies with the server when the window goes: the turns still running
 *  and the review loops. App keeps the counts; closing the window and the
 *  update card's restart ask the same question. */
export const running = { turns: 0, loops: 0 }

export function leave(): boolean {
  const what = [running.turns && `도는 작업 ${running.turns}개`, running.loops && `리뷰 루프 ${running.loops}개`].filter(Boolean).join('와 ')
  return !what || window.confirm(`${what}가 멈춘다. 다시 띄우면 루프는 [계속] 으로 잇는다. 닫을까?`)
}
