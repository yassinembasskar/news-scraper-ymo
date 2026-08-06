# SAP Attribute List — valid `concerned_param` values

This is the strict, authoritative list of values the LLM Insight stage is
allowed to put in `concerned_param`. Fed to the model alongside
`llm_insight_business_logic.md`, which explains *why* each one moves and
when — this file only defines *what's allowed*, so keep entries short.

Seeded from the fields already named and explained in
`llm_insight_business_logic.md`. This is a starting point, not a complete
list — extend it with the rest of your actual SAP master-data attributes as
they come up; anything not listed here is off-limits to the model, so an
article naming a real disruption with no matching entry here should come
back "not relevant" rather than force-fit onto the nearest wrong field.

## Inbound / procurement

- `MARC-PLIFZ` — Planned Delivery Time (plant-level Material Master)
- `MARC-EISBE` — Safety Stock (plant-level Material Master)
- `EKPO-ETFZ` — Firm Zone (Scheduling Agreement item)
- `EORD-FLIFN` — Source List indicator (fixed / blocked)
- `Scheduling Agreement PDT` — lead time on an active fixed-source agreement (`ME33N`), overrides `MARC-PLIFZ` when a named supplier/agreement is in play

## Outbound / distribution

- `VBEP` — schedule line delivery date (customer order line)
- `LIKP` — delivery header / planned goods issue date
- `VBAK` — sales order status
