/** Окно с QR-кодом.
 *
 *  Картинка рисуется на сервере по запросу и нигде не хранится: файл на диске
 *  пришлось бы инвалидировать и убирать, а нарисовать код — дело миллисекунд.
 *  Код внутри QR при этом постоянный: наклейку печатают один раз.
 *
 *  Что зашито в QR: deep link в бота @ishmedsifatbot. Пациент навёл камеру,
 *  попал в Telegram и сразу может приложить фото, голосовое или написать
 *  текстом — без набора в мобильном браузере и без регистрации.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { ReviewTarget } from '../lib/types'
import { Button } from './ui'

export function QrDialog({
  slug,
  title,
  onClose,
}: {
  slug: string
  title: string
  onClose: () => void
}) {
  const t = useT()
  const [copied, setCopied] = useState<'bot' | 'web' | null>(null)
  const svgSrc = `/api/reviews/qr/${slug}.svg?scale=10`

  // Ссылки берём с сервера, а не собираем в браузере: имя бота живёт в
  // конфигурации, и дублировать его во фронтенде значит однажды разойтись.
  const { data: targets } = useQuery<ReviewTarget[]>({
    queryKey: ['review-targets'],
    queryFn: () => api.get('/api/reviews/targets'),
  })
  const target = targets?.find((t) => t.slug === slug)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function copy(kind: 'bot' | 'web', text: string) {
    await navigator.clipboard.writeText(text)
    setCopied(kind)
    setTimeout(() => setCopied(null), 1800)
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="card w-full max-w-[400px] p-5"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${t('qr.dialogTitle')}: ${title}`}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <p className="text-[12px] text-ink-muted">{t('qr.dialogTitle')}</p>
            <h2 className="text-[16px] font-semibold tracking-[-0.01em]">{title}</h2>
          </div>
          <Button size="xs" variant="ghost" onClick={onClose} aria-label={t('common.close')}>
            ✕
          </Button>
        </div>

        <div className="grid place-items-center rounded-[10px] border border-line bg-white p-4">
          <img src={svgSrc} alt={t('qr.dialogAlt', { title })} className="size-[220px]" />
        </div>

        <div className="mt-3 rounded-[8px] bg-surface-head px-3 py-2">
          <p className="text-[11.5px] text-ink-muted">{t('qr.leadsTo')}</p>
          <p className="mt-0.5 break-all font-mono text-[11.5px] text-ink">
            {target?.url ?? `t.me/…?start=${slug}`}
          </p>
        </div>

        <div className="mt-3 grid gap-1.5">
          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant="soft"
              className="flex-1"
              disabled={!target}
              onClick={() => target && copy('bot', target.url)}
            >
              {copied === 'bot' ? t('common.copied') : t('common.copyLink')}
            </Button>
            <a
              href={`/api/reviews/qr/${slug}.png?scale=14`}
              download
              className="spring inline-flex h-7.5 flex-1 items-center justify-center rounded-[8px]
                         border border-line-strong bg-surface-card px-3 text-[13px] font-medium
                         text-ink transition hover:bg-surface-container"
            >
              {t('qr.downloadPng')}
            </a>
          </div>
          <a
            href={svgSrc}
            target="_blank"
            rel="noreferrer"
            className="spring inline-flex h-7.5 items-center justify-center rounded-[8px] px-3
                       text-[13px] text-ink-muted transition hover:bg-surface-container"
          >
            {t('qr.openSvg')}
          </a>
          {target?.web_url && (
            <button
              onClick={() => copy('web', target.web_url!)}
              className="spring inline-flex h-7 items-center justify-center rounded-[8px] px-3
                         text-[12px] text-ink-faint transition hover:bg-surface-container"
            >
              {copied === 'web' ? t('common.copied') : t('qr.webForm')}
            </button>
          )}
        </div>

        <p className="mt-3 text-[11.5px] leading-relaxed text-ink-faint">
          {t('qr.dialogHint')}
        </p>
      </div>
    </div>
  )
}
