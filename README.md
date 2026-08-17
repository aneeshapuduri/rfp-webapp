# RFP Proposal Agent — Web Application

Full end-to-end tool: upload a bid invitation, the agent extracts requirements, flags
ambiguities, generates clarification questions (or proceeds straight to a full proposal),
determines solution approach/staffing/pricing, and assembles a submission-ready proposal —
all tracked on a shared dashboard.

## What's included

- `main.py` — FastAPI app, all routes
- `db.py` — SQLite data layer (projects, documents, audit log)
- `storage.py` — file storage, with optional encryption at rest
- `pipeline_runner.py` — wires the Phase 1-4 pipeline into background jobs
- `pipeline/` — the actual Phase 1-4 logic (requirement extraction, SA engine, document
  assembly), self-contained
- `config/` — your company profile, rate card, ambiguity criteria, template spec, NFR spec
- `templates/`, `static/` — the UI

## Setup

```bash
pip install -r requirements.txt
```

### Required environment variables

```bash
export ANTHROPIC_API_KEY="your-key-here"     # required for real (non-demo) processing
export ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

**Generate `ENCRYPTION_KEY` once and store it securely (e.g. your infra team's secrets
manager) — losing it makes previously-stored documents unrecoverable.** Without it, the app
still runs but stores documents unencrypted at rest and prints a startup warning; do not use
that mode for real client bid data.

`DEMO_MODE` defaults to `true` and is a **sandbox-only convenience** — with no
`ANTHROPIC_API_KEY` set, it lets you test the app against the two sample RFPs in
`sample_data/` using canned responses instead of live model calls, so you can see the whole
flow work before spending API credits. Once `ANTHROPIC_API_KEY` is set, real processing is
always used regardless of `DEMO_MODE`.

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Before deploying with real client data — read this

1. **Network access**: there is no login (per your decision), which is not the same as no
   access control. Do not expose this on the open internet — put it behind your VPN, an IP
   allowlist, or otherwise restrict it to your internal network.
2. **HTTPS/TLS**: terminate TLS at your reverse proxy (nginx, your cloud load balancer, etc.)
   — the app itself serves plain HTTP, matching how most internal tools are fronted.
3. **Set `ENCRYPTION_KEY`** before any real bid data is uploaded — see above.
4. **Back up `data/app.db` and `data/generated/` / `data/uploads/`** per your normal backup
   policy — this is a single SQLite file plus a folder of documents, so standard file/DB
   backup tooling applies.
5. **Company profile**: `config/company_profile.json` still has placeholder company data.
   Replace it with your real company info before generating proposals you intend to submit.
6. **Rate card**: `config/role_rate_card.json` has research-grounded but generic market
   rates. Have your SA/leadership review and adjust before pricing goes out the door.

## Data model

- **Project**: name, agency, status (`Analyzing` → `Clarifications Sent` →
  `Responses Pending` → `Ready to Generate` → `Submitted`), timestamps, soft-delete flag.
- **Document**: every file tied to a project (bid invitation, clarification questions, client
  responses, final proposal) — always downloadable from the project detail page.
- **Audit log**: every upload, pipeline stage, download, and status change, with timestamp.
  View at `/audit`.

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

## Known limitations (intentional, scoped for later phases)

- **Client-provided templates**: `pipeline/template_mapper.py` does best-effort heading
  matching and flags anything it's not confident about for manual placement — it does not yet
  render content directly into an uploaded client template. See `config/template_spec.md`.
- **Background jobs**: uses FastAPI's built-in background tasks (single-process). Fine for a
  small internal team; if usage grows enough that multiple long-running pipeline jobs
  regularly overlap, consider moving to a real task queue (Celery/RQ) — the pipeline functions
  in `pipeline_runner.py` are already isolated enough to drop into one.
- **No user accounts**: per your decision. The audit log already carries a `user_identity`
  field (currently always `"shared-user"`) so adding real logins later doesn't require a
  schema change.
- **Accessibility**: built to WCAG 2.1 AA practices (semantic HTML, keyboard navigation,
  non-color-only status indicators, visible focus states) but not yet run through a formal
  automated scan or manual assistive-technology audit — recommended before wide rollout.
