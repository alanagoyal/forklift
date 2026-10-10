-- Booklist application schema subset inspected read-only through Supabase, 2026-10-10.

-- Synthetic rows only. Existing RPC definitions and policy expressions preserved.

CREATE SCHEMA extensions;

CREATE EXTENSION vector WITH SCHEMA extensions;

CREATE EXTENSION "uuid-ossp" WITH SCHEMA extensions;

CREATE EXTENSION pg_trgm;

SET search_path = public, extensions;

CREATE ROLE anon NOLOGIN;

CREATE ROLE authenticated NOLOGIN;

CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD 'local-booklist-only';

GRANT anon TO authenticator;

CREATE TABLE books (
  "id" uuid DEFAULT uuid_generate_v4() NOT NULL PRIMARY KEY,
  "title" text NOT NULL,
  "author" text NOT NULL,
  "description" text,
  "genre" text[] NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "updated_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "amazon_url" text,
  "title_embedding" vector(1536),
  "author_embedding" vector(1536),
  "description_embedding" vector(1536),
  "similar_books" jsonb,
  "recommendation_percentile" numeric
);

ALTER TABLE books ENABLE ROW LEVEL SECURITY;

CREATE TABLE people (
  "id" uuid DEFAULT uuid_generate_v4() NOT NULL PRIMARY KEY,
  "full_name" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "updated_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "url" text,
  "type" text,
  "description" text,
  "description_embedding" vector,
  "similar_people" jsonb,
  "recommendation_percentile" numeric
);

ALTER TABLE people ENABLE ROW LEVEL SECURITY;

CREATE TABLE recommendations (
  "id" uuid DEFAULT uuid_generate_v4() NOT NULL PRIMARY KEY,
  "person_id" uuid NOT NULL,
  "book_id" uuid NOT NULL,
  "source" text NOT NULL,
  "source_link" text,
  "created_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
  "updated_at" timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anon + auth to update" ON books FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Anyone can read books" ON books FOR SELECT TO public USING (true);

CREATE POLICY "Enable insert for anon + auth users only" ON books FOR INSERT TO anon, authenticated WITH CHECK (true);

CREATE POLICY "Allow inserts for anon + auth on people" ON people FOR INSERT TO anon, authenticated WITH CHECK (true);

CREATE POLICY "Anon + auth can update" ON people FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);

CREATE POLICY "Anyone can read people" ON people FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "No deletes allowed on people" ON people FOR DELETE TO anon, authenticated USING (false);

CREATE POLICY "Allow inserts from anon + auth on recommendations" ON recommendations FOR INSERT TO anon, authenticated WITH CHECK (true);

CREATE POLICY "Anyone can read recommendations" ON recommendations FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "No deletes allowed on recommendations" ON recommendations FOR DELETE TO anon, authenticated USING (false);

CREATE POLICY "No updates allowed on recommendations" ON recommendations FOR UPDATE TO anon, authenticated WITH CHECK (false);

CREATE OR REPLACE FUNCTION public.get_recommender_with_books(p_limit integer DEFAULT 1000, p_offset integer DEFAULT 0)
 RETURNS TABLE(id uuid, full_name text, type text, url text, description text, recommendations jsonb, related_recommenders jsonb, similar_people jsonb, _book_count bigint, recommendation_percentile numeric)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
declare
  recommender_ids uuid[];
begin
  -- Get all recommender IDs first
  select array_agg(p.id) into recommender_ids
  from people p
  where exists (
    select 1 from recommendations r where r.person_id = p.id
  );

  return query
    with recommender_base as (
      select
        p.id,
        p.full_name,
        p.type,
        p.url,
        p.description,
        p.similar_people,
        jsonb_agg(
          jsonb_build_object(
            'id', b.id,
            'title', b.title,
            'author', b.author,
            'description', b.description,
            'genre', b.genre,
            'amazon_url', b.amazon_url,
            'source', r.source,
            'source_link', r.source_link
          )
          order by b.title
        ) filter (where b.id is not null) as recommendations,
        count(b.id)::bigint as book_count,
        p.recommendation_percentile
      from people p
      inner join recommendations r on r.person_id = p.id
      inner join books b on b.id = r.book_id
      group by p.id, p.full_name, p.type, p.url, p.description, p.similar_people, p.recommendation_percentile
    ),
    related as (
      select * from get_related_recommenders(recommender_ids)
    )
    select
      rb.id,
      rb.full_name,
      rb.type,
      rb.url,
      rb.description,
      rb.recommendations,
      coalesce(r.related_recommenders, '[]'::jsonb) as related_recommenders,
      rb.similar_people,
      rb.book_count as _book_count,
      rb.recommendation_percentile
    from recommender_base rb
    left join related r on r.recommender_id = rb.id
    order by rb.book_count desc, rb.full_name
    limit p_limit
    offset p_offset;
end;
$function$
;

CREATE OR REPLACE FUNCTION public.semantic_search_books(embedding_input vector, match_count integer DEFAULT 500, min_similarity double precision DEFAULT 0.8, search_query text DEFAULT NULL::text)
 RETURNS TABLE(id uuid, title text, author text, description text, genre text[], similarity double precision)
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN QUERY
  WITH text_matches AS (
    -- First, get exact text matches with perfect similarity
    SELECT
      b.id,
      b.title,
      b.author,
      b.description,
      b.genre,
      1.0 as similarity,
      1 as priority -- Highest priority for text matches
    FROM books b
    WHERE search_query IS NOT NULL
      AND (
        LOWER(b.title) LIKE LOWER('%' || search_query || '%')
        OR LOWER(b.author) LIKE LOWER('%' || search_query || '%')
      )
  ),
  semantic_matches AS (
    -- Then get semantic matches
    SELECT
      b.id,
      b.title,
      b.author,
      b.description,
      b.genre,
      1 - (b.description_embedding <=> embedding_input) as similarity,
      2 as priority -- Lower priority for semantic matches
    FROM books b
    WHERE b.description_embedding IS NOT NULL
      AND 1 - (b.description_embedding <=> embedding_input) >= min_similarity
      -- Exclude items already found by text search
      AND NOT EXISTS (
        SELECT 1 FROM text_matches tm WHERE tm.id = b.id
      )
  ),
  combined AS (
    SELECT * FROM text_matches
    UNION ALL
    SELECT * FROM semantic_matches
  )
  SELECT
    c.id,
    c.title,
    c.author,
    c.description,
    c.genre,
    c.similarity
  FROM combined c
  ORDER BY
    c.priority ASC,  -- Text matches first
    c.similarity DESC, -- Then by similarity within each group
    c.title ASC
  LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.semantic_search_people(embedding_input vector, match_count integer DEFAULT 500, min_similarity double precision DEFAULT 0.8, search_query text DEFAULT NULL::text)
 RETURNS TABLE(id uuid, full_name text, type text, description text, url text, similarity double precision)
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN QUERY
  WITH text_matches AS (
    -- First, get exact text matches with perfect similarity
    SELECT
      p.id,
      p.full_name,
      p.type,
      p.description,
      p.url,
      1.0 as similarity,
      1 as priority -- Highest priority for text matches
    FROM people p
    WHERE search_query IS NOT NULL
      AND (
        LOWER(p.full_name) LIKE LOWER('%' || search_query || '%')
        OR LOWER(p.type) LIKE LOWER('%' || search_query || '%')
      )
  ),
  semantic_matches AS (
    -- Then get semantic matches
    SELECT
      p.id,
      p.full_name,
      p.type,
      p.description,
      p.url,
      1 - (p.description_embedding <=> embedding_input) as similarity,
      2 as priority -- Lower priority for semantic matches
    FROM people p
    WHERE p.description_embedding IS NOT NULL
      AND 1 - (p.description_embedding <=> embedding_input) >= min_similarity
      -- Exclude items already found by text search
      AND NOT EXISTS (
        SELECT 1 FROM text_matches tm WHERE tm.id = p.id
      )
  ),
  combined AS (
    SELECT * FROM text_matches
    UNION ALL
    SELECT * FROM semantic_matches
  )
  SELECT
    c.id,
    c.full_name,
    c.type,
    c.description,
    c.url,
    c.similarity
  FROM combined c
  ORDER BY
    c.priority ASC,  -- Text matches first
    c.similarity DESC, -- Then by similarity within each group
    c.full_name ASC
  LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.get_related_books(book_ids uuid[], p_limit integer DEFAULT 3)
 RETURNS json
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN (
    SELECT json_object_agg(
      book_id,
      related_books
    )
    FROM (
      SELECT
        book_id,
        json_agg(
          json_build_object(
            'id', id,
            'title', title,
            'author', author,
            'description', description,
            'amazon_url', amazon_url,
            '_recommendation_count', recommendation_count,
            '_shared_count', shared_recommenders
          )
        ) as related_books
      FROM (
        SELECT
          br.book_id,
          b.id,
          b.title,
          b.author,
          b.description,
          b.amazon_url,
          COUNT(DISTINCT r.person_id)::int as recommendation_count,
          br.shared_recommenders,
          row_number() OVER (PARTITION BY br.book_id ORDER BY br.shared_recommenders DESC) as rn
        FROM (
          SELECT
            r1.book_id,
            r2.book_id as related_id,
            COUNT(DISTINCT r1.person_id) as shared_recommenders
          FROM recommendations r1
          JOIN recommendations r2 ON r1.person_id = r2.person_id
          WHERE r1.book_id = ANY(book_ids)
          AND r1.book_id != r2.book_id
          GROUP BY r1.book_id, r2.book_id
        ) br
        JOIN books b ON b.id = br.related_id
        LEFT JOIN recommendations r ON b.id = r.book_id
        GROUP BY br.book_id, b.id, b.title, b.author, b.description, b.amazon_url, br.shared_recommenders
      ) ranked
      WHERE rn <= p_limit
      GROUP BY book_id
    ) book_related
  );
END;
$function$
;

CREATE OR REPLACE FUNCTION public.get_book_recommendations(book_ids uuid[])
 RETURNS json
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN (
    SELECT json_object_agg(
      book_id,
      recommendations
    )
    FROM (
      SELECT
        r.book_id,
        json_agg(
          json_build_object(
            'recommender', json_build_object(
              'id', p.id,
              'full_name', p.full_name,
              'url', p.url,
              'type', p.type
            ),
            'source', r.source,
            'source_link', r.source_link
          )
        ) as recommendations
      FROM recommendations r
      JOIN people p ON r.person_id = p.id
      WHERE r.book_id = ANY(book_ids)
      GROUP BY r.book_id
    ) book_recommendations
  );
END;
$function$
;

CREATE OR REPLACE FUNCTION public.get_related_recommenders(p_recommender_id uuid, p_limit integer DEFAULT 3)
 RETURNS TABLE(id uuid, full_name text, url text, type text, shared_books text, shared_count bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
  RETURN QUERY
  WITH recommender_books AS (
    SELECT book_id
    FROM recommendations
    WHERE person_id = p_recommender_id
  ),
  similar_recommenders AS (
    SELECT
      r.person_id,
      COUNT(*) AS shared_count,
      STRING_AGG(b.title, ', ' ORDER BY b.title) AS shared_books
    FROM recommendations r
    JOIN books b ON b.id = r.book_id
    WHERE r.book_id IN (SELECT book_id FROM recommender_books)
      AND r.person_id != p_recommender_id
    GROUP BY r.person_id
    ORDER BY shared_count DESC
    LIMIT p_limit
  )
  SELECT
    p.id,
    p.full_name,
    p.url,
    p.type,
    sr.shared_books,
    sr.shared_count
  FROM similar_recommenders sr
  JOIN people p ON p.id = sr.person_id
  ORDER BY sr.shared_count DESC;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.get_related_recommenders(p_recommender_ids uuid[])
 RETURNS TABLE(recommender_id uuid, related_recommenders jsonb)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
begin
  return query
    with shared_books as (
      -- Get all books recommended by each recommender
      select
        r1.person_id as recommender1,
        r2.person_id as recommender2,
        count(*) as shared_count,
        array_agg(b.title) as shared_books
      from recommendations r1
      join recommendations r2 on r1.book_id = r2.book_id and r1.person_id < r2.person_id
      join books b on b.id = r1.book_id
      where r1.person_id = any(p_recommender_ids)
      group by r1.person_id, r2.person_id
      having count(*) >= 2  -- Only include pairs with at least 2 shared books
    ),
    top_related as (
      -- Get top 3 related recommenders for each recommender
      select
        recommender1,
        recommender2,
        shared_count,
        shared_books,
        row_number() over (partition by recommender1 order by shared_count desc) as rn
      from shared_books
    )
    select
      tr.recommender1 as recommender_id,
      jsonb_agg(
        jsonb_build_object(
          'id', p.id,
          'full_name', p.full_name,
          'url', p.url,
          'type', p.type,
          'shared_books', tr.shared_books,
          '_shared_count', tr.shared_count
        )
      ) as related_recommenders
    from top_related tr
    join people p on p.id = tr.recommender2
    where tr.rn <= 3  -- Limit to top 3 related recommenders
    group by tr.recommender1;
end;
$function$
;

CREATE OR REPLACE FUNCTION public.get_books_with_counts(p_limit integer DEFAULT 1000, p_offset integer DEFAULT 0)
 RETURNS TABLE(id uuid, title text, author text, description text, genre text[], amazon_url text, similar_books jsonb, _recommendation_count integer, recommendation_percentile numeric)
 LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN QUERY
  WITH book_counts AS (
    SELECT
      books.id,
      books.title,
      books.author,
      books.description,
      books.genre,
      books.amazon_url,
      books.similar_books,
      books.recommendation_percentile,
      COUNT(DISTINCT r.person_id)::int as recommendation_count
    FROM books
    LEFT JOIN recommendations r ON books.id = r.book_id
    GROUP BY books.id, books.title, books.author, books.description, books.genre, books.amazon_url, books.similar_books, books.recommendation_percentile
  )
  SELECT
    bc.id,
    bc.title,
    bc.author,
    bc.description,
    bc.genre,
    bc.amazon_url,
    bc.similar_books,
    bc.recommendation_count,
    bc.recommendation_percentile
  FROM book_counts bc
  ORDER BY bc.recommendation_count DESC, bc.id -- Added stable secondary sort by ID
  LIMIT p_limit
  OFFSET p_offset;
END;
$function$
;

GRANT USAGE ON SCHEMA public, extensions TO anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO anon, authenticated;

INSERT INTO books (id,title,author,description,genre,recommendation_percentile,description_embedding) VALUES ('00000000-0000-0000-0000-000000000001', 'Fixture Book 1', 'Fixture Author', 'Synthetic storage book', ARRAY['Technology'], 0.9, ('[1,' || repeat('0,',1534) || '0]')::vector);

INSERT INTO people (id,full_name,type,description,similar_people,recommendation_percentile) VALUES ('10000000-0000-0000-0000-000000000001', 'Fixture Reader 1', 'Engineer', 'Synthetic reader', '[]', 0.8);

INSERT INTO recommendations (person_id, book_id,source) VALUES ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'Synthetic fixture');

INSERT INTO recommendations (person_id, book_id,source) VALUES ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'Synthetic fixture');

INSERT INTO books (id,title,author,description,genre,recommendation_percentile,description_embedding) VALUES ('00000000-0000-0000-0000-000000000002', 'Fixture Book 2', 'Fixture Author', 'Synthetic storage book', ARRAY['Technology'], 0.9, ('[1,' || repeat('0,',1534) || '0]')::vector);

INSERT INTO people (id,full_name,type,description,similar_people,recommendation_percentile) VALUES ('10000000-0000-0000-0000-000000000002', 'Fixture Reader 2', 'Engineer', 'Synthetic reader', '[]', 0.8);

INSERT INTO recommendations (person_id, book_id,source) VALUES ('10000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'Synthetic fixture');

INSERT INTO recommendations (person_id, book_id,source) VALUES ('10000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002', 'Synthetic fixture');
