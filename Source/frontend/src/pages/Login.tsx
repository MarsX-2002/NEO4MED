import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, auth } from '../lib/api'
import { useT } from '../lib/i18n'
import { Button, Field } from '../components/ui'

export default function Login() {
  const t = useT()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const me = await auth.login(email, password)
      qc.setQueryData(['me'], me)
      navigate('/', { replace: true })
    } catch (err) {
      // Сервер сознательно отвечает одинаково на неверный пароль и неизвестный
      // адрес, поэтому здесь просто показываем его текст, ничего не уточняя.
      setError(err instanceof ApiError ? err.message : t('login.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-8 flex items-center gap-3">
          <span
            aria-hidden
            className="grid size-11 place-items-center rounded-2xl bg-accent text-white"
          >
            <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M3 12h3.5l2-4.5 3 9 2.5-6 1.5 3H21" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <div>
            <p className="text-[19px] font-semibold tracking-[-0.02em]">IshMed</p>
            <p className="text-[13px] text-ink-muted">{t('shell.cabinet')}</p>
          </div>
        </div>

        <form onSubmit={submit} className="grid gap-4" noValidate>
          {error && (
            <div
              role="alert"
              className="rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-[14px] text-danger"
            >
              {error}
            </div>
          )}

          <Field
            label={t('login.email')}
            type="email"
            name="email"
            autoComplete="username"
            required
            autoFocus
            placeholder="clinica@ishmed.uz"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Field
            label={t('login.password')}
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          <Button type="submit" loading={busy} className="mt-1 h-11">
            {t('login.submit')}
          </Button>
        </form>

        <p className="mt-7 text-[13px] leading-relaxed text-ink-faint">
          {t('login.medicHint')}{' '}
          <a className="text-accent hover:underline" href="https://t.me/ishmedbot">
            @ishmedbot
          </a>
          .
        </p>
      </div>
    </div>
  )
}
