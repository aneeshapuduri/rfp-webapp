# RFP Proposal Agent — Web Application

Full end-to-end tool: upload a bid invitation, the agent extracts requirements, flags
ambiguities, generates clarification questions (or proceeds straight to a full proposal),
runs a Go/No-Go capability-fit check against your stated service offerings, determines
solution approach/staffing/pricing, and assembles a submission-ready proposal — all tracked
on a shared dashboard, behind per-user login.

## What's included

- `main.py` — FastAPI app, all routes (now behind login — see Accounts & login below)
- `auth.py` — password hashing, sessions, the login-required middleware, and CSRF checks
- `db.py` — Postgres data layer (projects, documents, users, audit log) — targets Supabase's
  managed Postgres by default, but works against any Postgres instance via `DATABASE_URL`
- `storage.py` — file storage: safe on-disk filenames, size/type validation, and optional
  encryption at rest with a per-document flag (see Security fixes below)
- `pipeline_runner.py` — wires the Phase 1-4 pipeline (+ the Go/No-Go check) into background jobs
- `pipeline/` — the actual Phase 1-4 logic (requirement extraction, SA engine, document
  assembly) plus `go_no_go.py` (capability-fit scoring), self-contained
- `config/` — your company profile, rate card, ambiguity criteria, template spec, NFR spec
- `templates/`, `static/` — the UI
- `render.yaml` — Render Blueprint for one-click deploy (see Deploying to Render below);
  harmless to ignore if you're not using Render

## Setup

```bash
pip install -r requirements.txt
```

Dependencies are pinned to exact versions that were installed and tested together — see the
comment at the top of `requirements.txt` before bumping any of them.

### Database (Supabase)

Projects, documents (metadata only — see "Where data is stored" below for the actual files),
users, and the audit log live in Postgres. The app is written against plain Postgres, but is
meant to point at [Supabase](https://supabase.com)'s managed Postgres, since that gets you
automated backups and a web dashboard to browse/query data for free, without running your own
database server.

1. Create a Supabase project (the free tier is enough for this app).
2. In the Supabase dashboard: **Settings → Database → Connection string** — copy the URI. It
   looks like `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres`
   (Supabase fills in the password placeholder for you once you paste in the one you set when
   creating the project). Prefer the **connection pooler** string (port 6543, "Transaction"
   mode) over the direct connection if your host environment recycles connections frequently —
   either works, since this app opens a small connection pool of its own either way.
3. Set it as an environment variable:

```bash
export DATABASE_URL="postgresql://postgres.xxxx:your-password@aws-0-xxxx.pooler.supabase.com:5432/postgres"
```

`SUPABASE_DB_URL` is also accepted as an alias, if you'd rather name it that in your secrets
manager. The app will not start without one of these set — there's no SQLite fallback.

The schema (tables and columns) is created and kept up to date automatically on startup via
`db.init_db()` — there's no separate migration command to run, on first setup or after future
schema changes shipped in an update. You can also open the Supabase dashboard's **Table
Editor** at any time to browse `projects`, `documents`, `users`, and `audit_log` directly.

### Accounts & login

Every page now requires signing in (this used to be a fully open, no-login internal tool —
that was a critical finding in the security review and has been fixed). On first run, if no
users exist yet, the app creates an initial **admin** account automatically:

```bash
export ADMIN_USERNAME="admin"          # optional — defaults to "admin"
export ADMIN_PASSWORD="a-real-password" # optional — a random one is generated and printed once if you skip this
```

If you don't set `ADMIN_PASSWORD`, watch the startup log for a one-time `[FIRST RUN]` banner
with the generated password — it is never shown again and never stored in plaintext. Once
logged in as an admin, create accounts for the rest of the team from **Admin → Users**; every
account (admin or member) can see and work every project, matching the tool's original
shared-access design — the admin role only adds the ability to manage user accounts.

```bash
export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

Set this to a fixed value in production — without it, a random secret is generated at process
startup (with a warning) and every logged-in session is invalidated on restart.

### Required environment variables

Set ONE of these two AI provider keys (both work, pick whichever you have):

```bash
export ANTHROPIC_API_KEY="your-key-here"     # uses Claude
# OR
export GEMINI_API_KEY="your-key-here"        # uses Gemini 3.1 Flash Lite
```

If you have both set and want to force one, set `AI_PROVIDER=anthropic` or `AI_PROVIDER=gemini`
explicitly — otherwise Anthropic is preferred if both are present. Both providers implement
the exact same interface internally, so every part of the pipeline (requirement extraction,
clarification questions, solution architecture, document generation) works identically
regardless of which one you use — this was verified with mocked responses confirming the
Gemini client sends the right model name, prompt, and JSON-mode config; live end-to-end
testing against the real Gemini API should still happen on your end, since this sandbox's
network can't reach Google's API to test it live.

```bash
export ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

**Generate `ENCRYPTION_KEY` once and store it securely (e.g. your infra team's secrets
manager) — losing it makes previously-stored documents unrecoverable.** Without it, the app
still runs but stores documents unencrypted at rest and prints a startup warning; do not use
that mode for real client bid data.

`DEMO_MODE` now **defaults to `false`** (this changed — it used to default to `true`, which the
security review flagged: a production deployment that forgot to set an API key would silently
serve fabricated demo output through the same UI as real analysis, with no visible difference).
It is now fail-closed: with no API key and `DEMO_MODE` not explicitly set to `true`, every
upload fails loudly with a clear error instead of guessing. To exercise the app end-to-end
against the sample RFPs in `sample_data/` without an API key:

```bash
export DEMO_MODE=true
```

A persistent banner is shown on every page while `DEMO_MODE` is active, so it's never
ambiguous whether what's on screen is a real analysis. Once `ANTHROPIC_API_KEY` or
`GEMINI_API_KEY` is set, real processing is always used regardless of `DEMO_MODE`.

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Where data is stored (`DATA_DIR`)

Projects, users, and the audit log live in Supabase (see "Database (Supabase)" above) — that
part isn't affected by `DATA_DIR` or by redeploys/restarts wiping the app's own filesystem,
since it's a separate managed service.

Uploaded bid invitations and generated proposals (the actual file bytes, not their database
records) are still written to local disk, under a `data/` folder next to the app by default. On
a VPS or your own server, that folder sits on the same disk as the app and just persists
normally.

On a platform where the app's own filesystem is wiped on every deploy or restart (Render,
Heroku, most container platforms), set `DATA_DIR` to a path on a **persistent/mounted disk**
instead:

```bash
export DATA_DIR=/var/data   # or wherever your platform mounts a persistent volume
```

If you skip this on such a platform, the app will still run — right up until the next deploy or
restart wipes every uploaded/generated document with no warning (your accounts, projects, and
audit history are safe either way, since those live in Supabase now). See "Deploying to Render"
below for the concrete version of this.

## Deploying to Render

A `render.yaml` blueprint is included. In the Render dashboard: **New +** → **Blueprint** →
point it at the git repo containing this app. Render reads `render.yaml` and creates the
service and a 1 GB persistent disk mounted at `/var/data` (for uploaded/generated documents —
see "Where data is stored" above), and prompts you for the secret environment variables it
marked `sync: false` (`DATABASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ENCRYPTION_KEY`,
`ANTHROPIC_API_KEY`/`GEMINI_API_KEY`). Set `DATABASE_URL` to your Supabase connection string
(see "Database (Supabase)" above) — don't skip this one, the app won't start without it.
`SESSION_SECRET` is auto-generated for you; `DATA_DIR` is already set to the mounted disk path;
`DEMO_MODE` is set to `false`.

If you'd rather click through it by hand instead of using the blueprint, create a **Web
Service** with:

- **Runtime**: Python 3
- **Build command**: `pip install -r requirements.txt`
- **Start command**: `uvicorn main:app --host 0.0.0.0 --port $PORT` (Render assigns `$PORT` —
  don't hardcode 8000)
- **Instance type**: anything above the free plan — a persistent disk requires a paid plan
- **Disk**: add one, e.g. 1 GB, mounted at `/var/data` (for documents — not needed for the
  database itself, since that's in Supabase)
- **Environment variables**: `DATABASE_URL` (your Supabase connection string), `DATA_DIR=/var/data`,
  `SESSION_SECRET` (a long random string), `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ENCRYPTION_KEY`,
  and `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`. Leave `DEMO_MODE` unset.

**The one thing that will bite you if skipped**: Render's default filesystem is ephemeral — it
resets on every deploy. Without the persistent disk (and `DATA_DIR` pointed at it), the app
still deploys and looks like it works, but every uploaded/generated document disappears the
next time you push a change (accounts and projects are safe regardless, since those live in
Supabase). If you're re-deploying an existing Render service that was already running this app
*without* a disk, your existing documents on that service were already living on borrowed
time — add the disk before your next deploy, not after.

**Redeploying after a code update** (like this round of fixes): commit the new code, push to
the branch Render is watching, and it redeploys automatically (or click **Manual Deploy** in
the dashboard). The persistent disk is untouched by a redeploy — your uploaded/generated
documents carry over, and your Supabase database is entirely unaffected by app redeploys since
it's a separate service. Database schema changes in this update (new `users` table, a couple of
new columns on `projects`/`documents`) are applied automatically via `db.init_db()`'s
migrations on startup — no manual migration step needed.

## Before deploying with real client data — read this

1. **Set `SESSION_SECRET` and create real accounts** (see Accounts & login above) — don't run
   with the auto-generated admin password past initial setup; rotate it once you've logged in.
2. **Network access**: login is now required, but that's still not a substitute for keeping
   this off the open internet — put it behind your VPN, an IP allowlist, or otherwise restrict
   it to your internal network.
3. **HTTPS/TLS**: terminate TLS at your reverse proxy (nginx, your cloud load balancer, etc.)
   and set `SESSION_COOKIE_SECURE=true` once you do, so the session cookie is only ever sent
   over HTTPS — the app itself still serves plain HTTP internally, matching how most internal
   tools and platform-fronted apps (Render included) work. On Render specifically, the public
   URL is always HTTPS, so set `SESSION_COOKIE_SECURE=true` from the start (already set in
   `render.yaml`).
4. **Set `ENCRYPTION_KEY`** before any real bid data is uploaded — see above. Turning it on
   later is safe: existing documents are tracked per-document as encrypted or not, so old
   plaintext files keep reading correctly instead of crashing.
5. **Confirm `DEMO_MODE` is unset (or `false`)** — see above.
6. **Database backups**: Supabase takes automated daily backups on paid plans (check your
   project's plan and backup retention under Settings → Database → Backups) — confirm this
   matches your organization's backup policy before relying on it for real client data. Also
   back up `data/generated/` / `data/uploads/` (the actual document files, which live outside
   Supabase — see "Where data is stored" above) per your normal file backup policy.
7. **Company profile**: `config/company_profile.json` still has placeholder company data.
   Replace it with your real company info before generating proposals you intend to submit —
   this also feeds the Go/No-Go capability check, so keep `core_capabilities` accurate.
8. **Rate card**: `config/role_rate_card.json` has research-grounded but generic market
   rates. Have your SA/leadership review and adjust before pricing goes out the door.

## Data model

- **Project**: name, agency, status (`Analyzing` → `Clarifications Sent` →
  `Responses Pending` → `Ready to Generate` → `Submitted`), timestamps, soft-delete flag.
- **Document**: every file tied to a project (bid invitation, clarification questions, client
  responses, final proposal) — always downloadable from the project detail page.
- **Audit log**: every upload, pipeline stage, download, status change, login, and user-account
  change, with timestamp and the real username that did it. View at `/audit`.
- **User**: username, hashed password (never plaintext), role (`admin`/`member`), active flag.

### Status lifecycle, as implemented

- `Analyzing` — Phase 1 (requirement extraction) is running.
- `Clarifications Sent` — ambiguities were found; the clarification questions document has
  been generated and is ready to download and send to the client. Also the state the project
  returns to if a client's response doesn't actually resolve the ambiguity (see below).
- `Responses Pending` — set the moment you upload the client's filled-in responses, while
  they're being processed in the background.
- `Ready to Generate` — either no ambiguities were found, or all client responses resolved
  them; Phases 3-4 have run automatically and the final proposal is generated and downloadable.
  *(Note on this status name: since this build auto-generates the proposal the moment
  requirements are resolved rather than treating "generate" as a separate manual step, this
  status functions as "ready for your review before submission" — flag if you'd rather split
  proposal generation into an explicit manual action instead of full automation.)*
- `Submitted` — set manually via the status dropdown once you've actually sent it to the
  client.

You can override the status manually at any time via the dropdown on the project detail page.

### Handling a client response that doesn't actually answer the question

If a client's answer is too vague to design/price against (e.g. "use your best judgement"),
the affected requirement is marked `escalated_for_manual_review` and stays `ambiguous` rather
than being auto-accepted or looping another AI-generated question back to the client. The
project stays in `Clarifications Sent` and the requirements table on the project page shows
which item(s) need a human to follow up on directly. This was tested end-to-end — see
`pipeline/test_phase2.py`.

## Go / No-Go capability check

Right after Phase 1 extraction, every project is scored against `config/company_profile.json`'s
`core_capabilities` list — a deterministic, keyword-based match (see `pipeline/go_no_go.py`),
not another AI call, so it's explainable, free, and available even in `DEMO_MODE`. It renders
as a real "Go / No-Go Decision" panel on the project page: an overall call (`Go` / `Go, with
gaps` / `No-Go`), which stated capabilities matched, and which specific requirements didn't
match anything — meant as a fast first read for the bid team, not a replacement for a human
bid/no-bid call. (The original mockup showed a panel like this with no backend behind it at
all; this is that panel, for real.)

## Known limitations (intentional, scoped for later phases)

- **Client-provided templates**: `pipeline/template_mapper.py` does best-effort heading
  matching and flags anything it's not confident about for manual placement — it does not yet
  render content directly into an uploaded client template. See `config/template_spec.md`.
- **Background jobs**: uses FastAPI's built-in background tasks (single-process). Fine for a
  small internal team; if usage grows enough that multiple long-running pipeline jobs
  regularly overlap, consider moving to a real task queue (Celery/RQ) — the pipeline functions
  in `pipeline_runner.py` are already isolated enough to drop into one.
- **Flat permissions**: every account (admin or member) can see and act on every project —
  there's no per-project ownership or restricted visibility yet, matching the tool's original
  shared-access design. The admin role currently only gates user management.
- **Accessibility**: built to WCAG 2.1 AA practices (semantic HTML, keyboard navigation,
  non-color-only status indicators, visible focus states) but not yet run through a formal
  automated scan or manual assistive-technology audit — recommended before wide rollout.
