# Workplace monitoring: legal landscape pointers

**Not legal advice.** This file exists so the code and policy template ask the right questions.
Laws change; verify every item against current official sources and have the operator's
counsel review before any sample policy wording ships (SPEC §11).

## Why the product enforces disclosure

Many jurisdictions require employers to tell employees about electronic monitoring in advance,
sometimes in writing with acknowledgement, and to limit collection to a stated purpose. The
SPEC §6 gates (published policy, notice period, posted notice, minimisation, retention cap,
audit) are designed to support those obligations, not to replace the employer's own legal review.

## Things to check per jurisdiction

| Question | Maps to |
|---|---|
| Is advance written notice required? How long before monitoring starts? | `notice_days`, effective date gate |
| Must employees acknowledge it (signed / electronic)? Named or anonymous? | §6.3 acknowledgement, `ack_by` |
| Must a notice be posted at the workplace? | posted-notice confirmation gate |
| Is consultation with a works council / union / staff representative required? | template "consultation status" field |
| Is an impact assessment required before deployment? | template, operator checklist |
| What lawful basis / purpose limits apply? | template "purpose" field, after-hours alert rule |
| Maximum or "no longer than necessary" retention? | `retention_days` (hard cap 90) |
| Employee right to access the data? | §6.5 transparency view |

## Examples to research (verify current text before relying on them)

- **EU / UK**: GDPR / UK GDPR — transparency obligations to data subjects, purpose limitation,
  data minimisation, and data protection impact assessments for systematic monitoring.
  Some member states require works-council agreement for technical monitoring systems
  (e.g. Germany's Works Constitution Act).
- **United States**: no single federal rule; several states have employee electronic-monitoring
  notice statutes (for example New York, Connecticut, Delaware). Check each state where the
  employer operates.
- **Other regions**: check national privacy/data protection law and labour law; many have
  similar notice and minimisation requirements.

## What the product must never do regardless of jurisdiction

- Monitor without a published, effective policy.
- Hide that collection is active (the transparency view always shows status).
- Collect more than occupancy booleans in workplace mode.
- Be repositioned as covert surveillance of individuals. Requests in that direction are out of
  scope (CLAUDE.md "Scope limits").
