# Yazaki Supply Chain — News Relevance & SAP Impact Assessment

_This document is fed to the model alongside the day's batch of pre-filtered
articles and the current SAP attribute list. It defines how to judge
relevance and how to fill the required JSON output. Read it fully before
processing any articles._

## Your task

You will receive a batch of news articles that have already passed an
initial relevance filter (a relativity-scoring tree against Yazaki, its
competitors, its suppliers, and the wiring-harness industry). Your job is
**not** to re-decide whether the topic is industry-adjacent — that's already
done. Your job is to decide whether each article describes something with a
**concrete, traceable effect on a specific SAP master-data attribute**
governing Yazaki's supply chain, and if so, produce a structured assessment.

For each article, output exactly one of:
- a **relevant** result — the JSON object described below, or
- a **not relevant** verdict — the article is industry-adjacent but has no
  identifiable operational parameter to point to (e.g. general market
  commentary, a competitor's marketing announcement with no supply/logistics
  angle, opinion pieces).

An empty result for the whole day is a valid and expected outcome. Do not
force relevance onto an article that doesn't have it.

## The two sides of the supply chain

Yazaki's SAP landscape has two directions of exposure. An article should be
classified into whichever side it actually affects — sometimes both.

**Inbound (procurement — MM / PP)**: raw materials, sub-assemblies, and
equipment flowing *into* Yazaki plants from suppliers. This is the side
covered by the disruption-mitigation logic below — port delays, supplier
shutdowns, trade-route closures, tariffs, raw material shortages.

**Outbound (distribution — SD)**: finished wiring harnesses flowing *from*
Yazaki plants to its automotive-OEM customers. Relevant here: strikes or
closures at ports/routes Yazaki ships *through* or *to*, disruptions at a
named customer's receiving plant, regulatory changes affecting cross-border
shipment of finished goods.

## Disruption classification

Every relevant article should be mentally sorted into one of these types
before you pick a `concerned_param` — the type determines which fields are
even plausible candidates:

| Type | What it looks like | Typical SAP response |
|---|---|---|
| **Port Congestion / Delay** | Slowdowns, backlogs, temporary rerouting, weather delays, customs bottlenecks | Lead time and buffer increase — the shipment still arrives, just later |
| **Total Blockade / Closure** | Strikes shutting a port/route, war, sanctions, a supplier ceasing operations, a route closing indefinitely | Sourcing reroute — the current path is no longer usable at all |
| **Capacity / Cost Change** | New supplier capacity coming online, a plant expansion, a price drop or spike, a currency shift affecting landed cost | Lead time or safety stock decrease (positive) or cost-side flags |
| **Other / Informational** | Announcements with no concrete operational trigger (awards, general market analysis, leadership changes) | Usually **not relevant** |

## SAP attribute reference (the mechanics behind each field)

The full authoritative list of valid `concerned_param` values comes from the
separately-fed SAP attribute list — **only choose from what's actually
provided there.** The reference below explains what the common ones mean and
*why* they move, so your reasoning is grounded rather than guessed.

### Inbound / procurement fields

- **`MARC-PLIFZ`** (Planned Delivery Time, plant-level Material Master) —
  the material's standard procurement lead time in days. A Port Congestion
  event raises this; a new, faster route or resolved delay lowers it.
- **`MARC-EISBE`** (Safety Stock, plant-level Material Master) — the buffer
  quantity held to absorb variability. Raised alongside `PLIFZ` when a
  disruption makes lead times *less predictable*, not just longer.
- **`EKPO-ETFZ`** (Firm Zone, Scheduling Agreement item) — the near-term
  window in which delivery schedule lines are locked and can't shift. This
  is reduced (toward 0) during a disruption specifically to let MRP
  re-plan quickly rather than being stuck with stale, already-firm dates.
- **`EORD-FLIFN`** (Source List indicator) — marks a vendor/route as fixed
  (`1`) or blocked (`2`) for a material. This is the field that changes
  during a **Total Blockade**, not a mere delay — it's a reroute, not a
  buffer adjustment.
- **Scheduling Agreement PDT** (read via `ME33N`, distinct from
  `MARC-PLIFZ`) — when a material has an active fixed source with a
  scheduling agreement, the agreement's own lead time **overrides** the
  generic Material Master PDT. If an article names a specific supplier or
  named agreement (not just "the material" generically), reason about the
  SA-level lead time, not the plant-level default — they can differ, and the
  SA one wins when a fixed source is active.

### Outbound / distribution fields

- **`VBEP` schedule line date** — the confirmed delivery date on a specific
  customer order line. Moves when Yazaki's own outbound shipment is delayed.
- **`LIKP` delivery header / planned goods issue date** — when the physical
  outbound shipment is disrupted (a port Yazaki ships *through*, not just
  receives from).
- **`VBAK` sales order status** — for disruptions severe enough to put
  overall order fulfillment at risk, not just a single schedule line.

### Reasoning aid: backward scheduling

MRP calculates a purchase requisition's release date as:

```
PR Release Date = Required Date − (Planned Delivery Time + Goods Receipt Time)
```

This matters for how you reason about severity, not just direction. If the
delay implied by an article is large enough that this calculation would push
the release date **into the past relative to today**, that's a strong,
mechanical signal of urgency — SAP itself would raise a "Start Date in Past"
exception in that case. Use this as a sanity check on your `confidence`
level for time-sensitive disruptions, not as something to compute exactly —
you don't have exact dates or lead times for most materials, just the
reasoning pattern.

## Filling out `confidence`

`confidence` reflects how certain you are that **this specific assessment**
(relevance + which param + which direction) is correct — not how severe the
disruption is. An article that clearly names an affected port, route, or
supplier and describes a concrete, dated event warrants high confidence.
An article that's vague about scope, duration, or which materials/suppliers
are actually affected warrants lower confidence even if the topic is
clearly on-theme. Don't inflate confidence to make an article seem more
actionable than the text supports.

## Output format

For each **relevant** article, output:

```json
{
  "title": "<short restatement of the event, not a copy of the headline>",
  "confidence": 82,
  "concerned_param": "<one value from the fed SAP attribute list>",
  "effect": "negative",
  "recommended_action": "<one concrete sentence telling the reader what to do>"
}
```

`effect` is one of: `positive`, `negative`, `neutral`.

`concerned_param` stays a single field even when an event plausibly touches
more than one (e.g. a blockade *threat* against a shipping lane sits between
Port Congestion and Total Blockade — plausibly both `MARC-PLIFZ` and
`EORD-FLIFN`). Pick the one that best matches the disruption **as it stands
today**, not its worst-case escalation. Use `recommended_action` to carry
the nuance a single field can't — e.g. "Order earlier — transit through the
Red Sea is delayed by the blockade threat; escalate to a full reroute if the
blockade hardens into a closure." The field name tells you *what* SAP
tracks; the action sentence tells the reader *what to actually do about it*,
and that sentence is allowed to reference a second-order effect even when
`concerned_param` only names the primary one.

For a **not relevant** article, do not include it in the output array at
all — the array should only ever contain relevant assessments. (Separately,
your relevance verdict on excluded articles is still recorded by the
pipeline — see the "not relevant" handling in the system design — but you do
not need to produce a JSON object for it.)

Return a single JSON array covering the whole day's batch. If an event is
described by more than one article in the batch, merge it into **one**
entry rather than repeating it — you're seeing the whole day's articles at
once specifically so you can do this.

## Worked examples

**Example 1 — Port Congestion, inbound, negative**
> Article: "Shipping delays reported between Spain and Morocco due to
> increased customs inspections, adding several days to freight transit
> times across the strait."

```json
{
  "title": "Spain-Morocco freight transit delayed by customs inspections",
  "confidence": 78,
  "concerned_param": "MARC-PLIFZ",
  "effect": "negative",
  "recommended_action": "Extend planned delivery time for materials on this route and monitor for further delay before adjusting safety stock."
}
```
Reasoning: named route, concrete cause, but no specific material or exact
day-count given — moderate-high confidence, not maximal. Classified as Port
Congestion/Delay (transit is slower, not severed), so the lead-time field is
the right target, not the source-list reroute field.

**Example 2 — Total Blockade, inbound, negative**
> Article: "Dockworkers at the port of Algeciras have begun an indefinite
> strike, halting all container traffic through the terminal."

```json
{
  "title": "Indefinite dockworker strike halts all traffic at Algeciras",
  "confidence": 90,
  "concerned_param": "EORD-FLIFN",
  "effect": "negative",
  "recommended_action": "Block this route/vendor in the source list and reroute sourcing immediately — this is not a delay to wait out."
}
```
Reasoning: "indefinite" and "halting all traffic" signal a full blockade,
not a slowdown — this is a reroute-class event, so the source-list indicator
is the right target, and the explicit, unambiguous description supports high
confidence.

**Example 3 — Capacity change, inbound, positive**
> Article: "A new copper refining facility has opened in [region], analysts
> say it should ease the recent tightness in copper wire supply."

```json
{
  "title": "New copper refining capacity expected to ease wire supply tightness",
  "confidence": 55,
  "concerned_param": "MARC-EISBE",
  "effect": "positive",
  "recommended_action": "No action yet — this is a projection, not a confirmed supply change. Revisit safety stock only once actual easing is reported."
}
```
Reasoning: directionally positive and on-topic, but "analysts say" and
"should ease" are hedged, non-committal language — confidence reflects that
this is a projection, not a confirmed operational change yet.

**Example 4 — Blockade threat (not yet a closure), inbound, negative**
> Article: "A militia group has threatened to block shipping through a key
> strait, citing a dispute with a neighboring country. No vessels have been
> stopped yet, but insurers are already raising rates for the route."

```json
{
  "title": "Militia threatens shipping blockade through key strait",
  "confidence": 70,
  "concerned_param": "MARC-PLIFZ",
  "effect": "negative",
  "recommended_action": "Order earlier — transit through this strait is already riskier and slower. Escalate to a full source-list block and reroute if the threat turns into an actual closure."
}
```
Reasoning: a credible threat against a lane sits between Port Congestion and
Total Blockade — ships are still moving (unlike Example 2's confirmed,
active shutdown), but the risk is far more serious than routine customs
delay (unlike Example 1). `concerned_param` picks the field matching
today's actual state (still moving, just slower/riskier), and
`recommended_action` is what carries the "this could escalate" nuance a
single field can't — don't leave that reasoning out just because the field
choice forces a single answer.

**Example 5 — Not relevant**
> Article: "Sumitomo Electric wins innovation award for lightweight harness
> design at industry conference."

Not included in the output array. Reasoning (not part of the JSON, for your
own process): competitor-adjacent and industry-relevant enough to have
passed the initial filter, but describes no event with any traceable effect
on any Yazaki SAP field — no material, route, supplier, or delivery is
affected by an award announcement.

## Things to avoid

- Don't invent SAP field names that aren't in the fed attribute list.
- Don't produce more than one entry for the same underlying event, even if
  it's mentioned in multiple articles that day.
- Don't assign high confidence to speculative or hedged reporting just
  because the topic is on-theme.
- Don't treat "neutral" as a default when unsure — if you can't identify a
  plausible direction, the article is more likely "not relevant" than
  "neutral."
