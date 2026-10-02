import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Btn } from '@/components/Modal'
import { mobileLayout, setMobileLayout, setRemote, status } from '@/lib/mobile'
import type { MobileStatus } from '@/lib/mobile'
import * as api from '@/lib/api'

// A pairing secret is a fragment, never a URL logged by the server or relay.
// Remove it before any application component or external link can read it.
let pairing = new URLSearchParams(location.hash.slice(1)).get('pair')
if (pairing) history.replaceState(null, '', location.pathname + location.search)

let opening: Promise<MobileStatus> | null = null
function open() {
  if (!opening) opening = (async () => {
    const info = await status()
    setRemote(!info.local)
    if (!info.local && pairing && !info.paired) {
      const res = await fetch('/api/mobile/pair', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: pairing }) })
      if (!res.ok) {
        if (res.status === 401) pairing = null
        throw new Error((await res.json()).detail)
      }
      info.paired = true
    }
    if (info.paired) pairing = null
    return info
  })().finally(() => { opening = null })
  return opening
}

export function MobileAccess({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [fault, setFault] = useState('')
  const [checking, setChecking] = useState(false)
  const [connected, setConnected] = useState(true)

  async function check() {
    setChecking(true)
    setFault('')
    try {
      const info = await open()
      setConnected(true)
      setReady(info.local || !!info.paired)
      if (!info.local && !info.paired) setFault('PC의 설정 → 휴대폰에서 새 연결 링크를 만들어 여세요.')
    } catch (error) {
      setConnected(false)
      setFault(String(error instanceof Error ? error.message : error))
    } finally { setChecking(false) }
  }

  useEffect(() => {
    void Promise.resolve().then(check)
    const offline = () => setConnected(false)
    const revoked = () => { setReady(false); setFault('PC에서 연결이 해제됐습니다. 새 연결 링크로 연결하세요.') }
    window.addEventListener('offline', offline)
    window.addEventListener('mobile-disconnected', offline)
    window.addEventListener('mobile-unauthorized', revoked)
    return () => {
      window.removeEventListener('offline', offline)
      window.removeEventListener('mobile-disconnected', offline)
      window.removeEventListener('mobile-unauthorized', revoked)
    }
  }, [])

  if (ready) return <>
    {!connected && <div role="status" className="fixed inset-x-0 top-0 z-50 bg-card p-3 text-center text-[14px]">
      연결이 끊겼습니다 · 다시 연결하면 PC 기록을 불러옵니다 <Btn onClick={() => location.reload()}>다시 연결</Btn>
    </div>}
    {children}
  </>
  return <main className="flex min-h-dvh items-center justify-center p-6">
    <section className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-card p-6">
      <h1 className="font-heading text-xl font-semibold">wiki-agent · 휴대폰 연결</h1>
      <p className="text-base leading-relaxed">작업과 기록은 PC에 있고, 휴대폰에서 상태를 확인하고 대화와 승인을 이어갑니다.</p>
      <p role="status" className="text-[14px] leading-relaxed text-muted-foreground">{fault || 'PC에 연결하는 중…'}</p>
      <Btn disabled={checking} onClick={() => void check()}>{checking ? '연결하는 중…' : '다시 연결'}</Btn>
    </section>
  </main>
}

export function MobileSettings() {
  const [info, setInfo] = useState<MobileStatus | null>(null)
  const [layout, setLayout] = useState(mobileLayout)
  const [link, setLink] = useState('')
  const [expires, setExpires] = useState(0)
  const [now, setNow] = useState(0)
  const [working, setWorking] = useState(false)
  const [fault, setFault] = useState('')
  useEffect(() => {
    let stale = false
    const read = () => {
      setNow(Date.now())
      return status().then((value) => { if (!stale) setInfo(value) }).catch(() => {})
    }
    void Promise.resolve().then(read)
    const timer = window.setInterval(read, 2000)
    return () => { stale = true; window.clearInterval(timer) }
  }, [])

  async function act(fn: () => Promise<void>) {
    setWorking(true)
    setFault('')
    try { await fn() }
    catch (error) { setFault(String(error instanceof Error ? error.message : error)) }
    finally { setWorking(false) }
  }
  return <section aria-label="휴대폰 연결" className="space-y-3">
    <h3 className="font-heading text-[14px] font-semibold">휴대폰</h3>
    <p className="text-[14px] leading-relaxed text-muted-foreground">PC에서 작업을 실행하고 휴대폰 브라우저에서 대화·변경 사항·승인을 이어갑니다. 모바일 데이터와 다른 Wi-Fi에서도 연결됩니다.</p>
    {info?.local ? <>
      <p role="status" className="text-[14px]">{info.starting ? '외부 연결을 여는 중…' : info.enabled ? '외부 연결 켜짐' : '외부 연결 꺼짐'}</p>
      <div className="flex flex-wrap gap-3">
        {!info.enabled && <Btn disabled={working || info.starting} onClick={() => void act(async () => setInfo(await api.startMobile()))}>
          외부 연결 켜기
        </Btn>}
        {info.enabled && <Btn tone="primary" disabled={working} onClick={() => void act(async () => {
          const made = await api.mobileLink()
          setLink(made.link)
          setExpires(Date.now() + made.seconds * 1000)
        })}>연결 링크 만들기</Btn>}
        {(info.enabled || info.starting) && <Btn disabled={working} onClick={() => void act(async () => {
          setInfo(await api.stopMobile())
          setLink('')
        })}>연결 끄기 · 기기 해제</Btn>}
      </div>
      {link && expires > now && <div className="space-y-3 text-[14px]">
        <figure className="space-y-2">
          <QRCodeSVG value={link} size={232} marginSize={4} level="M" role="img"
            aria-label="휴대폰 연결 QR 코드" title="휴대폰 연결 QR 코드" className="max-w-full h-auto" />
          <figcaption className="leading-relaxed">휴대폰 카메라로 QR 코드를 스캔하세요. 5분 안에 한 번만 연결할 수 있습니다.</figcaption>
        </figure>
        <input aria-label="휴대폰 연결 링크" readOnly value={link} onFocus={(e) => e.target.select()}
          className="w-full rounded-md border border-border bg-background p-2 font-mono text-[12px]" />
        <Btn onClick={() => void act(() => navigator.clipboard.writeText(link))}>링크 복사</Btn>
      </div>}
      {link && expires <= now && <p role="status">연결 링크가 만료됐습니다. 새로 만드세요.</p>}
      <p className="text-[14px] leading-relaxed text-muted-foreground">PC와 앱이 켜져 있어야 합니다. 기본 연결 주소는 다시 켤 때 바뀝니다. 연결 링크를 가진 사람은 PC 작업을 제어할 수 있으니 본인 휴대폰에서만 여세요.</p>
      {info.error && <p role="alert" className="text-destructive">{info.error}</p>}
    </> : info ? <>
      <fieldset className="space-y-2">
        <legend className="mb-2 font-heading text-[14px] font-semibold">화면 모드</legend>
        <div className="grid grid-cols-2 gap-3">
          {([{ id: 'portrait', label: '세로 모드', note: '한 화면씩 보기' },
            { id: 'landscape', label: '가로 모드', note: 'PC처럼 나란히 보기' }] as const).map((mode) => (
            <label key={mode.id} className={`flex min-h-11 cursor-pointer items-start gap-2 rounded-md border p-3 ${
              layout === mode.id ? 'border-primary bg-secondary' : 'border-border'}`}>
              <input type="radio" name="mobile-layout" value={mode.id} checked={layout === mode.id}
                className="mt-0.5 accent-primary" onChange={() => { setLayout(mode.id); setMobileLayout(mode.id) }} />
              <span className="text-[14px] leading-relaxed"><span className="block">{mode.label}</span>
                <span className="block text-muted-foreground">{mode.note}</span></span>
            </label>
          ))}
        </div>
        <p className="text-[14px] leading-relaxed text-muted-foreground">선택은 이 브라우저에 저장됩니다. 휴대폰을 돌려도 자동으로 바뀌지 않습니다. 가로 모드는 휴대폰을 가로로 놓고 사용하세요.</p>
      </fieldset>
      <p className="text-[14px] text-muted-foreground">연결과 기기 해제는 PC의 설정에서 관리합니다.</p>
    </> : <p className="text-[14px] text-muted-foreground">연결 상태를 읽는 중…</p>}
    {fault && <p role="alert" className="text-destructive">{fault}</p>}
  </section>
}
