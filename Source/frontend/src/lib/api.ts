/** Обёртка над fetch для кабинета.
 *
 *  credentials: 'include' обязателен — сессия живёт в httponly cookie, токена
 *  в JavaScript нет и быть не должно: так его не украдёт ни XSS, ни расширение.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfter?: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const data = text ? safeJson(text) : null

  if (!res.ok) {
    const retry = res.headers.get('retry-after')
    throw new ApiError(
      res.status,
      errorMessage(data, res.status),
      retry ? Number(retry) : undefined,
    )
  }
  return data as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/* ── Ошибки ─────────────────────────────────────────────────────────────── */

/** Перевод для слоя API.
 *
 *  Этот модуль живёт вне React и хук `useT` вызвать не может, а ошибки сервера
 *  показываются пользователю и обязаны быть на его языке. Поэтому провайдер
 *  i18n отдаёт сюда функцию перевода при каждой смене языка. До первого рендера
 *  работает запасной вариант на русском — им же пользуется страница входа,
 *  если провайдер почему-то не поднялся.
 */
type Translator = (key: string, vars?: Record<string, string | number>) => string

const RU_FALLBACK: Record<string, string> = {
  'err.fTitle': 'Должность',
  'err.fRole': 'Категория',
  'err.fSpecialty': 'Специальность',
  'err.fExperience': 'Опыт',
  'err.fSalaryMin': 'Оплата от',
  'err.fSalaryMax': 'Оплата до',
  'err.fSkills': 'Требования',
  'err.fCredentials': 'Документы',
  'err.fLanguages': 'Языки',
  'err.fSchedule': 'График',
  'err.fDistricts': 'Районы',
  'err.fCity': 'Город',
  'err.fSourceText': 'Описание',
  'err.fIntro': 'Приветствие перед интервью',
  'err.fQuestions': 'Вопросы',
  'err.fQuestion': 'Вопрос',
  'err.fIntent': 'Тема вопроса',
  'err.fEmail': 'Email',
  'err.fPassword': 'Пароль',
  'err.missing': 'не заполнено',
  'err.tooShort': 'не короче {n} символов',
  'err.tooLong': 'не длиннее {n} символов',
  'err.listShort': 'нужно хотя бы {n}',
  'err.listLong': 'не больше {n}',
  'err.min': 'не меньше {n}',
  'err.max': 'не больше {n}',
  'err.number': 'нужно число',
  'err.pattern': 'недопустимое значение',
  'err.checkFields': 'Проверьте заполненные поля',
  'err.generic': 'Ошибка {status}',
}

let translate: Translator = (key, vars) =>
  (RU_FALLBACK[key] ?? key).replace(/\{(\w+)\}/g, (whole, name: string) =>
    vars && name in vars ? String(vars[name]) : whole,
  )

export function setApiTranslator(fn: Translator): void {
  translate = fn
}

/** Имя поля из `loc` → ключ словаря. Поля, которых здесь нет, показываются
 *  как есть: техническое имя лучше молчания. */
const FIELD_KEY: Record<string, string> = {
  title: 'err.fTitle',
  role_category: 'err.fRole',
  specialty: 'err.fSpecialty',
  experience_min_months: 'err.fExperience',
  salary_min_uzs: 'err.fSalaryMin',
  salary_max_uzs: 'err.fSalaryMax',
  required_skills: 'err.fSkills',
  credential_requirements: 'err.fCredentials',
  required_languages: 'err.fLanguages',
  schedule: 'err.fSchedule',
  districts: 'err.fDistricts',
  city: 'err.fCity',
  source_text: 'err.fSourceText',
  interview_intro: 'err.fIntro',
  questions: 'err.fQuestions',
  question: 'err.fQuestion',
  intent: 'err.fIntent',
  email: 'err.fEmail',
  password: 'err.fPassword',
}

type PydanticError = {
  loc?: (string | number)[]
  msg?: string
  type?: string
  ctx?: Record<string, unknown>
}

/** Ограничения, которые реально стоят в наших схемах. Всё остальное падает
 *  на msg от Pydantic — по-английски, но хотя бы читаемо. */
function constraintText(e: PydanticError): string | null {
  const ctx = e.ctx ?? {}
  switch (e.type) {
    case 'missing':
      return translate('err.missing')
    case 'string_too_short':
      return translate('err.tooShort', { n: String(ctx.min_length) })
    case 'string_too_long':
      return translate('err.tooLong', { n: String(ctx.max_length) })
    case 'too_short':
      return translate('err.listShort', { n: String(ctx.min_length) })
    case 'too_long':
      return translate('err.listLong', { n: String(ctx.max_length) })
    case 'greater_than_equal':
      return translate('err.min', { n: String(ctx.ge) })
    case 'less_than_equal':
      return translate('err.max', { n: String(ctx.le) })
    case 'int_parsing':
    case 'float_parsing':
      return translate('err.number')
    case 'string_pattern_mismatch':
      return translate('err.pattern')
    default:
      return null
  }
}

function fieldError(item: unknown): string | null {
  if (typeof item === 'string') return item
  if (!item || typeof item !== 'object') return null
  const e = item as PydanticError

  // loc — это ['body', 'title'] или ['body', 'questions', 0, 'question'].
  // Берём последнее строковое звено: индекс элемента списка менеджеру
  // ничего не говорит, а имя поля говорит.
  const name = [...(e.loc ?? [])]
    .reverse()
    .find((part): part is string => typeof part === 'string' && part !== 'body')
  const label = name ? (FIELD_KEY[name] ? translate(FIELD_KEY[name]) : name) : null
  const text = constraintText(e) ?? e.msg
  if (!text) return null
  return label ? `${label}: ${text}` : text
}

/** FastAPI отдаёт detail либо строкой — так поднимают ошибки наши ручки, —
 *  либо списком объектов, если запрос не прошёл валидацию Pydantic (422).
 *  Второй случай нельзя класть в Error как есть: конструктор приводит значение
 *  к строке, и список объектов превращается в «[object Object]». Ровно это и
 *  видел менеджер вместо «Должность: не короче 3 символов».
 */
function errorMessage(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail

  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = [...new Set(detail.map(fieldError).filter(Boolean) as string[])]
    if (parts.length) return parts.join('; ')
  }
  if (detail && typeof detail === 'object') {
    const single = fieldError(detail)
    if (single) return single
  }
  return status === 422
    ? translate('err.checkFields')
    : translate('err.generic', { status })
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export type Me = {
  email: string
  full_name: string | null
  locale: string
  clinic_id: string
  clinic_name: string | null
  /** Роль в клинике. Приходит с сервера, в localStorage не кэшируется. */
  member_role: 'owner' | 'recruiter' | 'employee'
}

/** Кабинет или портал обучения.
 *
 *  Это выбор интерфейса, а не проверка доступа: разделы кабинета закрыты
 *  политиками RLS и зависимостью Manager на сервере. Подмена роли в браузере
 *  даст меню с пунктами, каждый из которых ответит 403.
 */
export const isManager = (me: Me): boolean =>
  me.member_role === 'owner' || me.member_role === 'recruiter'

export const auth = {
  me: () => api.get<Me>('/api/auth/me'),
  login: (email: string, password: string) =>
    api.post<Me>('/api/auth/login', { email, password }),
  logout: () => api.post<void>('/api/auth/logout'),
}
