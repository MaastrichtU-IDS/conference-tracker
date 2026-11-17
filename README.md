# Conference Tracker

Deadline, event and submission tracking for the Institute of Data Science.
Self-hosted successor to the Claude artifact prototype.

- **Deadlines** — every milestone, filterable by conference, milestone kind, Future/Past and free text.
- **Conferences** — one card per venue with headline dates, scope and fit notes.
- **Timeline** — Gantt view across Jul 2026 – Dec 2027.
- **Map** — world map of event locations, clickable to filter.
- **Submissions** — a shared board where the group registers ideas and tracks them from Idea to Accepted. Registered ideas surface inline on the deadline rows they are relevant to. Members only: with sign-in enabled, the API never returns submissions to an anonymous caller. Conferences and milestones stay public, as they are in `/feeds/all.ics`.
- **Calendar** — a per-person subscribable `.ics` feed of starred items, plus one public feed of everything.
- **Admin** — in-app editing of conferences and milestones for designated accounts.

Ships seeded with 29 conferences and 174 milestones.

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
| `ADMIN_EMAILS` | *(empty)* | Comma-separated accounts that may edit conferences and milestones. |
| `INIT_DB_ON_START` | `true` | Create the schema at boot. Set `false` where a migration job owns it. |
| `SEED_ON_START` | `true` | Load `seed/conferences.json` when the database is empty. |
| `OTEL_SDK_DISABLED` | `true` | Tracing on/off. The image always runs under `opentelemetry-instrument`; flip this to `false` to export. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Collector endpoint when tracing is on. |

Register `BASE_URL/auth/callback` as the redirect URI with your identity provider.

## Data model

Conferences and milestones are reference data, editable in the Admin tab by
accounts listed in `ADMIN_EMAILS`. Submissions are shared and writable by any
signed-in user. Stars are private per user and are what the personal calendar
feed is built from.

The seed loader only runs when the `conferences` table is empty, so it is safe
on every boot and will not overwrite edits made in the app.

## Schema and seeding

`app/initdb.py` creates the schema and loads the seed, then exits — run it as a
pre-deploy job (`python -m app.initdb`) and set `INIT_DB_ON_START=false` on the
app so replicas never race to create tables. Both steps are idempotent, so the
job is safe to re-run on every sync. Left at the defaults, the app does this
itself at startup, which is what `docker compose up` relies on.

## Tracing

The image runs under `opentelemetry-instrument` with `OTEL_SDK_DISABLED=true`
baked in, so there is no overhead by default and no rebuild is needed to turn
tracing on — set `OTEL_SDK_DISABLED=false` and point
`OTEL_EXPORTER_OTLP_ENDPOINT` at a collector.

## Calendar feeds

- `GET /feeds/{token}.ics` — one person's starred conferences (all their
  lifecycle deadlines) and individually starred milestones.
- `GET /feeds/all.ics` — every conference and every dated milestone.

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
