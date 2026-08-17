-- 001_extensions.sql
-- Требует суперпользователя. Расширения ставим в public, чтобы типы (vector)
-- были видны из всех схем без явного search_path.

CREATE EXTENSION IF NOT EXISTS vector      SCHEMA public;  -- pgvector: тип vector, HNSW/IVFFlat
CREATE EXTENSION IF NOT EXISTS pg_trgm     SCHEMA public;  -- нечёткий поиск по названиям (лексический слой гибрида)
CREATE EXTENSION IF NOT EXISTS unaccent    SCHEMA public;  -- нормализация ў/ғ/қ и латиницы с диакритикой
CREATE EXTENSION IF NOT EXISTS btree_gin   SCHEMA public;  -- составные GIN (tsvector + скалярные фильтры)
CREATE EXTENSION IF NOT EXISTS pgcrypto    SCHEMA public;  -- digest() для content_hash эмбеддингов
