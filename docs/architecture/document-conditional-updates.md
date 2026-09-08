# Conditional document updates

Use `tables.update(..., expected_updated_at=reviewed.updated_at,
expected_data=reviewed.data)` when applying a reviewed change that must not
replace newer data. The timestamp must include its timezone. `expected_data`
is optional, but requires the timestamp and checks the entire JSON document.
Updates still merge the supplied fields into existing data.

The SDK calls a dedicated conditional endpoint. An older server returns an
error instead of silently accepting an unconditional write. Deploy the API
and worker SDK before adopting conditional calls. Ordinary updates keep their
existing behavior.

The server applies the same organization, Solution ownership, attribution and
row-policy checks as ordinary updates. One PostgreSQL UPDATE compares the
table ID, document ID, reviewed timestamp and the data used for authorization.
Only a matching row changes. Its timestamp advances by at least one microsecond.
A concurrent edit or delete after the access check returns 409 without publishing
an update event. A missing row at initial lookup returns 404.

Conditional calls do not retry transport errors or transient 5xx responses.
A 409 is a conflict, not permission to retry with a fresh revision. After a
lost response, read the row and reconcile the intended result before deciding
whether another write is safe. This contract protects one document update;
it does not make vendor actions or multiple document writes transactional.
