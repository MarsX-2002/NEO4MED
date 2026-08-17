/** Базовые элементы интерфейса.
 *
 *  Своя горстка компонентов вместо библиотеки: их полтора десятка, а любая
 *  UI-kit притащила бы свою систему токенов и спорила с нашей.
 */
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useT } from '../lib/i18n'

const cx = (...parts: (string | false | undefined | null)[]) => parts.filter(Boolean).join(' ')

/* ── Кнопка ─────────────────────────────────────────────────────────────── */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'soft' | 'ghost' | 'outline' | 'danger'
  size?: 'md' | 'sm' | 'xs'
  loading?: boolean
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const base =
    'spring inline-flex items-center justify-center gap-1.5 rounded-[8px] font-medium ' +
    'transition disabled:opacity-45 disabled:cursor-not-allowed select-none whitespace-nowrap'
  const sizes = {
    md: 'h-9 px-4 text-[13.5px]',
    sm: 'h-7.5 px-3 text-[13px]',
    xs: 'h-6.5 px-2.5 text-[12.5px]',
  }
  const variants = {
    primary: 'bg-accent text-white hover:bg-accent-hover',
    soft: 'bg-accent-soft text-accent-ink hover:brightness-97',
    outline: 'border border-line-strong bg-surface-card text-ink hover:bg-surface-container',
    ghost: 'text-ink-muted hover:bg-surface-container hover:text-ink',
    danger: 'bg-danger-soft text-danger hover:brightness-97',
  }
  return (
    <button
      className={cx(base, sizes[size], variants[variant], className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && (
        <span
          aria-hidden
          className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
}

/* ── Поля ───────────────────────────────────────────────────────────────── */

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string
  hint?: string
  error?: string
}

export function Field({ label, hint, error, className, id, ...rest }: FieldProps) {
  const inputId = id ?? `f-${(label ?? 'x').replace(/\s+/g, '-').toLowerCase()}`
  return (
    <div className="grid gap-1">
      {label && (
        <label htmlFor={inputId} className="text-[12.5px] font-medium text-ink-muted">
          {label}
        </label>
      )}
      <input
        id={inputId}
        aria-invalid={!!error}
        className={cx(
          'spring h-9 w-full rounded-[8px] border bg-surface-card px-3 text-[13.5px] text-ink',
          'placeholder:text-ink-faint transition',
          error ? 'border-danger' : 'border-line hover:border-line-strong',
          className,
        )}
        {...rest}
      />
      {error ? (
        <p role="alert" className="text-[12.5px] text-danger">{error}</p>
      ) : hint ? (
        <p className="text-[12.5px] text-ink-faint">{hint}</p>
      ) : null}
    </div>
  )
}

export function Select({
  label,
  value,
  onChange,
  options,
  hint,
  placeholder,
}: {
  label?: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  hint?: string
  placeholder?: string
}) {
  // Подпись пустого пункта переводится сама: вызывающих мест много, и
  // прокидывать один и тот же текст из каждого — лишний повод его забыть.
  const t = useT()
  return (
    <div className="grid gap-1">
      {label && <label className="text-[12.5px] font-medium text-ink-muted">{label}</label>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="spring h-9 w-full rounded-[8px] border border-line bg-surface-card px-2.5
                   text-[13.5px] text-ink transition hover:border-line-strong"
      >
        <option value="">{placeholder ?? t('common.selectEmpty')}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {hint && <p className="text-[12.5px] text-ink-faint">{hint}</p>}
    </div>
  )
}

/* ── Контейнеры ─────────────────────────────────────────────────────────── */

export function Card({
  children,
  className,
  pad = true,
}: {
  children: ReactNode
  className?: string
  pad?: boolean
}) {
  return <div className={cx('card', pad && 'p-4', className)}>{children}</div>
}

/** Панель инструментов над таблицей: поиск, фильтры, кнопки действий. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('mb-3 flex flex-wrap items-center gap-2', className)}>{children}</div>
  )
}

/* ── Таблица ────────────────────────────────────────────────────────────── */

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="card overflow-x-auto">
      <table className="dt">{children}</table>
    </div>
  )
}

export function Th({
  children,
  align = 'left',
  width,
}: {
  children?: ReactNode
  align?: 'left' | 'right' | 'center'
  width?: string
}) {
  return (
    <th
      scope="col"
      style={width ? { width } : undefined}
      className={cx(
        'sticky top-0 z-10 border-b border-line bg-surface-head px-3 py-2.5',
        'text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-faint',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
      )}
    >
      {children}
    </th>
  )
}

export function Td({
  children,
  align = 'left',
  className,
  colSpan,
}: {
  children?: ReactNode
  align?: 'left' | 'right' | 'center'
  className?: string
  colSpan?: number
}) {
  return (
    <td
      colSpan={colSpan}
      className={cx(
        'border-b border-line px-3 py-2.5 align-middle',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
    >
      {children}
    </td>
  )
}

export function Tr({
  children,
  muted,
  onClick,
}: {
  children: ReactNode
  muted?: boolean
  onClick?: () => void
}) {
  return (
    <tr
      onClick={onClick}
      className={cx(
        'spring transition last:[&>td]:border-b-0',
        muted && 'text-ink-faint',
        onClick && 'cursor-pointer',
        'hover:bg-surface-head',
      )}
    >
      {children}
    </tr>
  )
}

/* ── Мелочи ─────────────────────────────────────────────────────────────── */

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'warn' | 'danger' | 'info'
}) {
  const tones = {
    neutral: 'bg-surface-container text-ink-muted',
    accent: 'bg-accent-soft text-accent-ink',
    warn: 'bg-warn-soft text-warn',
    danger: 'bg-danger-soft text-danger',
    info: 'bg-info-soft text-ink-muted',
  }
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-[6px] px-1.5 py-0.5 text-[11.5px] font-medium',
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}

/** Показываем честно: чего нет и что сделать. Выдуманных данных в кабинете
 *  не держим — на демо их легко принять за настоящие. */
export function Empty({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="card grid place-items-center gap-1.5 px-6 py-12 text-center">
      <p className="text-[14px] font-medium text-ink">{title}</p>
      {hint && <p className="max-w-lg text-[13px] leading-relaxed text-ink-muted">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
      <div>
        <h1 className="text-[20px] font-semibold tracking-[-0.015em] text-ink">{title}</h1>
        {subtitle && <p className="mt-0.5 text-[13px] text-ink-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </header>
  )
}

/** Плитка показателя. Компактная: их бывает четыре в ряд, и каждая лишняя
 *  строка внутри съедает место у таблицы под ними. */
export function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string | number
  hint?: string
  tone?: 'warn' | 'danger'
}) {
  return (
    <div className="card px-3.5 py-3">
      <p className="text-[12px] text-ink-muted">{label}</p>
      <p
        className={cx(
          'mt-0.5 text-[22px] font-semibold leading-none tracking-[-0.02em]',
          tone === 'warn' && 'text-warn',
          tone === 'danger' && 'text-danger',
          !tone && 'text-ink',
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-[11.5px] text-ink-faint">{hint}</p>}
    </div>
  )
}

export function Rating({ value }: { value: number }) {
  const tone =
    value <= 2
      ? 'bg-danger-soft text-danger'
      : value === 3
        ? 'bg-warn-soft text-warn'
        : 'bg-accent-soft text-accent-ink'
  return (
    <span className={cx('inline-flex items-center rounded-[6px] px-1.5 py-0.5 text-[12.5px] font-semibold', tone)}>
      {value}/5
    </span>
  )
}
