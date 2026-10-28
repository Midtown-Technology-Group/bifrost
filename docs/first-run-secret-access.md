# First-run secret access

On a fresh installation, setup creates a Platform Admin in the provider
organization. **Grant me Secrets Reader** is optional and unchecked by default,
for both password and passkey registration.

Selecting it grants the built-in Secrets Reader role to the account being
created. This allows reading decrypted secrets **everywhere**: Global, the
provider organization, and all managed organizations, including future managed
organizations. It is not a single-organization grant. Platform Admin alone still
does not include `secrets.read`.

Account credentials, the setup-completion flag, the optional role assignment and
an `auth.setup_completed` audit event commit together. The event records the new
account, consent (including declined/omitted consent), role ID and boundaries.
Concurrent setup attempts have one winner. Passkey consent belongs to its
short-lived, single-use registration challenge; it cannot be added to a later
verification request. Failed registration does not complete bootstrap.

## Existing installations and recovery

Upgrading an existing Alembic-managed installation closes bootstrap without
changing any role assignments, even if all previous humans have been deleted.
Only a database migrated from no revision in one online run opens fresh setup.
Offline SQL migration scripts conservatively close setup; use normal online
migrations for a fresh installation. Setup also closes when the first account is
provisioned through SSO or the development seed-user path; those paths do not
grant Secrets Reader. Removing users does not reopen completed setup.

If setup finished without Secrets Reader, use a **different authorized
administrator** to grant it through the normal user role-assignment UI/API.
An existing Platform Admin can invite a second person into the provider
organization and grant them Platform Admin. That second administrator can then
grant Secrets Reader to the first administrator. Both actions use ordinary
privileged-access checks; the grantor need not hold Secrets Reader themselves.

You cannot change your own roles through normal role assignment. This change
does not provide an existing-user self-grant or break-glass endpoint. If no
second authorized principal can participate, this setup option is not a recovery
mechanism. No supported direct database edit or bootstrap-reset procedure is
introduced.

A failed/expired passkey attempt can be restarted while setup is still open.
After successful setup, duplicate, stale and replayed consent requests cannot
add access. Later users and later Platform Admin promotions never automatically
receive Secrets Reader. Existing assignments remain in place.

## Regression checks

Backend coverage lives in `tests/unit/services/test_first_user_setup.py` and
`tests/e2e/api/test_first_user_setup.py`. The migration/race tests create and
remove their own test databases. Fake passkey verification exercises transaction
boundaries without real authenticators or secrets.

Run the real first-run browser journey on the disposable test stack with
`./test.sh client e2e --config=playwright.first-run.config.ts`. It needs a fresh
database, so it has no dependency on the normal browser global setup (which
creates users before tests). Component and service tests cover declined consent
and forwarding the choice through both authentication methods.
