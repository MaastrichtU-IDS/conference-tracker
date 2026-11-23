# Conference Tracker

Deadline, event and submission tracking for the Institute of Data Science.
Self-hosted successor to the Claude artifact prototype.

- **Deadlines** — every milestone, filterable by conference, milestone kind, Future/Past and free text.
- **Conferences** — one card per series (ESWC), showing its next edition (ESWC 2027) with headline dates, scope and fit notes; other years are a click away.
- **Timeline** — Gantt view over a rolling window (three months back, eighteen ahead).
- **Map** — world map of event locations, clickable to filter.
- **Submissions** — a shared board where the group registers ideas and tracks them from Idea to Accepted. Registered ideas surface inline on the deadline rows they are relevant to. Members only: with sign-in enabled, the API never returns submissions to an anonymous caller. Conferences and milestones stay public, as they are in `/feeds/all.ics`.
- **Calendar** — a per-person subscribable `.ics` feed of starred items, plus one public feed of everything.
- **Admin** — in-app editing of series, editions and milestones for designated accounts.

Ships seeded with 29 series (one 2027 edition each) and 174 milestones.

## Running locally

```bash
docker compose up --build
# http://localhost:8000
```

With no `OIDC_*` variables set the app runs in **single-user dev mode**: every
request is the same synthetic admin user. This is intended for development only
— never deploy without OIDC configured.

Without Docker:

```bash
pip install -r requirements-dev.txt
export DATABASE_URL="sqlite:///./dev.db"
uvicorn app.main:app --reload
pytest -q
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://tracker:tracker@localhost:5432/tracker` | SQLAlchemy URL. SQLite works for development. |
| `BASE_URL` | `http://localhost:8000` | Public origin. Used for OIDC redirects and the feed URLs shown in the UI. |
| `SECRET_KEY` | *(insecure default)* | Session cookie signing key. Generate with `openssl rand -hex 32`. |
| `OIDC_ISSUER` | — | Issuer URL; `/.well-known/openid-configuration` is discovered from it. |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | — | Client credentials. All three must be set to enable sign-in. |
| `OIDC_SCOPES` | `openid email profile` | Requested scopes. |
| `ALLOWED_EMAIL_DOMAINS` | *(empty = any)* | Comma-separated domains permitted to sign in. |
| `REQUIRE_VERIFIED_EMAIL` | `true` | Refuse sign-in unless the provider sends `email_verified: true`. Without it the domain check trusts whatever email the account carries. |
| `ADMIN_GROUPS` | *(empty)* | Comma-separated groups (from the token's `groups` claim) whose members may edit conferences and milestones. When set, `ADMIN_EMAILS` is ignored. |
| `ADMIN_EMAILS` | *(empty)* | Comma-separated accounts that may edit conferences and milestones, used only when `ADMIN_GROUPS` is empty. |
| `INIT_DB_ON_START` | `true` | Migrate the schema at boot. Set `false` where a migration job owns it. |
| `SEED_ON_START` | `true` | Load `seed/conferences.json` when no series exists yet. |
| `OTEL_SDK_DISABLED` | `true` | Tracing on/off. The image always runs under `opentelemetry-instrument`; flip this to `false` to export. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Collector endpoint when tracing is on. |

Register `BASE_URL/auth/callback` as the redirect URI with your identity provider.

For `ADMIN_GROUPS`, the provider must put a `groups` claim in the ID token. On
Keycloak: attach a client scope with a *Group Membership* mapper (claim `groups`,
add to ID token) to the client as a **Default** scope.

## Data model

- **Series** — a recurring conference (ESWC): acronym, name, homepage, and what
  stays true across years (emphasis, the group's fit notes).
- **Edition** — one year of a series (ESWC 2027): location, that year's site,
  status, headline dates and its **milestones**. (The table is still called
  `conferences`, and the API's `/api/conferences/{id}` is an edition.)
- **Submissions** point at an edition; **stars** are on a series (following
  every edition) or on a single milestone.

Series, editions and milestones are reference data, editable in the Admin tab
by members of `ADMIN_GROUPS` (or, without it, the accounts in `ADMIN_EMAILS`);
admin status is re-checked at every sign-in. Submissions are shared and
writable by any signed-in user. Stars are private per user and are what the
personal calendar feed is built from.

**Which edition is "next"** (`app/editions.py`): the earliest edition with a
submission deadline (abstract, paper or proposal) still ahead, in AoE; when none
is open, the latest edition, flagged as not open. So searching for ESWC lands on
ESWC 2027 until its last deadline passes, then on ESWC 2028 once that exists.

| Read (public) | |
| --- | --- |
| `GET /api/series?q=eswc` | series matching an acronym or name (exact acronym first), each with `current_edition_id`, `current_year` and `open` |
| `GET /api/series/{id or acronym}` | one series with every edition and its milestones |

| Write (admins) | |
| --- | --- |
| `POST /api/series`, `PUT`/`DELETE /api/series/{id}` | a series; deleting needs it to have no editions |
| `POST /api/series/{id}/editions` | a new edition (one per year) |
| `PUT`/`DELETE /api/conferences/{id}` | an edition; deleting removes its milestones |
| `POST /api/conferences/{id}/milestones`, `PUT`/`DELETE /api/milestones/{id}` | milestones |

The seed loader only runs when no series exists, so it is safe on every boot
and will not overwrite edits made in the app. Seed rows are editions: `venue`
names the series and an optional `year` the edition (else the event's year).

## Schema, migrations and seeding

The schema is managed by Alembic (`app/migrations/versions/`). `app/initdb.py`
migrates to the latest revision and loads the seed, then exits — run it as a
pre-deploy job (`python -m app.initdb`) and set `INIT_DB_ON_START=false` on the
app so replicas never race. Both steps are idempotent, so the job is safe to
re-run on every sync. Left at the defaults, the app does this itself at startup,
which is what `docker compose up` relies on.

A database from before migrations existed (v0.1.x) is recognised and stamped at
the baseline `0001` before upgrading. `0002` split series from editions: every
existing conference became the 2027 edition of a new series, keeping its id.
Migrations have no downgrade; restore a backup instead. `tests/test_migrations.py`
runs them on SQLite, and on PostgreSQL when `PG_TEST_URL` is set.

## Tracing

The image runs under `opentelemetry-instrument` with `OTEL_SDK_DISABLED=true`
baked in, so there is no overhead by default and no rebuild is needed to turn
tracing on — set `OTEL_SDK_DISABLED=false` and point
`OTEL_EXPORTER_OTLP_ENDPOINT` at a collector.

## Calendar feeds

- `GET /feeds/{token}.ics` — one person's starred series (the lifecycle
  deadlines of every edition) and individually starred milestones.
- `GET /feeds/all.ics` — every edition and every dated milestone.

Both are unauthenticated by design: Outlook and Google fetch them with no
credentials. The per-user token is 32 random bytes, is the only thing that
identifies the calendar, and can be rotated from the Calendar tab — rotating
invalidates the old URL immediately.

Entries are all-day and `TRANSP:TRANSPARENT`, so they never affect
availability. UIDs are derived from database ids, so refreshes update entries
in place rather than duplicating them.

## Deployment

Manifests live in `deploy/`. They assume an nginx ingress controller and
cert-manager.

```bash
kubectl apply -f deploy/namespace.yaml

kubectl -n conference-tracker create secret generic conference-tracker \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=OIDC_CLIENT_SECRET="…"

# Review the host, issuer and admin list first.
$EDITOR deploy/configmap.yaml deploy/ingress.yaml

kubectl apply -k deploy/
```

Use a hex password for Postgres: `DATABASE_URL` is assembled by string
interpolation in the Deployment, so characters such as `@`, `:` and `/` would
corrupt the URL.

Images are built and pushed to `ghcr.io/maastrichtu-ids/conference-tracker` by
the CI workflow on every push to `main` and on version tags.

Postgres here is a single-replica StatefulSet on a 5 Gi `ReadWriteOnce` volume
— adequate for this workload, but it is not highly available and has no backup
job. If the cluster offers a managed Postgres, point `DATABASE_URL` at it and
drop `deploy/postgres.yaml`.

## API

OpenAPI docs are served at `/api/docs`. The frontend uses a single
`/api/bootstrap` call for its initial state and narrower endpoints for
mutations.

## Provenance

Dates carry the status and notes from the original research — where a date is
community-reported, extended, or contradicted by its source, the milestone says
so. Those caveats are worth preserving when editing.
