# Unapproved migration candidates

These files are review inputs, not a second migration history. Alembic discovers
`alembic/versions` only; no runtime imports or startup hooks consume this folder.
Unit tests may compile candidate SQL offline without applying it. Move an exact
reviewed candidate into `versions` only after its schema/authority gate is met,
rechecking the current main head and installed catalog first. Do not stamp or
apply a candidate from this directory.

The owner-links draft is C1 storage only. The C2 starts/receipts draft adds typed
Start and exact report evidence, including the Start clock key required by C3.
Neither enables Start admission, Result finalization or replay. This continuation
does not yet include grant/admission/resource records, restricted login/guard grants,
ancestor-write exceptions or physical-drain admission. In particular, native
FK detachment is not physical/source retirement, and PUBLIC revocation does not
restrict a migration/table owner. It must not be installed as a complete writer
or retention policy. The whole approved common continuation and matching tests
are required before any runtime owner is enabled.

Native attempt deletion clears only `native_attempt_id` on the retained live-link
row. That NULL marker cannot be deleted or reattached, even if a native UUID and
its original fence are recreated. Historical session/fence fields stay intact.
The column-targeted `SET NULL` uses the repository's PostgreSQL 16 contract.
An empty-only downgrade requires read-committed isolation and migration-owner
table locks before checking emptiness; these are not runtime coordinator grants.

C2 preserves the prototype's immutable raw report bytes and 16 MiB storage
bound. These are sensitive payloads, not an approved retention policy. No real
payload ingestion is authorized by a storage draft; disposition and complete
writer/admission enforcement remain required before enabling a runtime owner.
