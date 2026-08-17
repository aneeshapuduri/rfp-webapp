# Non-Functional Requirements (NFR) Spec

Scope: this covers the web application (Phase 5) and the data it handles, since that's where
these properties are actually implemented. Design reference frameworks used: NIST 800-53
(moderate baseline) for access/audit/encryption controls, general data-minimization practice
for retention. This is a best-practice design reference, not a claim of formal certification —
formal certification would require an actual third-party audit, which is outside this build.

## 1. Data Security

- **In transit:** all traffic over HTTPS/TLS 1.2+, no exceptions, including internal
  service-to-service calls if the app is split across services.
- **At rest:** uploaded bid documents and all generated documents (clarification questions,
  proposals) encrypted at rest at the storage layer (e.g. server-side encryption on object
  storage, or full-disk/DB-level encryption if stored on a traditional filesystem/DB).
- **Secrets management:** API keys (e.g. ANTHROPIC_API_KEY) and any credentials loaded from
  environment variables or a secrets manager — never committed to source control, never
  logged in plaintext.
- **No public exposure by default:** since there's no login (per your call), the app should
  not be placed on the open internet without a network-level control (VPN, IP allowlist, or
  internal-network-only deployment) — flagged explicitly at deploy time, since "no login" and
  "no access control" are different things.

## 2. Audit Logging

- Every state-changing action logged with: timestamp, action type, project ID, and (if/when
  logins are added later) user identity. Logged actions: document uploaded, pipeline stage
  completed, clarification questions generated, client responses uploaded, proposal
  generated, document downloaded, status changed, project deleted.
- Logs are append-only from the application's perspective (no in-app edit/delete of log
  entries).
- Retained per the retention policy below; exportable as CSV for review.

## 3. Accessibility (WCAG 2.1 AA)

- Applies to the web app UI itself (dashboard, upload forms, project detail views) —
  semantic HTML, sufficient color contrast, full keyboard navigability, ARIA labels on
  interactive elements, and screen-reader-friendly status indicators (not color-only status
  badges).
- Generated proposal documents also carry WCAG-conscious formatting where feasible in
  .docx (heading structure, alt text on any images/logos) since some client RFPs (as seen in
  the sample) require this in the *deliverable* too, not just the tool.

## 4. Data Retention / Deletion

- Default retention: indefinite (bid history has ongoing reference value), but every project
  supports **manual deletion** by a user, which removes the source document, generated
  documents, and associated data.
- Deletion is a soft-delete with a recovery window (default 30 days) before permanent purge,
  to protect against accidental deletion of a live bid.
- Retention/purge window is configurable, not hardcoded, so you can tighten it later if a
  specific client contract requires shorter retention of their bid data.

## 5. Performance

- **Upload & parse:** a typical RFP document (under ~50 pages) should parse and begin
  requirement extraction within a few seconds of upload.
- **Pipeline stages that call Claude** (requirement extraction, ambiguity detection, SA
  reasoning, document generation) are inherently slower (tens of seconds each) — the UI must
  show live progress per stage rather than a blocking spinner, so non-technical users aren't
  left guessing whether it's stuck.
- **Dashboard load:** project list should load in well under a second even with hundreds of
  historical projects — achieved via pagination/lazy loading rather than loading full document
  content on the list view.
- **Async by design:** pipeline execution runs as a background job per project, not tied to
  the request/response cycle of the upload action, so the UI stays responsive and a user can
  navigate away and come back.

## Explicitly out of scope for now (flagging, not deciding silently)

- Formal penetration testing / third-party security audit — recommend before handling real
  client-sensitive bid data at scale, not something I can perform as part of this build.
- Multi-user auth/RBAC — deferred per your "no login for now" decision; the architecture will
  be built so login/roles can be added later without a rebuild (audit log already carries a
  user-identity field for this reason).
- Formal accessibility conformance testing (automated WCAG scan is included; a full manual
  audit with assistive-technology users is a separate service).
