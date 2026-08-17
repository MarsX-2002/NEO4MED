-- 018_review_via_bot.sql
-- Отзыв переезжает из веб-формы в отдельного Telegram-бота @ishmedsifatbot.
--
-- Что это меняет по существу:
--   * пациент может приложить фото и голосовое, а не только текст;
--   * личность пациента — telegram_user_id, а не хэш адреса. Это надёжнее
--     для лимитов: сменить IP проще, чем аккаунт;
--   * голосовое расшифровываем через уже развёрнутый gpt-4o-transcribe, чтобы
--     менеджер читал текст, а не слушал запись.
--
-- Веб-форму не удаляем: она остаётся резервом, если у пациента нет Telegram.

DO $$ BEGIN
    CREATE TYPE product.review_source AS ENUM ('web', 'telegram');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE product.reviews
    ADD COLUMN IF NOT EXISTS source product.review_source NOT NULL DEFAULT 'web',
    ADD COLUMN IF NOT EXISTS telegram_user_id bigint;

COMMENT ON COLUMN product.reviews.telegram_user_id IS
  'Кто прислал отзыв в боте. Персональных данных не несёт: это идентификатор '
  'Telegram, а не имя и не телефон.';

CREATE INDEX IF NOT EXISTS ix_reviews_tg_ratelimit
    ON product.reviews (target_id, telegram_user_id, created_at DESC)
    WHERE telegram_user_id IS NOT NULL;

-- ══ Вложения ══════════════════════════════════════════════════════════════════
-- Отдельной таблицей, а не колонками: пациент вполне может прислать два фото
-- подряд, и переделывать схему из-за этого не хочется.
DO $$ BEGIN
    CREATE TYPE product.attachment_kind AS ENUM ('photo', 'voice', 'document');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS product.review_attachments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   uuid NOT NULL REFERENCES product.reviews(id) ON DELETE CASCADE,
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    kind        product.attachment_kind NOT NULL,

    -- file_id Telegram, а не сам файл. Хранить медиа у себя незачем: Telegram
    -- отдаёт его по запросу, а нам не нужен ни диск, ни ответственность за
    -- фотографии пациентов.
    file_id     text NOT NULL,
    file_unique_id text,
    mime_type   text,
    file_size   integer,
    duration    integer,          -- для голосового, секунды

    -- Расшифровка голосового. Менеджер должен читать, а не слушать: чтение
    -- десяти отзывов занимает минуту, прослушивание — десять.
    transcript  text,
    transcribed_at timestamptz,

    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_review_attachments_review
    ON product.review_attachments (review_id);

COMMENT ON TABLE product.review_attachments IS
  'Фото и голосовые из бота отзывов. Храним ссылку Telegram, а не файл.';

ALTER TABLE product.review_attachments OWNER TO ezgumed;
ALTER TABLE product.review_attachments ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_review_attachments_tenant ON product.review_attachments
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

GRANT SELECT, INSERT, UPDATE ON product.review_attachments TO ishmed_app;

-- ══ Приём отзыва из бота ══════════════════════════════════════════════════════
-- Отдельная функция, а не флаг в существующей: у веба и у бота разные
-- идентичности и разные лимиты, и смешивать их в одной подписи значит
-- через месяц не понимать, какой аргумент когда обязателен.
CREATE OR REPLACE FUNCTION product.submit_review_telegram(
    p_slug             text,
    p_telegram_user_id bigint,
    p_rating           smallint,
    p_good_tags        text[]  DEFAULT '{}',
    p_bad_tags         text[]  DEFAULT '{}',
    p_comment          text    DEFAULT NULL,
    p_contact_phone    text    DEFAULT NULL,
    p_wants_callback   boolean DEFAULT false,
    p_locale           text    DEFAULT 'ru'
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    tgt        product.review_targets;
    recent_cnt integer;
    new_id     uuid;
    known_tags text[];
BEGIN
    SELECT * INTO tgt FROM product.review_targets WHERE slug = p_slug;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'опрос не найден' USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT tgt.is_active THEN
        RAISE EXCEPTION 'опрос закрыт' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_rating IS NULL OR p_rating < 1 OR p_rating > 5 THEN
        RAISE EXCEPTION 'оценка должна быть от 1 до 5' USING ERRCODE = 'check_violation';
    END IF;

    -- Один отзыв на цель в сутки с одного аккаунта. Сутки, а не час, как в
    -- вебе: аккаунт Telegram — устойчивая личность, и повторный отзыв с него
    -- в тот же день это уже не «другой пациент за тем же Wi-Fi».
    SELECT count(*) INTO recent_cnt
    FROM product.reviews
    WHERE target_id = tgt.id
      AND telegram_user_id = p_telegram_user_id
      AND created_at > now() - interval '24 hours';
    IF recent_cnt >= 1 THEN
        RAISE EXCEPTION 'отзыв на этот кабинет уже принят сегодня'
            USING ERRCODE = 'too_many_connections';
    END IF;

    SELECT array_agg(code) INTO known_tags FROM product.review_tags;
    IF NOT (coalesce(p_good_tags, '{}') <@ coalesce(known_tags, '{}')
            AND coalesce(p_bad_tags, '{}') <@ coalesce(known_tags, '{}')) THEN
        RAISE EXCEPTION 'неизвестный тег' USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO product.reviews (
        clinic_id, target_id, rating, good_tags, bad_tags, comment,
        contact_phone, wants_callback, locale, source, telegram_user_id
    ) VALUES (
        tgt.clinic_id, tgt.id, p_rating,
        coalesce(p_good_tags, '{}'), coalesce(p_bad_tags, '{}'),
        nullif(btrim(left(coalesce(p_comment, ''), 2000)), ''),
        nullif(btrim(coalesce(p_contact_phone, '')), ''),
        coalesce(p_wants_callback, false)
            AND nullif(btrim(coalesce(p_contact_phone, '')), '') IS NOT NULL,
        coalesce(p_locale, 'ru'), 'telegram', p_telegram_user_id
    )
    RETURNING id INTO new_id;

    RETURN new_id;
END $$;

COMMENT ON FUNCTION product.submit_review_telegram IS
  'Приём отзыва из бота @ishmedsifatbot. Лимит — один отзыв на цель в сутки '
  'с одного аккаунта Telegram.';

-- Вложение цепляется к отзыву тоже через функцию: бот работает без контекста
-- тенанта, а clinic_id должен выводиться из самого отзыва, а не приходить
-- из запроса.
CREATE OR REPLACE FUNCTION product.attach_to_review(
    p_review_id      uuid,
    p_kind           product.attachment_kind,
    p_file_id        text,
    p_file_unique_id text DEFAULT NULL,
    p_mime_type      text DEFAULT NULL,
    p_file_size      integer DEFAULT NULL,
    p_duration       integer DEFAULT NULL,
    p_transcript     text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_clinic uuid;
    new_id   uuid;
BEGIN
    SELECT clinic_id INTO v_clinic FROM product.reviews WHERE id = p_review_id;
    IF v_clinic IS NULL THEN
        RAISE EXCEPTION 'отзыв не найден' USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO product.review_attachments (
        review_id, clinic_id, kind, file_id, file_unique_id,
        mime_type, file_size, duration, transcript,
        transcribed_at
    ) VALUES (
        p_review_id, v_clinic, p_kind, p_file_id, p_file_unique_id,
        p_mime_type, p_file_size, p_duration, nullif(btrim(coalesce(p_transcript, '')), ''),
        CASE WHEN nullif(btrim(coalesce(p_transcript, '')), '') IS NOT NULL THEN now() END
    )
    RETURNING id INTO new_id;

    RETURN new_id;
END $$;

-- Бот показывает пациенту название кабинета и список тегов — ему нужен тот же
-- публичный доступ, что и веб-странице.
GRANT EXECUTE ON FUNCTION
      product.submit_review_telegram(text, bigint, smallint, text[], text[], text, text, boolean, text),
      product.attach_to_review(uuid, product.attachment_kind, text, text, text, integer, integer, text)
      TO ishmed_app;
