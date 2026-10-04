import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { Options } from '@/lib/api'
import { useState } from 'react'

export type Choice = { model: string; effort: string; fast?: boolean }

type Props = {
  value: Choice
  options: Options | null
  busy: boolean
  onChange: (next: Choice) => void
  showFast?: boolean
}

// A Radix Select cannot use the empty string as a value. "default" — meaning
// no flag is passed — is carried on screen by this token and turned back into
// an empty string on the way to the server. Pressing the selected item again
// deselects it and yields `null`, which counts as "default" too.
const NONE = '__default__'
const CUSTOM = '__custom__'
const out = (v: string | null) => (!v || v === NONE ? '' : v)
const inn = (v: string) => v || NONE

export type Item = { value: string; label: string; note?: string }

/** A model and its effort — for a focus, or for a worktree's agent. The
 *  project is the rail's: it applies to both panes at once. */
export function Toolbar({ value, options, busy, onChange, showFast = false }: Props) {
  const pick = (patch: Partial<Choice>) => onChange({ ...value, ...patch })
  const supportsFast = (model: string) => model.startsWith('codex:')
    ? !!options?.models.find((item) => item.id === model)?.supports_fast
    : !model || /^(opus|claude-opus-(5|4-8))(\b|\[)/.test(model)
  const fastSupported = supportsFast(value.model)

  // The list only holds aliases, which already follow the newest model. A
  // name that is not on it — a new family, a pinned version — is typed in,
  // and shown as an item of its own once chosen.
  const [typing, setTyping] = useState(false)
  const listed = options?.models.some((m) => m.id === value.model) ?? true
  const models: Item[] = [
    ...(options?.models.map((m) => ({ value: inn(m.id), label: m.label, note: m.note })) ?? []),
    ...(!listed ? [{ value: value.model, label: value.model, note: '직접 입력' }] : []),
    ...(options ? [{ value: CUSTOM, label: '직접 입력…' }] : []),
  ]
  const efforts: Item[] =
    (options?.models.find((m) => m.id === value.model)?.efforts ?? options?.efforts)?.map((e) => ({ value: inn(e.id), label: e.label, note: e.note })) ??
    []

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Picker
        label="모델"
        hideLabel
        width="w-32"
        items={models}
        value={inn(value.model)}
        disabled={busy || !options}
        onPick={(v) => {
          if (v === CUSTOM) return setTyping(true)
          const model = out(v)
          const supported = options?.models.find((m) => m.id === model)?.efforts ?? options?.efforts
          pick({ model, effort: supported?.some((e) => e.id === value.effort) ? value.effort : '',
            ...(showFast ? { fast: !!value.fast && supportsFast(model) } : {}) })
        }}
      />
      {typing && (
        <input
          autoFocus
          aria-label="모델 이름"
          placeholder="claude-opus-5-5"
          className="w-40 rounded-md border border-border bg-card px-2 py-1 font-mono text-[12px]"
          onKeyDown={(e) => {
            if (e.key === 'Escape') setTyping(false)
            if (e.key !== 'Enter') return
            const model = e.currentTarget.value.trim()
            setTyping(false)
            if (model) pick({ model, effort: '', ...(showFast ? { fast: !!value.fast && supportsFast(model) } : {}) })
          }}
          onBlur={() => setTyping(false)}
        />
      )}
      <Picker
        label="추론 강도"
        hideLabel
        width="w-24"
        items={efforts}
        value={inn(value.effort)}
        disabled={busy || !options}
        onPick={(v) => pick({ effort: out(v) })}
      />
      {options?.codex_error && <p role="status" className="w-full text-xs text-destructive">{options.codex_error}</p>}
      {showFast && <button type="button" role="switch" aria-label="FAST 모드" aria-checked={!!value.fast}
        disabled={busy || !options || !fastSupported} onClick={() => pick({ fast: !value.fast })}
        title={fastSupported ? 'CLI의 빠른 처리 모드 · 다음 턴부터 적용 · 계정과 모델 지원 및 추가 요금은 제공자 정책에 따른다'
          : '선택한 모델이 FAST 지원을 표시하지 않는다. 지원 모델을 선택하세요'}
        className={`h-7 shrink-0 rounded-md border px-2 text-[12.5px] disabled:opacity-40 ${value.fast ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'}`}>
        FAST {value.fast ? 'ON' : 'OFF'}
      </button>}
    </div>
  )
}

/** One picker.
 *
 *  The label is handed to `SelectValue` directly. The list comes from the
 *  server, and on a render before it arrives Radix cannot find an item
 *  matching the selected value and shows the raw value instead —
 *  `__default__` appeared on screen. Holding the label ourselves stops that. */
export function Picker({
  label,
  width,
  items,
  value,
  disabled,
  mono,
  stacked,
  hideLabel,
  onPick,
}: {
  label: string
  width: string
  items: Item[]
  value: string
  disabled: boolean
  mono?: boolean
  /** The label above rather than beside — for a narrow column. */
  stacked?: boolean
  /** Named for a screen reader only — where the value says what it is. */
  hideLabel?: boolean
  onPick: (v: string | null) => void
}) {
  const here = items.find((i) => i.value === value)
  return (
    <label className={stacked ? 'flex flex-col gap-1' : 'flex items-center gap-1.5'}>
      <span className={hideLabel ? 'sr-only' : 'font-heading text-[11px] font-semibold text-faint'}>{label}</span>
      <Select value={value} disabled={disabled} onValueChange={onPick}>
        <SelectTrigger size="sm" className={`${width} bg-card text-[12.5px]`}>
          <SelectValue>
            <span className={mono ? 'font-mono text-[12px]' : undefined}>
              {here?.label ?? '…'}
            </span>
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {items.map((i) => (
            <SelectItem key={i.value} value={i.value}>
              <span className={mono ? 'font-mono text-[12px]' : undefined}>
                {i.label}
              </span>
              {i.note && (
                <span className="ml-1.5 text-[10.5px] text-faint">{i.note}</span>
              )}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  )
}
