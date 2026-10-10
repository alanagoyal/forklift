# Existing PostgreSQL applications and Supabase

Forklift works on local PostgreSQL development databases. A project that uses hosted
PostgreSQL can bring a compatible logical schema/data export into a local Forklift
branch and point its development application there. It does not rewind the hosted
database, manage other database engines, or automatically copy a cloud service.

Supabase applications need an extra distinction: `supabase-js` sends HTTP requests to
Supabase's API, rather than connecting to PostgreSQL itself. Changing `DATABASE_URL`
therefore does not redirect a Supabase client. The local application also needs a
local HTTP API and local `NEXT_PUBLIC_SUPABASE_URL` / API-key values. Keep hosted
service credentials out of the local experiment.

## Booklist validation

`scripts/test_booklist.py` runs Booklist's existing `scripts/dump-data.ts` and
`utils/supabase/server.ts` unchanged in an isolated copy. It uses real supabase-js
and PostgREST, with a small loopback HTTP adapter removing the `/rest/v1` URL prefix.
Requests carry a local signed `anon` JWT; PostgREST logs in as a separate
non-superuser `authenticator` role and switches to `anon` for API requests.

The committed `examples/booklist/schema.sql` fixture contains the three application
tables, the RPCs used by the data dump and semantic search, and the observed RLS
policy expressions. These were inspected read-only through the Supabase plugin.
The fixture uses two synthetic books, two synthetic people, and four synthetic
recommendations. It is a deliberately scoped reproduction, not a full schema export:
production data, platform schemas, other functions, indexes and triggers are not
copied. Existing `SECURITY DEFINER` RPCs are preserved to reproduce the application;
those functions execute with their owner's privileges. Direct table DELETE requests
separately test the observed RLS prohibition. This is not a hosted security audit.

The hosted project reported PostgreSQL 15.8. This validation logically recreates the
application subset in PostgreSQL 17; it does not import PG15 physical database files.
Its required extensions are `vector` 0.8.0, `pg_trgm` 1.6, and `uuid-ossp` 1.1.
Install matching extensions in the chosen PG17 installation before capturing a
checkpoint. Forklift records and checks the local binaries; the remote server's
extension installation is not transferred automatically.

The test verifies:

- Booklist's build-time data dump returns books, recommenders and related-book data.
- The actual semantic-search RPC handles a 1536-dimensional vector through HTTP.
- Anonymous DELETE cannot remove a person despite having table-level DELETE grants.
- After a paired capture, editing source, adding a column and changing a title changes
  the application's output. Restoring the capture restores source, schema and output.
- RLS and vector search still work after the restore.
- The unchanged Next.js development app returns HTTP 200 and serves the regenerated
  JSON data at its configured `/booklist` base path. This is an HTTP smoke test, not
  a browser interaction or production build test.

This does **not** validate a complete Next.js production build, authenticated user
sessions, Supabase Auth, Storage, Realtime, Edge Functions, production data volume,
OpenAI embedding generation, or email delivery. Booklist's other hosted extensions
are outside this application subset. A project relying on those services needs a
corresponding local service setup or a deliberately narrower development workflow.

## Reproduce

On macOS ARM64/APFS, provide a PostgreSQL 17 installation with the three extensions,
a local Booklist checkout with its dependencies already installed, and the official
PostgREST 16.4 executable. The test only copies an explicit application-file
allowlist; it never reads `.env` files and does not run npm install or lifecycle
scripts. It reads existing dependencies via a symlink outside captured source paths.

```sh
cargo build
BOOKLIST_ROOT=/path/to/booklist \
POSTGREST_BIN=/path/to/postgrest \
PG_BIN=/opt/homebrew/opt/postgresql@17/bin \
DYLD_LIBRARY_PATH=/opt/homebrew/opt/postgresql@17/lib/postgresql \
python3 scripts/test_booklist.py
```

`DYLD_LIBRARY_PATH` allows the official macOS PostgREST binary to use the selected
installation's `libpq` when Homebrew's standalone `libpq` package is absent. Test
artifacts stay under ignored `benchmark-data/booklist-*`. The test closes its HTTP
servers and stops the project branch; the disposable store remains available
for inspection, with a `summary.json` receipt and `SUCCESS` marker after successful checks
and shutdown. Neither Booklist's original files nor its hosted database change.

A normal app integration should similarly stop the API server and application
before capture or restore, then restart them with the newly injected connection.
This refreshes connection pools and PostgREST's schema cache. Forklift does not yet
coordinate those processes automatically. The `.forklift.json` settings are:

```json
{
  "format": 1,
  "source": ["scripts/dump-data.ts", "utils/supabase/server.ts", "tsconfig.json"],
  "connection_env": "DATABASE_URL",
  "database": "booklist",
  "tcp": true,
  "commands": {}
}
```

Create the named database in the seed before configuring the project. Use
`forklift project doctor` to inspect configuration and local connectivity. TCP binds
to loopback and uses a generated password; connection URLs contain credentials and
should not be committed. An API gateway should use its own least-privilege login,
as this fixture does, rather than run HTTP requests as the database administrator.

See [project setup](project.md) for the seed, configure, setup and run commands.
For the underlying API behavior, see [Supabase's REST API overview](https://supabase.com/docs/guides/api)
and [PostgREST configuration](https://docs.postgrest.org/en/stable/references/configuration.html).
