# Native Supabase projects

Forklift can manage an optional, private **local Supabase** stack for a Git
worktree. The ordinary PostgreSQL backend remains available and requires no
Supabase CLI, credentials, or service downloads.

This first Supabase backend supports macOS ARM64/APFS, **Supabase CLI 2.120.0**,
and its native PostgreSQL 17 runtime. It runs PostgreSQL, the REST API, Auth,
Storage, and Realtime. Docker/Podman stacks and existing Supabase-managed stacks
are not adopted. Forklift creates a separate owned stack for each worktree.

## Setup

Install Forklift, the pinned [Supabase CLI release](https://github.com/supabase/cli/releases/tag/v2.120.0),
and the PostgreSQL 17 utilities required by the normal Forklift installation.
The Supabase native distribution supplies the database server and extensions;
Forklift also uses `pg_controldata` and `pg_checksums` from your PostgreSQL 17
installation to verify saved database files. The first start downloads Supabase's
native service artifacts. Subsequent starts use the private runtime cache.

In a Git project with `supabase/config.toml`:

```sh
forklift project setup
forklift project doctor
forklift project run -- npm run dev
```

Set `FORKLIFT_SUPABASE_BIN=/absolute/path/to/supabase` if the pinned CLI is not on
PATH. Its location is saved in the binding. Use `--pg-bin PATH` before `project`
if your PostgreSQL 17 utilities are outside the default Homebrew installation.

Setup detects the Supabase configuration and creates `.forklift.json` if it is
missing. It selects conventional application paths plus the existing
`supabase/config.toml`, migrations, seed.sql, and functions directories. Review
that source list and commit the configuration. The normal source limits apply;
large assets, links and nested exclusions need an explicitly narrower selection.
Dotenv files, dependencies, and the original `supabase/.temp` are not copied.

The input config must specify PostgreSQL 17. A project declaring PostgreSQL 15
must first prepare and validate a PostgreSQL 17 local configuration; Forklift
does not silently upgrade it. Hosted-only projects need local configuration and
migrations first. Setup applies local migrations/seeds to the newly created stack;
it does not pull production data, credentials, or remote schema changes.

Forklift copies the local service configuration into private state, removes fixed
port assignments so worktrees receive separate endpoints, and forces durability
settings on. It enables database checksums offline before allowing captures.
The original `supabase/config.toml` is unchanged.

## Backend and environment selection

Use `"backend": "postgres"` or `"backend": "supabase"` in `.forklift.json` to
override detection. Existing bound PostgreSQL worktrees keep their backend.
Changing backends requires a fresh worktree; a saved binding cannot be reused for
a different backend or copied to another location.

An explicit Supabase configuration can look like:

```json
{
  "format": 1,
  "backend": "supabase",
  "source": ["src", "package.json", "supabase/config.toml", "supabase/migrations", "supabase/seed.sql"],
  "connection_env": "DATABASE_URL",
  "supabase_env": {
    "API_URL": "VITE_SUPABASE_URL",
    "PUBLISHABLE_KEY": "VITE_SUPABASE_PUBLISHABLE_KEY"
  },
  "commands": { "start": ["npm", "run", "dev"] }
}
```

By default, managed commands receive `DATABASE_URL`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `SUPABASE_PUBLISHABLE_KEY`, and the corresponding
`NEXT_PUBLIC_SUPABASE_*` URL/client-key variables. Server-only keys are passed as
`SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_SECRET_KEY`. Custom `supabase_env` maps
Supabase output names (`API_URL`, `DB_URL`, `ANON_KEY`, `PUBLISHABLE_KEY`,
`SERVICE_ROLE_KEY`, `SECRET_KEY`) to your application's names. Secret keys and
database credentials cannot be mapped to recognized public-client variable
prefixes. Never log the child
process environment or expose server keys to a frontend.

`project run` and `project task` start the local services and inject these values.
They hold the workspace gate while the app runs. Stop the managed app command
before capturing or restoring; Forklift refuses to interrupt a command holding
that gate. Alternatively, configure the [opt-in managed lifecycle](project.md#opt-in-managed-app-lifecycle)
before setup and use `project app-start`. Capture/restore then drain the owned app
group before stopping native services, and restart it with fresh local URLs/keys
only after success. A stopped app stays stopped. `project app-stop` drains just
the app; `project stop` drains it before stopping the native stack.
Pause unmanaged writers and agents too. Raw CLI errors are kept in a
private runtime log because they may contain local credentials.

## Save and rewind

```sh
forklift project capture baseline
# Change source, run migrations, change rows or upload local files.
forklift project restore baseline
forklift project run -- npm run dev
forklift project stop
```

Capture cleanly stops all managed services, verifies the stopped database and
page checksums, and saves matching selected source, database state, uploaded
Storage bytes, and normalized runtime configuration. Auth records, database
roles, RLS policies, functions, and migration history are part of that database.
Services restart after successful capture or restore. Realtime connections must
reconnect after restart; browser memory and cookies are not restored.

Snapshots use stopped filesystem copies/APFS clones. They do not yet use the
ordinary PostgreSQL backend's packed objects, deduplication, or exports. File
hashes and an exact runtime fingerprint are checked before restore. A runtime or
CLI upgrade can make existing captures incompatible; automatic upgrades and
cross-runtime snapshot migration are unsupported.

An interrupted restore leaves a journal and blocks normal managed commands:

```sh
forklift project recover
```

Recovery completes the selected restore, including source and Storage files,
before restarting native services. If managed app hooks are configured, recovery
first confirms app quiescence and leaves the app **stopped** after recovery; use
`project app-start` explicitly. Hook failures/timeouts keep a separate lifecycle
journal pending, even if the backend restore succeeded. See the shared
[failure contract](project.md#failures-and-recovery). Previous dirty source and local state are retained
until explicit pruning. This is tested process-interruption recovery, not a
claim of arbitrary host power-loss recovery.

`project forget NAME` previews removing a capture; `project prune` previews
reclaiming forgotten snapshots, pre-restore copies, and incomplete staging data.
Add `--yes` to apply. Current runtime data, artifact caches, and named captures
remain protected. Stop your stack when finished to free resources.

## Scope and verification

Studio, Edge Functions execution, mail, analytics, and connection pooling are
excluded from this initial backend. Function source can be saved but is not
executed. OAuth providers, external SMTP, third-party APIs, and already-delivered
external effects are not rewound. This is a local native stack integration, not a
hosted Supabase backup or security audit.

`scripts/test_supabase.py` exercises actual native Auth, private Storage, database
and source restore, interrupted operations, retention, and independent Git
worktrees. With `BOOKLIST_ROOT` it also applies Booklist's complete checked-in
migration, runs its unchanged data-loading script using synthetic rows, and
checks real Realtime broadcasts before and after restore.
Booklist's original configuration declares PostgreSQL 15; the native test uses
an explicit PostgreSQL 17 configuration in a separate local copy. It does not
change Booklist's repository or access its hosted database.
See [contributing](../CONTRIBUTING.md) for the test command. The earlier
[Booklist/PostgREST prototype](supabase.md) is retained as separate evidence.
