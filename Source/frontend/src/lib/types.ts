export type Unit = {
  id: string
  parent_id: string | null
  name: string
  district: string | null
  district_name: string | null
  level: number
  qr_slug: string | null
  positions_count: number
  seats: number
  seats_filled: number
  employees_count: number
  reviews_count: number
}

export type Position = {
  id: string
  unit_id: string | null
  unit_name: string | null
  title: string
  role_category: string
  specialty: string | null
  role_name: string | null
  specialty_name: string | null
  seats: number
  seats_filled: number
  seats_open: number
}

export type Employee = {
  id: string
  full_name: string
  status: 'onboarding' | 'active' | 'suspended' | 'dismissed'
  hired_at: string | null
  dismissed_at: string | null
  work_phone: string | null
  work_email: string | null
  unit_id: string | null
  unit_name: string | null
  staff_position_id: string | null
  position_title: string | null
  role_name: string | null
  specialty_name: string | null
  qr_slug: string | null
  reviews_count: number
  avg_rating: number | null
}

export type ReviewTarget = {
  id: string
  kind: 'unit' | 'employee'
  slug: string
  title: string
  subtitle: string | null
  is_active: boolean
  unit_name: string | null
  employee_name: string | null
  reviews_count: number
  avg_rating: number | null
  url: string          // deep link в бота отзывов — то, что зашито в QR
  web_url?: string     // резервная веб-форма для пациентов без Telegram
}

export type Review = {
  id: string
  rating: number
  good_tags: string[]
  bad_tags: string[]
  comment: string | null
  contact_phone: string | null
  wants_callback: boolean
  created_at: string
  handled_at: string | null
  target_kind: 'unit' | 'employee'
  target_title: string
  unit_name: string | null
  employee_name: string | null
  attachments: ReviewAttachment[]
}

/** Вложение отзыва. Самого файла здесь нет: он живёт в Telegram, кабинет
 *  отдаёт его прокси-эндпоинтом по id. */
export type ReviewAttachment = {
  id: string
  kind: 'photo' | 'voice' | 'document'
  transcript: string | null
  duration: number | null
}

export type ReviewSummary = {
  total: number
  last_week: number
  avg_rating: number | null
  low: number
  low_unhandled: number
  callbacks_pending: number
}

export type TagStat = { code: string; name_ru: string; bad: number; good: number }

export type Dict = { code: string; name_ru: string; name_uz: string }
export type SpecialtyDict = Dict & { role_category: string }

export type Dictionaries = {
  roles: Dict[]
  specialties: SpecialtyDict[]
  districts: Dict[]
  review_tags: Dict[]
}

/* ── Вакансии и авто-интервью ──────────────────────────────────────────── */

export type JobStatus = 'draft' | 'active' | 'paused' | 'closed'
export type PlanStatus = 'none' | 'draft' | 'approved'

export type JobRow = {
  id: string
  title: string
  role_category: string
  specialty: string | null
  specialty_name: string | null
  city: string
  schedule: string[]
  salary_min_uzs: number | null
  salary_max_uzs: number | null
  status: JobStatus
  public_code: string
  published_at: string | null
  closed_at: string | null
  interview_plan_status: PlanStatus
  applications_count: number
  questions_count: number
  interviews_done: number
  created_at: string
  updated_at: string
}

export type JobSummary = {
  drafts: number
  active: number
  closed: number
  applications: number
  interviews_done: number
}

/** Поля вакансии. Приходят и от модели после разбора текста, и из базы. */
export type JobFields = {
  title: string
  role_category: string | null
  specialty: string | null
  experience_min_months: number | null
  required_skills: string[]
  required_languages: string[]
  city: string
  districts: string[]
  schedule: string[]
  salary_min_uzs: number | null
  salary_max_uzs: number | null
  credential_requirements: string[]
  source_text?: string | null
  interview_intro?: string | null
}

export type JobQuestion = {
  id: string
  ord: number
  question: string
  intent: string | null
  origin: 'ai' | 'manual'
  edited: boolean
}

export type ApplicationRow = {
  application_id: string
  status: 'sent' | 'viewed' | 'accepted' | 'declined' | 'withdrawn'
  applied_at: string
  message: string | null
  job_id: string
  job_title: string
  interview_id: string | null
  interview_status: 'in_progress' | 'completed' | 'abandoned' | null
  summary: string | null
  gaps: string[] | null
  follow_ups: string[] | null
  extraction: Record<string, unknown> | null
  started_at: string | null
  finished_at: string | null
  voice_answers: number
  answered: number
  total: number
  follow_ups_asked: number
  role_category: string | null
  specialty: string | null
  experience_months: number | null
  skills: string[] | null
  languages: string[] | null
  salary_expectation_uzs: number | string | null
  /** Имя — не контакт: позвонить по нему нельзя. Телефон открывается отдельно. */
  candidate_name: string | null
}

/** Ответ product.reveal_application_contact. Приходит только после принятия
 *  отклика, и каждое открытие пишется в журнал согласий. */
export type RevealedContact = {
  phone: string | null
  telegram_username: string | null
}

export type InterviewTurn = {
  ord: number
  kind: 'question' | 'follow_up'
  question_text: string
  answer_kind: 'text' | 'voice' | 'button' | 'skipped' | null
  answer_text: string | null
  voice_seconds: number | null
  asked_at: string
  answered_at: string | null
}

/* ── Обучение ───────────────────────────────────────────────────────────── */

export type CourseStatus = 'draft' | 'published' | 'archived'
export type AssignmentStatus = 'assigned' | 'in_progress' | 'passed' | 'failed'

export type CourseRow = {
  id: string
  title: string
  summary: string | null
  status: CourseStatus
  pass_score: number
  role_category: string | null
  specialty: string | null
  role_name: string | null
  specialty_name: string | null
  lessons_count: number
  questions_count: number
  assigned: number
  passed: number
  failed: number
  in_progress: number
  not_started: number
  avg_score: number | null
  created_at: string
  updated_at: string
}

export type CourseSummary = {
  published: number
  drafts: number
  assigned: number
  passed: number
  failed: number
  overdue: number
}

export type CourseLesson = {
  id: string
  ord: number
  title: string
  content: string
}

/** Вопрос в кабинете менеджера — с отметкой верного варианта. У сотрудника
 *  такого типа нет и быть не может: флаг не доезжает до его браузера. */
export type CourseQuestionWithKey = {
  id: string
  ord: number
  text: string
  explanation: string | null
  options: { id: string; text: string; is_correct: boolean }[]
}

export type CourseDetail = {
  id: string
  title: string
  summary: string | null
  status: CourseStatus
  pass_score: number
  role_name: string | null
  specialty_name: string | null
  created_at: string
  lessons: CourseLesson[]
  questions: CourseQuestionWithKey[]
  assignments: AssignmentRow[]
}

export type AssignmentRow = {
  id: string
  status: AssignmentStatus
  due_at: string | null
  assigned_at: string
  completed_at: string | null
  best_score: number | null
  course_id: string
  course_title: string
  pass_score: number
  employee_id: string
  employee_name: string
  unit_name: string | null
  attempts: number
  last_attempt_id: string | null
}

/* ── Портал сотрудника ──────────────────────────────────────────────────── */

export type EmployeeCard = {
  employee_id: string
  full_name: string
  unit_name: string | null
  role_name: string | null
  clinic_name: string | null
}

export type MyCourse = {
  assignment_id: string
  status: AssignmentStatus
  due_at: string | null
  assigned_at: string
  completed_at: string | null
  best_score: number | null
  course_id: string
  title: string
  summary: string | null
  pass_score: number
  lessons_count: number
  questions_count: number
  attempts: number
  last_attempt_id: string | null
}

export type MyAttempt = {
  id: string
  score: number | null
  correct_count: number | null
  total_count: number | null
  passed: boolean | null
  started_at: string
  finished_at: string | null
}

export type MyCourseDetail = {
  assignment_id: string
  status: AssignmentStatus
  due_at: string | null
  best_score: number | null
  completed_at: string | null
  course_id: string
  title: string
  summary: string | null
  pass_score: number
  questions_count: number
  lessons: CourseLesson[]
  attempts: MyAttempt[]
}

/** Вопрос попытки. Правильного варианта здесь нет: его не отдаёт ни функция
 *  БД, ни колонка — право на чтение is_correct у приложения отозвано. */
export type AttemptQuestion = {
  question_id: string
  ord: number
  text: string
  options: { id: string; text: string }[]
}

export type AttemptReviewRow = {
  question_id: string
  ord: number
  text: string
  explanation: string | null
  chosen_id: string | null
  correct_id: string | null
  is_right: boolean | null
}

export type AttemptResult = {
  score: number
  correct_count: number
  total_count: number
  passed: boolean
  review: AttemptReviewRow[]
}

/** Отзыв о себе. Без телефона пациента и без вложений — они адресованы
 *  руководству клиники, а не тому, о ком отзыв. */
export type MyReview = {
  id: string
  rating: number
  good_tags: string[]
  bad_tags: string[]
  comment: string | null
  locale: string
  handled_at: string | null
  created_at: string
}

export type MyReviewSummary = {
  total: number
  last_week: number
  avg_rating: number | null
  low: number
}

/* ── Подбор: пул медиков, совпадения, приглашения ───────────────────────── */

/** Карточка из общего поиска. Анонимна по построению: `product.pool_candidates`
 *  не отдаёт ни имени, ни телефона, ни транскрипта. Это ограничение базы, а не
 *  недосмотр интерфейса — до приглашения и accept человек никого не выбирал. */
export type PoolCandidate = {
  candidate_id: string
  role_category: string | null
  role_name_ru: string | null
  role_name_uz: string | null
  specialty: string | null
  specialty_name_ru: string | null
  specialty_name_uz: string | null
  experience_months: number | null
  skills: string[]
  languages: string[]
  city: string
  districts: string[]
  schedule: string[]
  salary_min_uzs: number | string | null
  credential_claims: string[]
  has_contact: boolean
  updated_at: string
  total_count: number
}

export type MatchLevel = 'strong' | 'possible'
export type InvitationStatus = 'sent' | 'accepted' | 'declined' | 'expired' | 'withdrawn'

/** Совпадение из product.matches вместе с карточкой и состоянием приглашения.
 *  `score_internal` наружу не показываем: число сравнивает людей между собой,
 *  но не описывает их, и «62 против 58» — не то, на основании чего зовут на
 *  работу. Показываем уровень, причины и пробелы. */
export type MatchRow = {
  match_id: string
  candidate_id: string
  level: MatchLevel
  score_internal: number
  hard_constraints_passed: boolean
  /** Коды, а не фразы: матч видят обе стороны, каждая на своём языке.
   *  Формат `код` или `код:аргумент`. */
  reasons: string[]
  gaps: string[]
  algorithm_version: string
  created_at: string
  role_category: string | null
  specialty: string | null
  experience_months: number | null
  skills: string[]
  languages: string[]
  city: string
  districts: string[]
  schedule: string[]
  salary_min_uzs: number | string | null
  credential_claims: string[]
  self_filled: boolean
  role_name: string | null
  specialty_name: string | null
  invitation_id: string | null
  invitation_status: InvitationStatus | null
  invited_at: string | null
  responded_at: string | null
  has_application: boolean
}

export type MatchableJob = {
  job_id: string
  title: string
  status: JobStatus
  public_code: string
  interview_plan_status: PlanStatus
  role_category: string
  specialty: string | null
  specialty_name: string | null
  experience_min_months: number | null
  required_skills: string[]
  required_languages: string[]
  city: string
  districts: string[]
  schedule: string[]
  salary_min_uzs: number | null
  salary_max_uzs: number | null
  credential_requirements: string[]
  matches_count: number
  invitations_count: number
}

export type MatchDictionaries = {
  roles: Dict[]
  specialties: SpecialtyDict[]
  districts: Dict[]
  schedules: Dict[]
}

/** Итог пересчёта. `excluded` — сводка по отсеянным: «пусто» без объяснения
 *  читается как поломка, а «отсеяно 34, из них 20 по роли» показывает, что
 *  править в требованиях. */
export type RecomputeResult = {
  matches: MatchRow[]
  excluded: Record<string, number>
  excluded_total: number
  strong: number
  wants_more_money: number
}

export type InvitationRow = {
  invitation_id: string
  invitation_status: InvitationStatus
  message: string | null
  sent_at: string
  responded_at: string | null
  job_id: string
  job_title: string
  candidate_id: string
  role_category: string | null
  specialty: string | null
  specialty_name: string | null
  experience_months: number | null
  salary_min_uzs: number | string | null
  has_application: boolean
}
