# Conference Tracker

Deadline, event and submission tracking for the Institute of Data Science.
Self-hosted successor to the Claude artifact prototype.

- **Deadlines** — every milestone, filterable by conference, milestone kind, Future/Past and free text.
- **Conferences** — one card per series (ESWC), showing its next edition (ESWC 2027) with headline dates, scope and fit notes; other years are a click away.
- **Timeline** — Gantt view over a rolling window (three months back, eighteen ahead).
- **Map** — world map of event locations, clickable to filter.
- **Submissions** — a shared board where the group registers ideas and tracks them from Idea to Accepted. Registered ideas surface inline on the deadline rows they are relevant to. Members only: with sign-in enabled, the API never returns submissions to an anonymous caller. Conferences and milestones stay public, as they are in `/feeds/all.ics`.
- **Calendar** — a per-person subscribable `.ics` feed of starred items, plus one public feed of everything.
- **Admin** — in-app editing of series, editions and milestones for designated accounts, a log of every change with undo, and personal API tokens so an AI routine or a script can do the same edits.

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
Each series also reports a `state` for that edition: `open`, `closed` (deadlines
passed, next edition not added yet) or `awaiting` (no deadline published yet) —
the last two are what needs updating.

| Read (public) | |
| --- | --- |
| `GET /api/series?q=eswc` | series matching an acronym or name (exact acronym first), each with `current_edition_id`, `current_year`, `open` and `state` |
| `GET /api/series/{id or acronym}` | one series with every edition and its milestones |

| Write (admins) | |
| --- | --- |
| `POST /api/series`, `PUT`/`DELETE /api/series/{id}` | a series; deleting needs it to have no editions |
| `POST /api/series/{id}/editions` | a new edition (one per year) |
| `PUT`/`DELETE /api/conferences/{id}` | an edition; deleting removes its milestones |
| `POST /api/conferences/{id}/milestones`, `PUT`/`DELETE /api/milestones/{id}` | milestones |

Every write above may carry `?source=<url>` (where the new value was read, e.g.
the CFP page) and `?note=<why>`; both are kept in the change log.

### Change log and undo

Each create, update and delete of a series, edition or milestone is recorded in
`changes` (`app/editing.py`), with the values before and after, who made it,
whether through the app or an API token (and which), and the source and note.
Saving a form without changing anything records nothing.

A change can be undone while the record still looks exactly as that change left
it; if something has edited it since, undo refuses (409) rather than overwrite
the later edit — undo the newer change first. Undoing is itself a change, so
undoing the undo is a redo. Undoing an edition's deletion brings back its
milestones under their old ids and re-links the ideas that pointed at it.
Deleting a series leaves people's stars on it in place (they show nothing), so
undoing the delete gives them back. Undoing the creation of an edition is
refused once milestones or ideas have been added to it.

Homepages and edition URLs must be empty or start with `http://` or `https://`;
anything else (`javascript:`, a bare host name) is refused on the way in.

| Admins (app or token) | |
| --- | --- |
| `GET /api/changes?series=eswc&limit=50&before=<id>` | newest first; `series` takes an id or acronym, `entity_id` narrows to one record |
| `POST /api/changes/{id}/undo` | revert one change; returns the new change |

The Admin tab shows the log under *Recent changes*, with an Undo button on each
entry and a *History* button on each series.

### API tokens

A signed-in admin creates tokens in the Admin tab (*API tokens*). A token acts
as its owner with the owner's rights, so it can make every admin edit; its
writes go live immediately and land in the change log marked with the token's
name. Send it as `Authorization: Bearer ct_…`.

- Shown once at creation; only its SHA-256 hash is stored.
- Expires after 1–365 days (default 90). Any admin can see every token and
  revoke it; revocation takes effect on the next request.
- Any `Authorization` header that is not a valid, current token is a 401: never
  treated as anonymous, as the browser session, or as the dev user.
- Tokens cannot list, create or revoke tokens, rotate calendar URLs, or read
  the owner's calendar URL: a leaked token cannot mint another or leave a
  lasting way in. Those need a signed-in session.
- The token's power follows its owner. When a sign-in finds the owner is no
  longer in the admin group, all their tokens are revoked. Someone who leaves
  and never signs in again keeps working tokens until they expire, so revoke
  their tokens by hand when they leave.

| Signed-in admins only (not with a token) | |
| --- | --- |
| `GET /api/tokens` | every token, without its value |
| `POST /api/tokens` `{"name": "monthly AI routine", "expires_in_days": 90}` | a new token; the value is in this response only |
| `DELETE /api/tokens/{id}` | revoke |

```bash
curl -X PUT "$BASE_URL/api/conferences/$EDITION_ID?source=https://…/cfp&note=deadline+extended" \
  -H "Authorization: Bearer $TRACKER_TOKEN" -H 'Content-Type: application/json' -d @edition.json
```

### MCP server (for Claude)

`/mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server
(`app/mcp_server.py`, Streamable HTTP, stateless) so an AI can keep the tracker
current. It takes the same API tokens, and only them: no token, a bad one, or
one whose owner is no longer an admin is refused before the MCP layer runs,
even in dev mode. Its edits go through the same code as the app's, so they are
in the change log, under the token's name, and can be undone.

```bash
claude mcp add --transport http conference-tracker "$BASE_URL/mcp" \
  --header "Authorization: Bearer $TRACKER_TOKEN"
```

The Admin tab shows this command, filled in, when a token is created. In a
claude.ai custom connector, choose no sign-in and set the same header.

| Tool | |
| --- | --- |
| `tracker_search_series` | series by acronym/name, filter by `state` (`closed`/`awaiting` = needs updating) |
| `tracker_get_series` | one series: editions, key dates, milestones, ids |
| `tracker_list_changes` | the change log, newest first |
| `tracker_add_series`, `tracker_update_series` | a new venue; its lasting facts |
| `tracker_add_edition`, `tracker_update_edition` | a year of a series, its key dates (`clear` removes one) |
| `tracker_add_milestone`, `tracker_update_milestone` | timeline steps |
| `tracker_delete_milestone` | one milestone, with a `reason` |
| `tracker_undo_change` | revert a change; refused if the record changed since |

Rules on top of the REST API's: every add and update must cite `source_url`, an
http(s) page the values came from; updates are partial (only the fields given
change, so an AI never writes back stale copies of the rest); nothing deletes a
series or an edition. Editions are named by id or label (`"ESWC 2027"`).
Refusals (a clash, a stale undo, a bad link) come back as tool errors whose text
says what to do. The server's instructions explain the series/edition model,
AoE deadlines and the milestone names in use.

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
`0003` added `api_tokens` and `changes`.
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
