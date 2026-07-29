# Synthetic data contract

## Stable identifiers

All related records use stable prefixed string IDs:

| Prefix | Entity |
| --- | --- |
| `cus_` | customer |
| `con_` | contact |
| `dep_` | deployment |
| `doc_` | document |
| `pas_` | document passage |
| `tkt_` | support ticket |
| `inc_` | incident |
| `msg_` | message |
| `mtg_` | meeting |
| `upd_` | customer update |
| `prj_` | project |
| `iss_` | project issue |

Records from separate systems join on `customer_id`; Highland should never join
on display names. Times are ISO 8601 UTC strings. URLs use the fictional
`*.summit.test` domain and are safe to render as non-routable demo links.

## Provenance

Any text that can be retrieved includes:

- `source_system`
- `record_id`
- `customer_id` where applicable
- `title`
- `text`
- `updated_at`
- `source_url`

Knowledge passages additionally include `passage_id`, `section`, and an ordinal.
MCP search results preserve these fields so a model citation can be resolved
back to a human-readable source.

## Permissions

Records include a `visibility` field such as `company`, `support`,
`engineering`, or `customer-success`. The mock services expose it but do not
authenticate users in milestone one. Highland’s retrieval and connector policy
layers must filter visibility before records are sent to a model.

This is intentional: authorization is a platform responsibility and must be
tested separately from the source data itself.

## Synthetic-data rule

Names, domains, incidents, account values, and messages are fictional.
Generated records must not be copied from real customer data. Any future sample
must use reserved domains such as `.test`, `.example`, or `.invalid`.
