# Porchlight — design

**Date:** 2026-07-26
**Status:** Draft for review
**Scope:** One semester, solo build, portfolio piece
**Name:** Provisional. See section 7.

---

## 1. The problem

Two hazards kill isolated people in their own homes, and in both cases the
failure is the same: nobody knew the house had become dangerous.

**Heat.** Extreme heat is the deadliest weather hazard in the United States —
roughly 2,000 deaths a year, 2,394 in 2024, and heat deaths have nearly doubled
since 1999. The strongest single risk factor is lack of home air conditioning,
and among people who die of heat stress the place of death is most often an
un-air-conditioned home. Advisories are issued regionally and describe *outdoor*
conditions. People die of *indoor* conditions, which nobody measures.

**Power.** Over 2.5 million Medicare beneficiaries depend on electricity for
medical equipment at home — oxygen concentrators, ventilators, dialysis, powered
mobility. Their backup batteries last 3 to 8 hours. Residents of more than half
of US counties experienced at least one outage longer than 8 hours between 2018
and 2021, and outages grew 9% more frequent and 56% longer between 2014 and
2023. The 2003 New York blackout produced a 25% rise in disease-related deaths
over three days.

**What exists, and why it isn't enough.** HHS publishes emPOWER, a de-identified
count of electricity-dependent Medicare beneficiaries down to the ZIP code with
live hazard overlays. It is a map an emergency manager looks at, not an alerting
system, and it knows nothing about any individual. Utility "medical baseline"
registries are opt-in, siloed per utility, and are the systems that already
failed — PG&E once admitted it failed to notify 23,000 customers ahead of a
planned shutoff. On the heat side, cities have converged on the right answer,
direct outreach: Philadelphia runs volunteer block captains, New York a buddy
system, San Jose a Buddies Program. These work, and they run on spreadsheets and
phone calls.

**The gap this project fills.** There is no software that takes a hazard, a
roster of at-risk people, and an estimate of conditions *inside each home*, and
turns that into a prioritized, escalating, auditable sequence of human contact.
That is the whole product.

### Why Baltimore is the right pilot geography

Baltimore's rowhouse stock traps heat and stays hot into the night. Land surface
temperature is measurably higher in block groups with lower income, higher
poverty, and more elderly residents. And there is a documented, telling detail:
many residents will not put a window AC unit on the first floor for fear of
break-ins. That is not a problem a thermostat solves — it is exactly the kind of
thing a human check-in surfaces. The Baltimore Office of Sustainability already
operates urban heat island sensors, and there is existing community-perspectives
research on heat and health in the city to build on.

---

## 2. What we are building

A **dispatch layer for community wellness checks**, driven by hazard data and by
an estimate of indoor conditions.

Given a hazard (a heat event or an outage) and a roster of at-risk households,
the system decides *who to contact, in what order, and by whom*, escalates when
contact fails, and records every step. A coordinator at a partner organization —
an Area Agency on Aging, a Meals on Wheels chapter, a CERT team — runs it. A
volunteer receives a single, simple dispatch. Nobody looks at a map and guesses.

### The central design insight

Real-time outage data is not publicly available. EAGLE-I's historical archive is
downloadable, but its live platform is government-operational. Utility outage
maps can be scraped per-utility, which is fragile and doesn't tell you about a
specific house anyway.

So the primary outage signal is not a data feed. It is **silence**.

A ~$15 in-home kit reports indoor temperature on a heartbeat. When the heartbeat
stops, that house has probably lost power. One device therefore covers both
hazards: its readings drive heat triggers, and its silence drives outage
triggers. Utility and EAGLE-I data become *corroboration and context* — "is this
one house or the whole block?" — rather than the thing the system depends on.

Silence is ambiguous (power out, internet out, device unplugged, device broken),
so it is resolved by **correlation**: several devices in the same small area
going quiet within the same window is an outage; one device going quiet alone is
a device fault, and gets a low-priority "check your sensor" task instead of a
2 a.m. escalation. That correlation detector is a real algorithm with real
failure modes, and it is one of the more interesting things to build and test
here.

### The second design insight

Hardware does not scale for a solo student in one semester. Who installs 400
devices? Many low-income elderly households also lack the home WiFi the kit
needs. So the sensor tier cannot be the product.

Instead: **a small sensor deployment is used to validate a model that covers
everyone else.** Deploy 5–10 kits, collect ground-truth indoor temperature, and
fit a model that predicts indoor temperature trajectory from outdoor conditions
plus building characteristics (vintage, type, assessor data, tract-level social
vulnerability). Validate the model against the sensors you actually have. Then
apply it to an entire roster with no hardware at all.

This makes the project scientifically legible rather than merely an app: there is
a measurement, a model, a validation, and an error bar.

---

## 3. Approaches considered

**A. Software-only dispatch layer.** No hardware at all. Trigger purely on NWS
alerts and scraped utility outage data.
*Pros:* deployable immediately, no hardware logistics, simpler privacy story.
*Cons:* never observes the variable that actually kills, and the outage signal
rests entirely on fragile per-utility scraping. Automates the existing phone tree
without improving on its information.

**B. Sensor-first household telemetry.** Every enrolled home gets a kit.
*Pros:* precise, novel data; the strongest demo.
*Cons:* cannot reach a real pilot in a semester; installation labor is
unbounded; excludes households without WiFi, who are disproportionately the
households most at risk. Fails on equity grounds, which is disqualifying for a
project whose entire premise is reaching the people others miss.

**C. Hybrid, sensor-optional — CHOSEN.** The escalation engine is the product.
Sensors are one signal among several that sharpen a household's risk estimate.
Sensored homes get measured triggers; unsensored homes get modeled triggers with
explicit uncertainty.
*Pros:* works on day one with zero hardware, degrades gracefully, reaches the
households B excludes, and the sensor tier earns its place by validating the
model rather than by being the product.
*Cons:* two code paths through the trigger engine. Accepted — the estimator
interface hides the difference from everything downstream.

---

## 4. Architecture

Eight components. Each has one job, a defined interface, and can be tested
without the others.

```
  NWS API ─┐
 utility   ├──> [1] Hazard Ingest ──> HazardEvent ──┐
 EAGLE-I ──┘                                        │
                                                    v
 assessor/SVI ──> [3] Indoor Climate Estimator ──> [5] Trigger Engine ──> CheckInTask
                            ^                       ^                          │
 sensors ──> [4] Telemetry Ingest ──────────────────┘                          │
                    │  (correlated-silence detector)                           │
                    v                                                          v
              [2] Roster & Risk Model ─────────────────────────> [6] Escalation Engine
                                                                     │        │
                                                            [7] Dashboard  [8] Volunteer app
```

**[1] Hazard Ingest.** Pollers for the NWS API (free, no key, User-Agent header
required; active alerts and hourly forecast by lat/lon), one utility outage map,
and the EAGLE-I historical archive. Each normalizes its feed into a common
`HazardEvent`: geometry, kind, severity, time window, source, confidence. The
parsers are pure functions from a stored raw payload to events, so every one of
them is testable against fixtures with no network.

**[2] Roster & Risk Model.** Households, residents, equipment, contacts, and
consent records. Produces a `RiskScore` per household per hazard kind from: age,
AC presence and type, electricity-dependent equipment, medications that impair
thermoregulation (an underused and genuinely predictive signal —
anticholinergics, diuretics, beta blockers), social isolation, building vintage
and type, and tract social vulnerability. Scores are explainable: every score
carries the list of factors that produced it. A coordinator must be able to
answer "why is she at the top of my list?"

**[3] Indoor Climate Estimator.** The research core. Input: outdoor temperature
history and forecast, building attributes, and optionally live sensor readings.
Output: predicted indoor temperature trajectory with uncertainty. Two backends
behind one interface — `MeasuredEstimator` (a sensor is present) and
`ModeledEstimator` (no sensor). Everything downstream consumes the interface and
neither knows nor cares which backend answered. The overnight low matters more
than the daytime peak: rowhouses that never cool down are the documented killer.

**[4] Telemetry Ingest.** Endpoint for sensor heartbeats and readings. Holds the
correlated-silence outage detector: cluster recently-silent devices in space and
time; a cluster implies an outage, an isolated silence implies a device fault.
Emits a `HazardEvent` of kind `outage` with a confidence that reflects cluster
size, so a single dead sensor can never page a volunteer at 2 a.m.

**[5] Trigger Engine.** Consumes hazard events, risk scores, indoor estimates,
and last-contact times; emits `CheckInTask`s. Deliberately a small set of
explicit, readable, individually-tested rules — not a model. When this system
wakes someone up at night, a human has to be able to read the rule that did it
and agree with it.

**[6] Escalation Engine.** The state machine: automated SMS or voice call → wait
→ dispatch nearest available volunteer → wait → notify partner staff → recommend
welfare check. Every transition timestamped and logged. Must be idempotent and
resumable — the process will be restarted mid-heat-wave, and nobody may be
contacted twice or, far worse, dropped.

**[7] Coordinator Dashboard.** A map and a queue: who is unreached, sorted by
risk, with the reason for each ranking visible. This is the screen a partner
organization would actually keep open during an event.

**[8] Volunteer App.** Mobile web, intentionally minimal. "Check on Mrs. R, three
blocks away. Here is what to look for. Log what you found." Nothing else.

### Testing strategy: hazard replay

EAGLE-I publishes historical outage data from 2014 to 2025 at 15-minute county
resolution, and NWS alerts are archived. So the whole pipeline can be replayed
against a real historical event and asserted on: feed a recorded Baltimore heat
wave through ingest → estimator → triggers → escalation, and check which
households would have been flagged, in what order, and when.

This gives deterministic end-to-end tests with no live dependencies, and it
doubles as the demo. "Here is that heat wave. Here is who this system would have
reached, and when" is far more persuasive than a screenshot.

### Stack

Python 3.12 + FastAPI, Postgres with PostGIS, React + Vite + TypeScript,
ESPHome on ESP32 for the sensor tier, Twilio for SMS and voice. Modeling and
validation in Jupyter with numpy/matplotlib. Chosen to match existing fluency,
not novelty — the interesting parts of this project are the estimator, the
correlation detector, and the escalation semantics, and none of them get easier
with an unfamiliar stack.

### Hardware, for completeness

Per home: one ESP32 (~$6) running ESPHome as a passive BLE receiver, plus one or
more Xiaomi LYWSD03MMC sensors (~$5 each) reflashed with pvvx custom firmware,
which broadcasts temperature and humidity as unencrypted BLE advertisements
requiring no pairing or connection. The ESP32 listens, batches, and posts to the
telemetry endpoint on a heartbeat. Roughly $15 for a bedroom-and-living-room kit.
The reflash is done once, in bulk, before deployment.

---

## 5. Privacy and ethics

This system holds a list of isolated, medically fragile people, their addresses,
and their medical equipment. That is among the most sensitive datasets a student
project could plausibly assemble, and the design has to earn the right to hold
it.

- **Refuse surveillance capability by construction.** Temperature and a heartbeat.
  No cameras, no microphones, no motion sensing, no fall detection, no location
  tracking of residents. These are not "later" features; declining them is a
  design commitment, because a system that could watch people would be adopted
  for watching people.
- **Data stays with the partner organization.** Single-tenant per partner. No
  cross-organization analytics, no central roster.
- **Consent is a record, not a checkbox.** Who consented, to what, when, who
  witnessed it, and how to revoke — with revocation actually implemented and
  tested, including deletion of historical telemetry.
- **Volunteers see the minimum, briefly.** Name, address, what to look for, for
  the duration of the dispatch. Not the medical profile. Access expires with the
  task.
- **Stay outside HIPAA's perimeter deliberately.** This is community outreach by
  a non-covered entity, not healthcare. Do not ingest clinical records; medication
  categories are self-reported at enrollment as risk factors, not a medical
  record. Stated here explicitly so a future contributor does not casually cross
  the line.
- **Failure is the expected case.** This system will sometimes not reach someone
  in time. It must never present itself to a resident, a family, or a partner as
  a guarantee of safety, and the UI copy has to be written with that in mind.

---

## 6. Scope for one semester

**In:**
- Hazard ingest: NWS + one utility (BGE) + EAGLE-I historical replay
- Roster, consent records, explainable risk scoring
- Indoor climate estimator, both backends, validated against real sensors
- Correlated-silence outage detector
- Trigger engine and escalation state machine
- Coordinator dashboard and volunteer mobile web app
- SMS and voice via Twilio
- 5–10 sensor kits deployed for ground truth
- Historical replay harness as the primary end-to-end test

**Explicitly out:**
- Multi-tenancy and org self-service onboarding
- Native mobile apps
- Real-time streaming infrastructure
- Any ML over outcomes (there will not be enough outcomes, and the failure mode
  of a wrong model here is a person nobody visits)
- Integration with HMIS, EHRs, or case management systems
- Anything requiring HIPAA compliance
- Nationwide coverage. One city, done properly.

---

## 7. Open questions for review

1. **Partner organization.** The design assumes a real coordinator. Is the plan
   to actually approach a Baltimore partner (Meals on Wheels of Central Maryland,
   a CERT team, the city Health Department's aging division), or to build against
   a realistic synthetic roster and treat partnership as future work? This
   changes how much of section 5 must be *implemented* versus *specified*.
2. **Sensor deployment.** Are there 5–10 real homes available, or should ground
   truth come from instrumenting a few volunteered homes of any kind — a friend's
   apartment, a relative's rowhouse — accepting that the validation set is not
   representative?
3. **Which hazard leads.** Heat is the better first build: the trigger is
   forecastable days ahead, the season is testable, and the estimator is the
   interesting part. Outage then reuses the same engine. Confirm that ordering.
4. **Name.** "Porchlight" is provisional — it evokes leaving a light on for a
   neighbor, which is the right register. There is an existing Porchlight
   Community Action nonprofit in Madison, WI and a Porchlight book company, so it
   is not clean. Alternatives: Lantern, Hearth, Keepwell, Nightwatch.

---

## 8. Success criteria

- Replaying a real historical Baltimore heat event produces a ranked check-in
  queue, and the ranking can be justified household by household.
- The indoor climate estimator's error against sensor ground truth is measured
  and reported, not assumed. A stated error bar is the deliverable, whatever its
  magnitude.
- The correlated-silence detector distinguishes a simulated block outage from a
  simulated single device failure, with the decision boundary characterized.
- The escalation engine, killed and restarted mid-event, contacts nobody twice
  and drops nobody.
- A person unfamiliar with the project can read the dashboard and correctly say
  who needs help first and why.

---

## Appendix: sources for the claims in section 1

| Claim | Source |
|---|---|
| Heat deadliest US weather hazard; ~2,000/yr; 2,394 in 2024; doubled since 1999 | CDC / ABC News summary; PLOS Climate, "Rise in heat related mortality in the United States" |
| Lack of home AC strongest risk factor; deaths in un-air-conditioned homes | Healthbeat, June 2025 |
| 2.5M+ Medicare beneficiaries on electricity-dependent equipment | HHS emPOWER Program fact sheet |
| Device batteries last 3–8 hours; >half of counties had an 8h+ outage 2018–21; outages +9% frequency, +56% duration 2014–23 | The Conversation / UT Center for Transportation Research |
| 25% rise in disease-related deaths, 2003 NYC blackout | Same |
| PG&E failed to notify 23,000 customers | AARP, utility alerts for medically vulnerable |
| Philadelphia block captains, NYC buddy system, San Jose Buddies | Grist / UC Berkeley Journalism |
| Baltimore rowhouses trap heat; LST higher in poorer, older, minority block groups; AC not installed on first floor due to break-in fear | CNS Maryland "Code Red"; PubMed 21371807 |
| NWS API free, no key, alerts + forecast by lat/lon | weather.gov/documentation/services-web-api |
| emPOWER REST service, de-identified, to ZIP, monthly | empowerprogram.hhs.gov/empower-rest.html |
| EAGLE-I county-level 15-min, 2014–2025, via Globus; live platform not public | ORNL Constellation / OSTI DOE Data Explorer |
| LYWSD03MMC ~$5, pvvx firmware broadcasts unencrypted BLE advertisements | github.com/pvvx/ATC_MiThermometer; ESPHome docs |
