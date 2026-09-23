# Defensive security assessment

Baseline: `929f85b` (reviewed 2026-09-23). Scope: tracked source, tests,
configuration, dependency declarations and local execution. No external attack
testing. Initial assessment was recorded before hardening changes.

## Architecture observed before changes

- Frontend and backend: Streamlit, native widgets, Altair; no custom JavaScript.
- Authentication/authorization: absent; arbitrary text identified an approver.
- Storage: CSV/XLSX, joblib output and per-browser temporary directories;
  no database, ORM, REST API, password store or JWT implementation.
- Uploads: six CSV/XLSX inputs, suffix filtering in the widget, 100 MB each.
- Computation: pandas preprocessing and synchronous RandomForest training,
  unrestricted calendar expansion and `n_jobs=-1`.
- Configuration: Streamlit TOML; no Docker, reverse proxy or deployment manifest.
- CORS/XSRF: framework defaults, no application override originally.
- Third-party network services: none in application code before OIDC integration.
- Logging: Python logging and errors printed to the UI; supplier names logged.
- Secrets: no credential-like values found by the targeted tracked-source scan;
  `.streamlit/secrets.toml` ignored, `.env` not ignored. This is not proof that
  credentials have never appeared in history or in external deployment settings.

## Findings (initial prioritization)

| Severity | Finding | Location | Fix | Status |
|----------|---------|----------|-----|--------|
| HIGH | S01: anonymous real upload/calculation/approval | app.py | OIDC adaptation, server roles, synthetic-only demo | Fixed in app; production activation depends on S08 |
| HIGH | S02: suffix-only uploads and unbounded spreadsheet parsing | src/data_loader.py | Content/schema/archive/row/cell bounds in secure_files.py | Fixed |
| HIGH | S03: expensive calculations lack resource controls | src/preprocessing.py, src/forecasting.py | Bounded worker, workload/rate/concurrency limits | Fixed in app; OS/cluster limits require deployment |
| HIGH | S04: spreadsheet formula injection in exports | src/ordering.py, src/pipeline.py | Shared string neutralization for CSV/XLSX | Fixed |
| MEDIUM | S05: arbitrary approver label and positional editor trust | app.py | Verified actor, canonical rows and allowlisted edits | Fixed |
| MEDIUM | S06: temporary retention/private cache/extra personal columns | app.py, src/preprocessing.py | Owner/session registry, expiry, data minimization, per-run pseudonyms | Fixed for application-managed files; framework buffers documented |
| MEDIUM | S07: raw errors and business data in logs/UI | app.py, src/pipeline.py | Safe errors and redacted event logging | Fixed |
| MEDIUM | S08: production TLS/gateway/headers/transport isolation absent | deploy/nginx.conf.example | Template and documented mandatory staging checks | OPEN — deployment not provisioned or verified |
| MEDIUM | S09: .env/private keys not ignored | .gitignore | Ignore rules and placeholder templates | Fixed |
| MEDIUM | S10: unpinned dependencies and missing audit procedure | requirements.txt | Audited runtime constraints, dev scan tools; vulnerable local pip updated | Fixed for tested environment |
| LOW | S11: local demo deserializes joblib | src/demonstrate.py | Rebuild model from local data instead | Fixed |

SQL injection, IDOR against a database, password reset, JWT decoding, arbitrary
outbound URL fetching, open redirects and prototype pollution have no matching
application surface. Their absence is documented rather than adding artificial
endpoints solely to test them. File ownership and server-side action checks are
tested at the actual Streamlit/service boundaries.

## Verification (2026-09-23)

Initial priority totals: **0 Critical, 4 High, 6 Medium, 1 Low**. Ten findings
received application/code fixes; **one deployment finding (S08) remains open**.
This is a scoped review count, not a guarantee that no other vulnerability exists.

| Check | Result |
|---|---|
| Baseline regression suite | 44 tests passed before changes |
| Final project/security suite | 85 tests passed, including 41 added checks |
| Streamlit AppTest | Demo startup/calculation/approval; production missing-config and invalid-mode fail closed |
| Real local worker | CSV upload, preserved SKU leading zeros, outputs, raw-input cleanup, privacy and analyst approval denial passed |
| Malicious input checks | Extension/MIME/path, binary disguises, oversize, ZIP/XML expansion, formulas, external links, macros, sheets and calendar limits passed |
| Access control | Missing/expired identity, issuer/subject/role rejection, ownership/session IDOR, mass assignment and rate limiting passed |
| HTTP on loopback:8502 | Health/page 200; upload PUT and DELETE without XSRF 403; hostile-origin WebSocket 403; hostile preflight did not receive matching/wildcard origin |
| Bandit | 0 findings, 0 scan errors; fixed-worker subprocess exceptions reviewed |
| pip check | No broken requirements |
| pip-audit installed environment | 83 dependencies, 0 known vulnerabilities after updating pip |
| pip-audit -r requirements.txt | 55 resolved runtime dependencies, 0 known vulnerabilities |
| Targeted secret scan | 141 historical Git blobs and 45 project working files; no matches for selected credential/private-key patterns; values never printed |

The initial environment audit reported six unique pip advisories (some returned
twice by the feed), fixed by upgrading local pip 25.0.1 to 26.2.1. Runtime
dependencies had no known findings in that scan. The installer upgrade is a local
environment change and is documented for other machines. Unused Plotly was removed
from this Altair frontend; pytest/scanning tools are in requirements-dev.txt.

Signature verification is delegated to Streamlit/Authlib. Local tests exercise
unverified-identity rejection and the application claim/role boundary, not a live
IdP. A real OIDC round trip, provider signature/algorithm rejection, logout across
tabs, proxy cookie flags, CSP browser compatibility, and Linux resource enforcement
still need staging verification. There is no database/JWT/REST implementation to
pretend-tested with artificial endpoints. SQL/HTML payload tests confirm these
values remain table data and that raw HTML rendering is absent; they do not
constitute a comprehensive browser XSS assessment.

The user's long-running local server on port 8501 displayed a redacted startup
error while a fresh server from the same checkout worked. The old process remained
on the IPv6 listener after SIGTERM; it was stopped and replaced. Both localhost and
127.0.0.1 then returned healthy responses, and the UI/demo ran again. The original
traceback was not available, so the underlying old-process exception is not claimed
to be diagnosed. Restart the server after dependency/module changes.

## Changes and control boundaries

- New src/security.py, src/secure_files.py, src/web_auth.py, src/web_service.py and
  src/worker.py implement shared validation, OIDC/RBAC, per-session workspaces and
  bounded calculation.
- app.py calls those checks around uploads, results and approval while retaining
  the supplier dashboard, article card, trends and downloads.
- Loader/preprocessing/config/forecasting/evaluation/ordering/pipeline/demo modules
  bound computation, minimize personal data and harden export/deserialization.
- .streamlit/config.toml, .gitignore, .env.example, secrets.example.toml and the
  nginx template establish safer defaults and placeholder-only deployment inputs.
- requirements.lock/dev requirements, security tests, README.md and SECURITY.md
  document installation, verification, roles, routes and remaining limitations.
- User-owned .agents/ and .claude/ were not edited or included in the change.

## Remaining risks and production settings

S08 is **not fixed merely by adding a template**. Configure HTTPS, an
identity-aware gateway protecting every Streamlit path, exact origin/host,
Secure/HttpOnly/Lax auth cookies, OIDC allowlists and MFA, and OS-level CPU/memory/
disk quotas. Keep port 8501 private and run as an unprivileged user. Validate the
CSP and file/WS/auth routes with the actual domain before accepting business data.

Framework media URLs remain bearer capabilities among admitted gateway users;
filesystem expiry does not revoke downloaded files or instantly clear browser/
Streamlit memory. Login/logout is not universal session revocation. Rate limits
are process-local, the approval log is not a durable tamper-evident ledger, and
parser bugs/supply-chain risks remain possible despite limits and scans.
See [SECURITY.md](SECURITY.md) for the complete route inventory, constraints,
deployment sequence and limits of verification.

No third-party infrastructure was attack-scanned, no production identity provider
was configured, and no credentials were generated or published for the user.
