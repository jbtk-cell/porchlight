# Porchlight: A Walkthrough

**What it is, why it exists, what's actually built, and how hard it would be to deploy**

Prepared 26 July 2026 · Status: design complete, no code written yet

---

## The one-paragraph version

Every summer, people die alone in hot houses. Every storm season, people die when
the power goes out and their oxygen concentrator stops. In both cases the failure
is identical and mundane: **nobody knew that particular house had become
dangerous.** Porchlight is the software that closes that gap — it takes a hazard
(a heat wave, an outage), a roster of at-risk neighbors, and an estimate of what
it's actually like *inside each home*, and turns that into a prioritized,
escalating, logged sequence of human contact. A coordinator at a Meals on Wheels
chapter or an Area Agency on Aging runs it. A volunteer gets one simple dispatch:
go check on Mrs. R, three blocks away, here's what to look for. Nobody stares at a
map and guesses who to call first.

---

## Part 1: Why this needs to exist

### Heat is the deadliest weather hazard in the United States

Not hurricanes, not floods, not tornadoes. Heat kills roughly 2,000 Americans a
year — 2,394 in 2024 — and the annual toll has nearly doubled since 1999.

The part that matters for design: **the single strongest risk factor is lack of
home air conditioning**, and among people who die of heat stress, the place of
death is most often an un-air-conditioned home. They aren't dying at outdoor
concerts. They're dying in their bedrooms.

Now notice the mismatch. Heat advisories are issued regionally and describe
*outdoor* conditions. People die of *indoor* conditions. A 95°F day is a nuisance
in one house and lethal in the house next door, and nobody measures the
difference. That mismatch is the entire opening.

### Power outages are a slow-motion medical emergency

Over 2.5 million Medicare beneficiaries depend on electricity to stay alive at
home — oxygen concentrators, ventilators, home dialysis, powered mobility.
Their equipment's backup batteries last **3 to 8 hours**.

Against that: residents of more than half of all US counties experienced at least
one outage longer than 8 hours between 2018 and 2021. Outages became 9% more
frequent and **56% longer** between 2014 and 2023. The 2003 New York blackout
produced a 25% rise in disease-related deaths over just three days.

### Why existing tools don't close it

**HHS emPOWER** is a genuinely good federal dataset — de-identified counts of
electricity-dependent Medicare beneficiaries down to the ZIP code, with live
hazard overlays. But it is *a map an emergency manager looks at*. It is not an
alerting system, and by design it knows nothing about any individual person.

**Utility "medical baseline" registries** exist, but they're opt-in, siloed per
utility, and they're the systems that already failed. PG&E once admitted it
failed to notify 23,000 customers ahead of a planned shutoff. Some people found
out when their equipment stopped.

**On the heat side, cities already know the right answer.** Philadelphia runs
volunteer block captains. New York runs a buddy system. San Jose has a Buddies
Program. These work. And they run on spreadsheets, binders, and phone calls.

So the gap isn't "someone should check on people." Everyone knows that. The gap
is that **there is no software that decides who to check on, in what order, and
escalates when nobody answers.** That's the product.

### Why Baltimore is the right place to build it

Baltimore's rowhouse stock traps heat and stays hot deep into the night — the
overnight low, not the afternoon peak, is what kills. Land surface temperature is
measurably higher in block groups with lower income, higher poverty, and more
elderly residents.

And there's one documented detail that convinced me this problem needs humans and
not thermostats: **many residents won't put a window AC unit on the first floor
because they're afraid of break-ins.** No sensor solves that. No app solves that.
A person knocking on the door surfaces it, and then someone can do something
about it. That detail is why the system dispatches humans instead of just sending
alerts.

---

## Part 2: The core idea

Two decisions carry the entire design. Both came out of feasibility research, and
both are the kind of constraint that turns out to be a gift.

### Insight 1: The outage signal is silence

I went looking for a live power outage feed and found that **there isn't a public
one.** The Department of Energy runs EAGLE-I, which covers 92% of US electricity
customers at 15-minute resolution — but the real-time platform is
government-operational. Only the historical archive (2014–2025) is downloadable.
Utility outage maps can be scraped one at a time, which is fragile, and even then
they'd tell you about a *county*, not about Mrs. R's house.

So the primary outage signal isn't a data feed at all. It's **silence.**

A ~$15 device in the home reports indoor temperature on a heartbeat every minute.
When the heartbeat stops, that house has probably lost power. This is a
dead-man's switch, and it's better than any feed because it's about *that
specific house*.

The elegant part: **one device covers both hazards.** Its readings drive the heat
triggers. Its silence drives the outage triggers. You don't build two systems.

Now, silence is ambiguous — power out, internet out, device unplugged by a
grandchild, battery dead. So it gets resolved by **correlation.** If six devices
across three blocks all go quiet within the same two minutes, that's an outage.
If one device goes quiet while its neighbors keep chirping, that's a broken
sensor, and it generates a low-priority "check your device" ticket instead of
waking a volunteer at 2 a.m. Getting that boundary right — how many devices, how
close, how fast — is a real algorithm with real failure modes, and it's one of
the more genuinely interesting things to build here.

### Insight 2: A few sensors validate a model that covers everyone

The obvious version of this project puts a sensor in every home. That version
fails, for two reasons.

First, logistics: who installs 400 devices? Not one student in one semester.

Second, and much more important — **the kit needs household WiFi, and the
households least likely to have reliable home internet are disproportionately the
households most at risk.** A design that only protects the connected would
systematically exclude exactly the people the whole project exists for. That's
disqualifying.

So the sensors aren't the product. Here's what they're for instead:

> Deploy 5–10 kits. Collect real ground-truth indoor temperature. Use it to
> **fit and validate a model** that predicts indoor temperature from outdoor
> conditions plus building characteristics — vintage, construction type,
> assessor data, tract-level social vulnerability. Then apply that model to an
> entire roster of hundreds of homes **with no hardware at all.**

The sensored homes teach you what a 1920s Baltimore rowhouse does at 3 a.m. in
August. Then you can reason about every other 1920s rowhouse in the city.

This is also what makes the project scientifically legible rather than "an app."
There's a measurement, a model, a validation, and an honest error bar. That's a
result you can write up, not just a demo you can click through.

---

## Part 3: What actually exists right now

**Let me be completely direct: no code has been written. Not a line.**

What exists is research and a design. Specifically:

| Artifact | Status | Where |
|---|---|---|
| Problem research, 4 rounds, ~23 sources | Done | Summarized in this doc |
| Competitive check (what's already solved) | Done | Part 6 below |
| Data source feasibility verification | Done | All four confirmed accessible |
| Full design spec, 2,815 words | Written and committed | `~/porchlight`, branch `worktree-porchlight-spec`, at `docs/superpowers/specs/2026-07-26-porchlight-design.md` |
| Git repository | Initialized, 2 commits, clean | `~/porchlight` — local only, no GitHub remote yet |
| Implementation plan | **Not started** — deliberately | Blocked on four open decisions, see Part 7 |
| Any application code | **None** | — |
| Any hardware purchased | **None** | — |

I stopped before the implementation plan on purpose. Four open questions in the
spec would materially change what gets built, and writing 2,000 lines against the
wrong assumptions is a bad trade.

### What I did verify, so the design isn't resting on hope

These four checks are the reason I'm confident this is buildable rather than
merely plausible:

- **National Weather Service API** (`api.weather.gov`) — free, **no API key**,
  just needs a User-Agent header. Serves active alerts (including heat
  advisories and excessive heat warnings) and hourly forecasts by lat/lon.
  Confirmed.
- **HHS emPOWER** — has an actual REST service serving de-identified
  electricity-dependent Medicare counts down to ZIP level, updated monthly.
  Directly consumable into a GIS or a database. Confirmed.
- **EAGLE-I** — county-level outage counts at 15-minute intervals, 2014 through
  2025, downloadable from Oak Ridge via Globus. Live platform not public.
  Confirmed, and this constraint is what produced Insight 1.
- **The sensors** — the $5 Xiaomi thermometer's custom firmware broadcasts
  temperature and humidity as *unencrypted BLE advertisements*, requiring no
  pairing and no connection. Any ESP32 in the house can just listen. Confirmed.

---

## Part 4: What the system looks like once built

Eight components. Each does one job and can be tested without the others.

**1. Hazard Ingest** — Pollers for the NWS API, one utility outage map, and the
EAGLE-I archive. Each converts its feed into one common shape: a hazard with
geometry, kind, severity, a time window, and a confidence. The parsers are pure
functions from a saved payload to events, so all of them can be tested against
fixtures with no network.

**2. Roster & Risk Model** — Households, residents, equipment, contacts, and
consent records. Produces a risk score per household per hazard type from age, AC
presence, electricity-dependent equipment, social isolation, building vintage,
tract social vulnerability, and one underused signal: **medications that impair
the body's ability to regulate temperature** (anticholinergics, diuretics, beta
blockers). Every score carries the list of factors that produced it, because a
coordinator must be able to answer "why is she at the top of my list?"

**3. Indoor Climate Estimator** — The research core. Takes outdoor conditions,
building attributes, and optionally live sensor data; returns a predicted indoor
temperature trajectory with uncertainty. Two interchangeable backends behind one
interface: *measured* (there's a sensor) and *modeled* (there isn't). Everything
downstream consumes the interface and doesn't know or care which answered. The
model weights the overnight low heavily, because a rowhouse that never cools down
is the documented killer.

**4. Telemetry Ingest** — Receives heartbeats and readings, and houses the
correlated-silence detector described above.

**5. Trigger Engine** — Combines hazards, risk scores, indoor estimates, and
last-contact times into check-in tasks. Deliberately a small set of explicit,
readable rules — **not** a machine learning model. When this system wakes someone
up at 2 a.m., a human has to be able to read the rule that did it and agree with
it.

**6. Escalation Engine** — The state machine: automated SMS or voice call → wait
→ dispatch nearest available volunteer → wait → notify staff → recommend a
welfare check. Every transition timestamped. It has to be idempotent and
resumable, because the process *will* get restarted in the middle of a heat wave,
and when it comes back nobody may be contacted twice and — far worse — nobody may
be silently dropped.

**7. Coordinator Dashboard** — A map and a queue. Who's unreached, sorted by risk,
with the reason for each ranking visible. This is the screen a partner
organization keeps open during an event.

**8. Volunteer App** — Mobile web, deliberately minimal. "Check on Mrs. R, three
blocks away. Here's what to look for. Log what you found." Nothing else.

**Stack:** Python 3.12 + FastAPI, Postgres with PostGIS, React + Vite +
TypeScript, ESPHome on ESP32, Twilio for SMS and voice, Jupyter for the modeling
work. Chosen to match what I already know — the hard parts here are the
estimator, the correlation detector, and the escalation semantics, and none of
those get easier in an unfamiliar stack.

### How you'd know it actually works

Because EAGLE-I publishes years of historical outage data and NWS alerts are
archived, **you can replay a real past disaster through the entire pipeline** and
assert on what the system would have done. Feed in a recorded Baltimore heat wave;
check which households get flagged, in what order, at what hour.

That gives deterministic end-to-end tests with zero live dependencies. It's also
the demo — "here is that heat wave, and here is who this system would have reached
and when" lands considerably harder than a screenshot of a dashboard.

---

## Part 5: Use cases

### Scenario A — The heat wave (the primary case)

It's Thursday. NWS issues an excessive heat warning for Baltimore City starting
Saturday, with overnight lows not dropping below 79°F.

*Thursday:* The system ingests the alert. The indoor climate estimator projects
each rostered household forward: this 1918 rowhouse with no central air and a
west-facing bedroom is on track to sit above 88°F at 4 a.m. Sunday. Risk scores
recompute. The coordinator opens the dashboard Friday morning and sees a ranked
list — not "here are 340 seniors," but "here are the 22 households that need
contact before Saturday, and here's why each one is on the list."

*Saturday:* Automated check-in texts go out. Most people reply. Eleven don't.

*Saturday evening:* Those eleven escalate to volunteers, sorted by proximity.
A volunteer gets one card: Mrs. R, 3 blocks, no AC on record, lives alone, has a
window unit in the back bedroom only. She's fine, but she mentions the fan died
Tuesday. That gets logged, and it's a fixable problem someone can now fix.

*Sunday, 2 a.m.:* One sensored home reports 91°F indoors and no response to two
contact attempts. That escalates past volunteers straight to staff.

### Scenario B — The outage

A line goes down at 9 p.m. Within ninety seconds, fourteen devices across a
six-block area go silent simultaneously. The correlation detector sees a cluster,
not a fault, and raises a high-confidence outage hazard.

The system already knows which of those homes have someone on an oxygen
concentrator, and it knows the battery is good for 3 to 8 hours. Those households
go to the top of the queue immediately with a countdown attached. Everyone else
gets a lower-priority check. The utility outage map, scraped separately, confirms
the extent and gives an estimated restoration time — which turns "someone should
check on her" into "her concentrator has about four hours and restoration is
estimated at six, so she needs to physically move tonight."

### Scenario C — The one that isn't an emergency

A single device goes quiet at 11 p.m. Its neighbors keep reporting normally. The
detector correctly calls this a device fault, not an outage. It creates a
low-priority ticket for the next business day. **Nobody gets woken up.**

This scenario is in here on purpose. A system that cries wolf gets ignored during
the event that matters, and the failure mode of over-alerting is that volunteers
stop answering. Being reliably quiet is a feature.

---

## Part 6: What I deliberately am *not* building

Part of the research was checking what already exists, so effort doesn't go
somewhere it isn't needed. Four ideas got killed outright:

- **Food rescue** — Food Rescue Hero already has 12,000 volunteer drivers and
  70,000+ completed rescues.
- **Sidewalk accessibility mapping** — Project Sidewalk (UW/UMD) has 932,000
  geo-located labels.
- **Disaster cleanup coordination** — Crisis Cleanup is mature and widely used.
- **Hospital charity care screening** — Dollar For already runs a proprietary
  hospital-policy database and has driven $60M+ in debt relief.

Better to know that now than three weeks in.

---

## Part 7: The hardware — and how hard this actually is

This is the question you asked most directly, so here's the honest answer:
**the technical side is much easier than you'd expect, and the hard part isn't
technical at all.**

### What's in a kit

| Part | Cost | Purpose |
|---|---|---|
| Xiaomi LYWSD03MMC thermometer | ~$5 | Measures bedroom temperature and humidity |
| Second thermometer (optional) | ~$5 | Living room |
| ESP32 dev board | ~$6 | Listens for the thermometers, posts to the server, provides the heartbeat |
| **Total** | **~$11–16 per home** | |

Ten homes is roughly **$150.** That's a real number for a student project.

### Why the technical difficulty is low

**Flashing the thermometers: no soldering, no cables, no programmer.** The pvvx
custom firmware is installed through a **web page in Chrome over Bluetooth** —
you open `pvvx.github.io/ATC_MiThermometer/TelinkMiFlasher.html`, it connects to
the thermometer wirelessly, and you click flash. That's it. I expected this to
require a USB-to-serial adapter and soldering to test pads; it doesn't. You can do
ten of them at a kitchen table in under an hour.

**The thermometers then just broadcast, unencrypted.** With the custom firmware
they push temperature and humidity out as plain BLE advertisements. No pairing, no
connection, no bindkey. Anything nearby that's listening can read them. This is
what makes one ESP32 able to serve a whole house.

**Flashing the ESP32 is also browser-based.** ESPHome's web installer at
`web.esphome.io` flashes over WebSerial from Chrome or Edge, and the Improv WiFi
standard lets you hand it network credentials right in the same flow.

**Battery life is a non-issue.** A CR2032 coin cell runs the thermometer for
**over a year** at default settings (14–15 µA draw). Some variants with a CR2450
go past three years. You are not visiting these homes every month to change
batteries.

So: total per-home install is roughly *plug in the ESP32, place two thermometers,
join the WiFi.* Fifteen minutes, no tools.

### Where it's actually hard

The genuine difficulties are social and logistical, and they're worth stating
plainly because they're what would sink a deployment:

1. **Household WiFi.** The kit needs the home's internet. Many of the households
   most at risk don't have reliable home broadband. **This is the single biggest
   constraint**, and it's precisely why the architecture treats sensors as
   optional and leans on the modeled estimator for everyone else. If you ever
   wanted full coverage you'd need cellular or LoRa, which changes the cost
   structure substantially — that's a future-work problem, not a semester
   problem.

2. **Getting into someone's home at all.** Asking an isolated, elderly person to
   let a stranger install an internet-connected device in their bedroom is a
   *trust* problem, not an engineering problem. It only works through an
   organization they already know — which is exactly why the design assumes a
   partner like Meals on Wheels rather than direct-to-resident signup. Their
   driver already comes to the door every week.

3. **The privacy burden is real and it's on you.** This system holds a list of
   isolated, medically fragile people with their addresses and their medical
   equipment. That's about the most sensitive dataset a student project could
   assemble. The spec commits to refusing surveillance capability *by
   construction*: temperature and a heartbeat, and **no cameras, no microphones,
   no motion sensing, no fall detection, no location tracking.** Not "later" —
   never, because a system that *could* watch people will eventually be adopted
   for watching people.

4. **Maintenance and drift.** Devices get unplugged. People move. Routers get
   replaced. Some fraction of your fleet is always broken, which is another
   reason the correlated-silence logic has to be good — a system that treats
   every dead device as an emergency becomes noise.

### Effort estimate for the software

Rough, assuming this is your main project for a semester:

| Component | Estimate |
|---|---|
| Hazard ingest (NWS easy, utility scraper fiddly) | ~1 week |
| Roster, consent records, risk scoring | ~1 week |
| **Indoor climate estimator + validation** | **3–4 weeks** — the research core, and the most uncertain |
| Telemetry ingest + correlated-silence detector | ~2 weeks |
| Trigger engine | ~1 week |
| Escalation state machine | ~2 weeks — idempotency and resumability is where the bugs live |
| Coordinator dashboard | ~2 weeks |
| Volunteer app | ~3 days |
| Historical replay test harness | ~1 week |
| Hardware: order, flash, deploy | ~1 week elapsed, mostly waiting on shipping |

That totals roughly 14–15 weeks, which is a full semester with no slack. **It's
ambitious.** If time compresses, the cut order is: drop the volunteer web app and
dispatch volunteers by plain SMS; drop the utility scraper and run on NWS plus
sensors alone; simplify the dashboard to a ranked table with no map. The
estimator, the correlation detector, and the escalation engine are the parts worth
protecting — they're where both the difficulty and the originality live.

---

## Part 8: What I need from you before writing code

Four decisions, from the spec:

1. **Partner organization — real or synthetic?** Are we actually approaching a
   Baltimore partner (Meals on Wheels of Central Maryland, a CERT team, the city
   Health Department's aging division), or building against a realistic synthetic
   roster with partnership as future work? This changes how much of the privacy
   design must be *implemented* versus *specified*.

2. **Sensor homes.** Are there 5–10 real households available, or should ground
   truth come from whatever homes you can get — a friend's apartment, a
   relative's rowhouse — accepting that the validation set isn't representative?

3. **Which hazard leads?** My recommendation is heat first: it's forecastable days
   ahead, the season is testable, and the estimator is the interesting part.
   Outage then reuses the same engine. Confirm.

4. **The name.** "Porchlight" is provisional. It evokes leaving a light on for a
   neighbor, which is the right register — but there's an existing Porchlight
   Community Action nonprofit in Madison, WI and a Porchlight book company.
   Alternatives: Lantern, Hearth, Keepwell, Nightwatch.

---

## Sources

Problem statistics: CDC and PLOS Climate on US heat mortality; Healthbeat (June
2025) on air conditioning as the dominant risk factor; HHS emPOWER Program fact
sheet for the 2.5M figure; The Conversation and UT Center for Transportation
Research on device battery life, outage frequency and duration, and the 2003 NYC
blackout mortality finding; AARP on the PG&E notification failure; Grist and UC
Berkeley Journalism on the Philadelphia, New York, and San Jose volunteer
programs; CNS Maryland "Code Red" and PubMed 21371807 on Baltimore rowhouse heat
and the first-floor AC finding.

Technical feasibility: `weather.gov/documentation/services-web-api`;
`empowerprogram.hhs.gov/empower-rest.html`; ORNL Constellation and OSTI DOE Data
Explorer for EAGLE-I; `github.com/pvvx/ATC_MiThermometer` and ESPHome
documentation for the sensor tier; `esphome.github.io/esp-web-tools` for
browser-based ESP32 flashing.
