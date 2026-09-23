# Security architecture and deployment

This MVP processes sales files in Python/Streamlit. Its UI uses native widgets and
Altair; it has no application REST API, database, ORM, custom JavaScript, local
password store or user-supplied outbound URLs. CLI execution assumes a trusted
operator. Web uploads are untrusted. This document describes implemented controls
and the deployment work still required; it does not certify security.

## Authentication and sessions

The default `APP_MODE=demo` exposes **synthetic examples only**. It cannot upload
real files or approve a real-data workspace. Unknown modes stop the app.
`APP_MODE=production` requires configured Streamlit OIDC; missing/invalid
configuration fails closed.

Use `st.login`, `st.user`, `st.logout` and Authlib, not application JWT decoding.
The provider's discovery metadata, client ID and secret are administrator-owned.
Authlib verifies the OIDC response, signatures, audience and protocol state; the
app additionally checks the exact issuer, subject allowlist and token expiry.
Never pass request JSON into `principal_from_claims`: it only adapts already
verified `st.user` claims. Tokens are not exposed through `st.user.tokens`.

Streamlit 1.64 signs its identity cookie and sets HttpOnly/SameSite=Lax, but
**does not set Secure itself**. The HTTPS proxy template enforces Secure on
upstream cookies. Leave the XSRF cookie readable by the frontend. Verify these
attributes in a real browser behind the final proxy.

The framework's cookie may persist for 30 days. The app checks the provider's
`exp` on every rerun and service action, so that cookie is insufficient after the
identity expires. Identity changes clear application session state; workspace
tokens and session keys use cryptographically random values. Logout removes the
current workspace and browser identity. It does not revoke previously downloaded
files, other active browser tabs, or the separate gateway login. Configure gateway
logout/short session expiry and require reauthentication at the provider. A
live, idle WebSocket is not proactively revoked at the instant of expiry; the next
action checks it. Removing a role requires rerun/restart and gateway revocation
for immediate operational access removal.

## Authorization

Roles come only from the server's `[access.users]` mapping of immutable OIDC
subjects. Email and client-supplied role claims grant no permission.

| Role | Demo | Real upload/calculation | Own results/export | Approve real orders |
|---|---|---|---|---|
| demo | Yes; educational approval only | No | Own synthetic run | No |
| viewer | Yes | No | Yes, if owned | No |
| analyst | Yes | Yes | Yes | No |
| manager / admin | Yes | Yes | Yes | Yes |

Every result lookup requires both the authenticated subject and the browser's
server-side session key. There is no admin ownership bypass. Results are referenced
by random opaque tokens, not paths. Approval reloads the canonical order table,
accepts only unique `sku` and `approved_qty`, verifies supplier membership and
quantity/stock/MOQ/pack rules, and derives the actor from the verified identity.
The operation generates a download; it does not send a purchase order externally.

## Actual routes and actions

Routes below are framework-owned, verified against installed Streamlit 1.64.
There are no JSON business endpoints. Service errors have 400/401/403/410/413/429
semantics and are rendered as safe UI messages; they are not invented REST status
responses. The production gateway must protect **all** Streamlit paths.

| Method / path | Authentication / role | Validation / rate control | Sensitive data |
|---|---|---|---|
| GET / and static assets | Gateway in production | Exact host/origin; ingress rate | App shell |
| GET /auth/login, /oauth2callback | OIDC initiation/callback + gateway | Framework state, nonce, signatures; gateway/provider login limits | Identity/cookies |
| GET /auth/logout | Framework identity + gateway | Framework logout | Removes app identity |
| WS /_stcore/stream | Gateway + app OIDC in production | CORS/XSRF handshake, message cap; action-specific RBAC | Inputs/results |
| PUT /_stcore/upload_file/{session_id}/{file_id} | Gateway; Streamlit session/XSRF | 20 MiB/file, 21 MiB proxy body; ingress rate | Raw uploads |
| DELETE /_stcore/upload_file/{session_id}/{file_id} | Gateway; framework XSRF | Framework file/session handling | Uploaded file |
| OPTIONS upload path | Trusted browser origin at ingress | No wildcard credentialed CORS | No business response |
| GET /media/{file} | Gateway; generated capability URL | Allowlisted creation through service, no-store | Downloads |
| GET /_stcore/health, /_stcore/host-config | Loopback or protected gateway | Rate at ingress | Framework health/config |
| calculate (WS action) | analyst/manager/admin, or demo-only action | All file/schema limits; 3/user/5min, 12/process/5min, one active worker | Sales and result |
| read/export (WS action) | read/export permission + owner/session | Workspace expiry and artifact allowlist | Own results |
| approve (WS action) | manager/admin + owner/session | Canonical rows, quantity bounds, allowlisted changes | Approved order |

Framework upload/delete handlers and media delivery do not call application RBAC.
An app login screen alone does not secure those transport routes. Keep port 8501
private and use the identity-aware gateway in the deployment template.
Generated download URLs are bearer capabilities: any admitted gateway user who
receives a live URL may fetch it. Do not share those URLs. Strict per-user download
authorization/revocation requires an authenticated download service, beyond the
current Streamlit MVP; consider this before handling highly sensitive datasets.

## Input and file validation

Only CSV and data-only XLSX are accepted. Files have randomized names in private
temporary directories outside application/static directories. Client paths,
symlinks, double-dot paths and unexpected extensions/MIME types are rejected.
`application/octet-stream` is allowed only with full content validation.
CSV supports UTF-8/BOM or Windows-1251, and comma, semicolon, tab or pipe delimiters.
Executable signatures, NUL bytes, disguised HTML/XML and malformed rows are
rejected. Headers must be unique and nonempty.

XLSX archives are inspected before openpyxl: maximum 128 members, 80 MiB expanded
total, 40 MiB/member, compression ratio 200. Paths, duplicate members, encrypted
entries, macros, formulas, external relationships, embedded binaries and XML
DTDs/entities are rejected. Exactly one sheet is required. This strict data-only
format intentionally rejects decorated/linked workbooks; export values first.

| Bound | Limit |
|---|---|
| File / combined input batch | 20 MiB / 60 MiB (actual bytes checked) |
| Table rows / columns / populated rectangular cells | 100,000 / 40 / 2,000,000 |
| Cell text / filter text | 2,048 / 128 characters |
| SKUs / warehouses | 2,000 / 100 |
| History span / expanded daily rows | 3,653 days / 200,000 |
| Forecast / lead time / review period | 365 / 275 / 90 days |
| Nonnegative numeric inputs / approved quantity | At most 1,000,000,000 |
| z / optional cross-validation splits | 0–4 / 0–5 |

Limits apply server-side, including calendar expansion before allocation, finite
numeric values and reference schemas. Invalid sales dates/quantities follow the
documented cleaning rules; the app rejects empty results. SQL-like and HTML-like
cell strings stay data: there is no SQL executor, raw HTML rendering or shell
interpolation. Native dataframe/chart widgets render labels. All CSV/XLSX exports
neutralize formula-prefixed strings, including whitespace/control-prefix bypasses;
real numeric negatives remain numeric. A leading apostrophe can be visible in
downstream systems: verify import behavior with your 1C configuration.

## Computation and storage

A single-process limiter admits one worker at a time, at most 3 jobs per subject
and 12 overall per 5 minutes. Public demo users share one subject/rate budget.
Each worker has a fixed argument vector, no shell, no inherited secret variables,
single-threaded numerical libraries and a 120-second timeout. On Linux it also
sets 2 GiB address-space, 115-second CPU and 150 MiB/file limits. These Linux limits
were not executed in the macOS validation environment. Apply container/service
CPU, memory, process and temporary-disk quotas as well; a subprocess is not a
security sandbox and parsers may have undiscovered flaws.

At most 24 result workspaces are retained for one hour; a background reaper checks
every minute. Raw copied files and job instructions are removed after processing,
and failures remove the workspace. No global private-result dataframe cache is
used. Raw browser uploads and rendered results may remain in Streamlit session
memory until the session is cleared/disconnected; the filesystem TTL does not
erase a user's browser, framework media buffers or downloaded files. Use private
encrypted temporary storage, disable core dumps, clean stale procurement temp
directories on service startup after a crash, and restart workers to clear process
memory where required. Local CLI outputs are operator-managed and have no TTL.

## Privacy, errors and logging

Unknown columns are dropped; transaction IDs are used only for deduplication then
removed. Customer IDs receive a new random salt per processing run and a 128-bit
pseudonym. Pseudonyms are consistent within a run and differ across runs; they are
not a claim that the entire dataset is anonymous. Do not put personal information
in product/supplier names. Web runs persist neither transaction-level audit tables
nor serialized models. CLI audit/model persistence is retained for trusted use.

The UI shows controlled validation errors or a generic reference code, never raw
parser exceptions or paths. Security events include action/denial codes, hashed
actors and generated order references, without customer rows, tokens or secrets.
Unexpected errors log their exception type/reference only. Forward these events
to access-controlled logs with retention/alerts; this MVP has no durable,
tamper-evident approval ledger. The proxy template logs status/method/size, excluding
URLs, query strings, identities and cookies. Apply equivalent redaction to gateway,
identity-provider and platform logs.

## Secrets and production setup

1. Use Python 3.12 and install with `python -m pip install --upgrade 'pip>=26.2,<27'`
   followed by `python -m pip install -r requirements.txt`.
2. Copy `.streamlit/secrets.example.toml` to server-only
   `.streamlit/secrets.toml` (or mount through your secret manager).
   Set real HTTPS redirect/discovery URLs, issuer and client credentials.
   Generate a cookie secret from at least 32 random bytes; never use the example.
   Give the file owner-only permissions, never commit it, and rotate on disclosure.
3. Register an exact HTTPS callback ending in `/oauth2callback` with your IdP.
   Use a dedicated client, short ID-token lifetime, MFA and a server subject
   allowlist. Restrict discovery/egress to the trusted provider. No provider URLs
   come from users. Configure login abuse protection at the provider and gateway.
4. Set `APP_MODE=production`, `STREAMLIT_BROWSER_SERVER_ADDRESS` to the public
   host and `STREAMLIT_BROWSER_SERVER_PORT=443`. The `.env.example` is a reference;
   the app does not automatically load dotenv files. Secrets use Streamlit's
   secret configuration, not browser environment variables.
5. Run `python -m streamlit run app.py` as a dedicated unprivileged account on
   loopback. Allow external traffic only through HTTPS and the trusted gateway.
6. Adapt `deploy/nginx.conf.example` with your host/certificates and
   oauth2-proxy (or equivalent) on loopback:4180. Configure the gateway with its
   own random cookie secret, Secure/HttpOnly/Lax cookies, OIDC tenant/user allowlist,
   short lifetime and logout flow. Both gateway and app authentication must work;
   the template does not provision the gateway or identity provider.
7. Verify `nginx -t`, login/logout, expired/invalid tokens, cross-user files,
   WebSocket CORS/CSRF, cookie flags, and chart/download behavior in staging.
   The proposed CSP allows inline **styles** for Streamlit, no script
   unsafe-inline/unsafe-eval; compatibility must be checked with the final browser
   bundle. Other headers: HSTS only on HTTPS, nosniff, no-referrer,
   Permissions-Policy, frame-ancestors none and no-store.
8. Use one application process initially, OS/container quotas and private storage.
   For multiple replicas, add shared rate limiting, reliable job execution,
   authenticated artifact storage and session routing. Process-local limits do
   not provide a cluster-wide quota.

CORS and XSRF remain enabled; static serving and UI traceback details are disabled.
No Docker configuration exists; if containerizing, add a non-root user, a minimal
pinned image, read-only application filesystem, private writable temp volume,
secret mounts and a .dockerignore excluding credentials/private data. Production
domain, certificates, gateway and provider are not configured by this change.

## Dependencies and repeatable verification

`requirements.lock` pins the runtime dependency set used with Python 3.12;
`requirements.txt` applies it as constraints. Linux watchdog and Windows tzdata
are also pinned; those platforms still require their own installation/tests.
Pins are version constraints, not wheel hashes or a supply-chain attestation.
Use a trusted package index and refresh reviewed pins regularly, then run:

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
python -m unittest discover -s tests -v
python -m bandit -r src app.py main.py
python -m pip_audit -r requirements.txt
python -m pip_audit
```

Bandit has two narrow reviewed suppressions for importing subprocess and invoking
the fixed, bounded worker without a shell. No application code calls joblib.load,
pickle.loads, eval or os.system. The CLI may still write a model artifact, which
must never be loaded from an untrusted source.

Scan tracked changes and history for credentials before publishing. .env, private
key extensions and Streamlit secrets are ignored; templates contain placeholders.
Ignore rules do not remove previously committed secrets. If any credential is
found in Git or logs, revoke/rotate it and follow the host's history-removal
procedure. Report suspected issues privately to repository maintainers without
posting sales data or credentials in a public issue.

## Remaining risks and scope limits

Deployment finding S08 remains open until a real HTTPS/gateway/IdP setup passes
staging checks. Nginx/CSP/cookie and Linux resource-limit behavior cannot be
certified from macOS AppTest. See the route table for framework media capabilities
and the session section for logout/revocation limitations. Distributed quotas,
malware scanning, a durable audit ledger and authenticated per-user download
storage are future production work when the sensitivity/scale demands them.
No external infrastructure was scanned. Passing tests and vulnerability databases
cover known cases, not all possible attacks.
