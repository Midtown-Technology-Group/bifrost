# Maintainer directed contribution records

The principal authorizes the task. The agent records what it did and the source
it produced. This route removes repeated personal sign-off prompts for qualifying
maintainer-directed work while keeping authorship and contribution evidence honest.

## Record the contribution

Use one PR-level record, updated for the current head:

- Principal: the named maintainer and verified GitHub user identity
- Agent: the assistant or service that performed the work and its role
- Authorization: a concise account of the actual task and approved scope
- Contribution: repository, PR, base/head, exact new commit set and changed paths
- Provenance: relevant reused source, authors, licenses and any unresolved limits
- Verification: exact-head tests/checks, review disposition and delivery boundary

The agent may write this record from the actual authorization it received. Do
not publish private conversations, credentials or customer information to prove
the task existed. Keep any private evidence in its authorized location and give
the public record only the context needed to review the contribution.

The record must label itself as agent-recorded authorization and provenance.
It must not say the principal personally entered a sign-off, reviewed every
commit or made a certification unless that is independently established.
Ordinary amendments within the approved task do not require the principal to
re-enter the same instruction. Material scope or consequence changes still do.

## Accept the right evidence

Read commit/signature evidence from GitHub and bind the record to the current
source. Contributor identity, task authorization, source/license provenance,
quality evidence and deployment authorization are separate facts. A missing
DCO trailer remains missing even when this alternative acceptance route applies.
Report acceptance as maintainer-directed provenance, not DCO-certified.

The principal's approved account can be used manually or by their authorized
agent. Therefore account type `User` and a Verified badge do not prove a personal
human action. This policy deliberately accepts the named principal's delegated
account activity for contribution submission. It does not create a human-only
approval signal or allow an agent to appoint itself or a new principal.

A machine consumer must load the applicable principal policy from protected
main, independently fetch exact source and authenticated record identity, and
reject stale/mismatched records or an unapproved issuer. Candidate-authored JSON,
an arbitrary label or a copied record cannot authorize an action. A record
checker establishes conformance to this contribution route only; it does not
grant merge, spending, access or deployment authority.

## Adoption and existing branches

This route applies after the repository explicitly adopts it. Existing open
maintainer-directed branches may use it for future admission once their current
source, record and required checks meet the adopted policy. No old commit is
rewritten, no historical trailer is claimed to exist, and prior third-party
contributions do not become principal-authored work.

Other repositories and upstream projects retain their own contribution rules
until their maintainers separately adopt a compatible route. Required DCO apps
or organization web sign-off controls must be inspected before adoption; this
document cannot disable or bypass them. Any corresponding configuration change
requires its own exact reviewed change. Human-only controls remain human steps.

Before adoption, record one bundled decision covering the exact proposed
contribution policy, named principal, permitted authenticated publishing
identity, evidence format and any affected admission check. Reassess the
contribution-related [OpenSSF assurance notes](../security/openssf-best-practices-badge.md).
Do not merge this proposal on the assumption that its candidate policy already
authorizes its own admission. Until adoption, the protected base policy and any
explicitly recorded, commit-bounded maintainer exception remain authoritative.
