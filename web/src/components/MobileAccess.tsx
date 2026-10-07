import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Btn } from '@/components/Modal'
import { installApp, installState, setRemote, status } from '@/lib/mobile'
import type { MobileStatus } from '@/lib/mobile'
import type { InstallState } from '@/lib/mobile'
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

  async function check(reconnect = false) {
    setChecking(true)
    setFault('')
    try {
      const info = await open()
      setConnected(true)
      setReady(info.local || !!info.paired)
      if (reconnect && (info.local || info.paired)) window.dispatchEvent(new Event('mobile-reconnect'))
      if (!info.local && !info.paired) setFault('PC의 설정 → 휴대폰에서 새 연결 링크를 만들어 여세요.')
    } catch (error) {
      setConnected(false)
      setFault(String(error instanceof Error ? error.message : error))
    } finally { setChecking(false) }
  }

  useEffect(() => {
    void Promise.resolve().then(() => check())
    const offline = () => setConnected(false)
    const online = () => { void check(true) }
    const connected = () => setConnected(true)
    const revoked = () => { setReady(false); setFault('PC에서 연결이 해제됐습니다. 새 연결 링크로 연결하세요.') }
    window.addEventListener('offline', offline)
    window.addEventListener('online', online)
    window.addEventListener('mobile-connected', connected)
    window.addEventListener('mobile-disconnected', offline)
    window.addEventListener('mobile-unauthorized', revoked)
    return () => {
      window.removeEventListener('offline', offline)
      window.removeEventListener('online', online)
      window.removeEventListener('mobile-connected', connected)
      window.removeEventListener('mobile-disconnected', offline)
      window.removeEventListener('mobile-unauthorized', revoked)
    }
  }, [])

  if (ready) return <>
    {!connected && <div role="status" className="fixed inset-x-0 top-0 z-50 bg-card p-3 text-center text-[14px]">
      연결이 끊겼습니다 · 다시 연결하면 PC 기록을 불러옵니다 <Btn disabled={checking} onClick={() => void check(true)}>{checking ? '연결하는 중…' : '다시 연결'}</Btn>
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
  const [link, setLink] = useState('')
  const [expires, setExpires] = useState(0)
  const [now, setNow] = useState(0)
  const [working, setWorking] = useState(false)
  const [fault, setFault] = useState('')
  const [installation, setInstallation] = useState<InstallState>(installState)
  const pairWhenReady = useRef(false)
  const connectionRequest = useRef(0)
  useEffect(() => {
    let stale = false
    const read = async () => {
      const request = connectionRequest.current
      setNow(Date.now())
      try {
        const value = await status()
        if (stale || request !== connectionRequest.current) return
        setInfo(value)
        if (value.enabled && pairWhenReady.current) {
          pairWhenReady.current = false
          const made = await api.mobileLink()
          if (!stale && request === connectionRequest.current) {
            setLink(made.link)
            setExpires(Date.now() + made.seconds * 1000)
          }
        }
      } catch (error) {
        if (!stale && request === connectionRequest.current) setFault(String(error instanceof Error ? error.message : error))
      }
    }
    void Promise.resolve().then(read)
    const timer = window.setInterval(read, 2000)
    return () => { stale = true; window.clearInterval(timer) }
  }, [])
  useEffect(() => {
    const changed = () => setInstallation(installState())
    window.addEventListener('mobile-install-changed', changed)
    return () => window.removeEventListener('mobile-install-changed', changed)
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
    <p className="text-[14px] leading-relaxed text-muted-foreground">PC에서 작업을 실행하고 휴대폰 앱이나 브라우저에서 대화·변경 사항·승인을 이어갑니다. 모바일 데이터와 다른 Wi-Fi에서도 연결됩니다.</p>
    <p className="text-[14px] leading-relaxed text-muted-foreground">외부 연결을 켜고 휴대폰 카메라로 연결 QR을 스캔하세요. 별도 설치 없이 브라우저에서 바로 사용할 수 있습니다.</p>
    {info?.local ? <>
      <p role="status" className="text-[14px]">{info.starting ? info.progress || '외부 연결을 여는 중…' : info.enabled ? '외부 연결 켜짐' : '외부 연결 꺼짐'}</p>
      <div className="space-y-2 rounded-md border border-border bg-background p-3">
        <h4 className="font-heading text-[14px] font-semibold">Android 앱 설치 (선택)</h4>
        {!info.apk_available ? <p className="text-[14px] leading-relaxed text-muted-foreground">Android 설치 파일이 없습니다. 아래 연결 QR로 브라우저에서 바로 사용할 수 있습니다.</p>
          : info.enabled && info.origin ? <>
            <figure className="space-y-2 text-[14px]">
              <QRCodeSVG value={`${info.origin}/mobile-install`} size={232} marginSize={4} level="M" role="img"
                aria-label="Android 앱 설치 QR 코드" title="Android 앱 설치 QR 코드" className="max-w-full h-auto" />
              <figcaption className="leading-relaxed">휴대폰 카메라로 스캔 → APK 다운로드 → 설치를 누르세요. Android 8 이상에서 설치할 수 있습니다.</figcaption>
            </figure>
            <Btn onClick={() => void act(() => navigator.clipboard.writeText(`${info.origin}/mobile-install`))}>설치 링크 복사</Btn>
          </> : <p className="text-[14px] leading-relaxed text-muted-foreground">외부 연결을 켜면 휴대폰 카메라로 스캔할 설치 QR이 표시됩니다.</p>}
      </div>
      <h4 className="font-heading text-[14px] font-semibold">PC 연결</h4>
      <div className="flex flex-wrap gap-3">
        {!info.enabled && <Btn disabled={working || info.starting} onClick={() => void act(async () => {
          connectionRequest.current++
          pairWhenReady.current = true
          setLink('')
          setInfo(await api.startMobile())
        })}>
          외부 연결 켜기
        </Btn>}
        {info.enabled && <Btn tone="primary" disabled={working} onClick={() => void act(async () => {
          pairWhenReady.current = false
          const made = await api.mobileLink()
          setLink(made.link)
          setExpires(Date.now() + made.seconds * 1000)
        })}>연결 링크 만들기</Btn>}
        {(info.enabled || info.starting) && <Btn disabled={working} onClick={() => void act(async () => {
          connectionRequest.current++
          pairWhenReady.current = false
          setInfo(await api.stopMobile())
          setLink('')
        })}>연결 끄기 · 기기 해제</Btn>}
      </div>
      {link && expires > now && <div className="space-y-3 text-[14px]">
        <figure className="space-y-2">
          <QRCodeSVG value={link} size={232} marginSize={4} level="M" role="img"
            aria-label="휴대폰 연결 QR 코드" title="휴대폰 연결 QR 코드" className="max-w-full h-auto" />
          <figcaption className="leading-relaxed">설치한 앱의 ‘연결 QR 스캔’으로 스캔하세요. 브라우저는 휴대폰 카메라로 여세요. 5분 안에 한 번만 연결할 수 있습니다.</figcaption>
        </figure>
        <input aria-label="휴대폰 연결 링크" readOnly value={link} onFocus={(e) => e.target.select()}
          className="w-full rounded-md border border-border bg-background p-2 font-mono text-[12px]" />
        <Btn onClick={() => void act(() => navigator.clipboard.writeText(link))}>링크 복사</Btn>
      </div>}
      {link && expires <= now && <p role="status">연결 링크가 만료됐습니다. 새로 만드세요.</p>}
      <p className="text-[14px] leading-relaxed text-muted-foreground">PC와 앱이 켜져 있어야 합니다. 기본 연결 주소는 다시 켤 때 바뀝니다. 연결 링크를 가진 사람은 PC 작업을 제어할 수 있으니 본인 휴대폰에서만 여세요.</p>
      {info.error && <p role="alert" className="text-destructive">{info.error}</p>}
    </> : info ? <>
      <div className="space-y-2 rounded-md border border-border bg-background p-3">
        <h4 className="font-heading text-[14px] font-semibold">앱 설치</h4>
        {installation === 'installed' ? (
          <p role="status" className="text-[14px] leading-relaxed">홈 화면 앱으로 설치되어 있습니다.</p>
        ) : installation === 'ready' ? <>
          <p className="text-[14px] leading-relaxed text-muted-foreground">홈 화면에서 바로 열고, 브라우저 주소창 없이 사용합니다.</p>
          <Btn tone="primary" disabled={working} onClick={() => void act(() => installApp())}>앱 설치</Btn>
        </> : <div className="space-y-1 text-[14px] leading-relaxed text-muted-foreground">
          <p>브라우저 메뉴에서 설치할 수 있습니다.</p>
          <p>iPhone·iPad: Safari 공유 → 홈 화면에 추가</p>
          <p>Android: 브라우저 메뉴 → 앱 설치 또는 홈 화면에 추가</p>
        </div>}
        {location.hostname.endsWith('.trycloudflare.com') && (
          <p className="text-[14px] leading-relaxed text-muted-foreground">현재 임시 주소는 PC에서 연결을 다시 켜면 바뀝니다. 계속 쓸 앱은 고정 주소에서 설치하세요.</p>
        )}
      </div>
      <p className="text-[14px] leading-relaxed text-muted-foreground">Android 앱 왼쪽 위 ⋮ → 세로 모드 또는 가로 모드를 선택하세요. 화면과 배치가 함께 바뀌며, 자동 회전이 꺼져 있어도 두 모드를 선택할 수 있습니다. 세로는 아래 메뉴로 이동합니다. 가로는 목록·대화·작업을 함께 보고, 목록을 접거나 대화·작업만 넓게 볼 수 있습니다. 브라우저에서는 앱 화면 회전을 제어할 수 없습니다.</p>
      <p className="text-[14px] text-muted-foreground">연결과 기기 해제는 PC의 설정에서 관리합니다.</p>
    </> : <p className="text-[14px] text-muted-foreground">연결 상태를 읽는 중…</p>}
    {fault && <p role="alert" className="text-destructive">{fault}</p>}
  </section>
}
