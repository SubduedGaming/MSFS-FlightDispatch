# Careers: companies, qualifications, messenger and copilot

## The job board
Open **Job Board** to see the companies you can apply to. Each one checks four things about you:

| Qualification | What it is |
|---|---|
| **Total flight time** | Every hour in your logbook. |
| **Recent experience** | Hours flown recently, *weighted by how long ago*. Each flight's hours count in full on the day you fly and **halve every 45 days** (configurable in Settings > Gameplay). Nothing is stored: it is recomputed from your logbook, so it shrinks a little every day you don't fly and recovers when you do. |
| **Skill level** | A rolling rating (0-100) that moves towards the score of each flight: smooth landings and clean flying raise it, hard landings, overspeeds and crashes lower it. Novice < 40 <= Developing < 55 <= Competent < 70 <= Skilled < 85 <= Expert. |
| **Time on the company's aircraft** | Hours in an aircraft class (piston, twin, turboprop, jet, airliner) from your logbook, from any flight you flew in it, including company flights. |

Each company also expects the **ratings** its aircraft need (see *Training, licences and money* below).

### Companies recruit in bursts, not around the clock
A company only takes applications while it has a **vacancy open**. Vacancies appear at random and stay open for three to
eight days; someone else may fill one early. Small operators recruit often, airlines rarely. The Job Board shows who is
recruiting now and until when, and your operations desk tells you when a company you actually qualify for opens one.

The decision is immediate and honest: if you fall short the reply says exactly what is missing, but **a rejection
locks you out of that company for two weeks**, so check the requirements first. Getting hired fills the vacancy.

### The ladder
| # | Company | Flies | Pay | Total time | Recent exp. | Skill | Time on |
|---|---|---|---|---|---|---|---|
| 1 | **Bluebird Bush Air** | Cessna 152, Cessna 172 Skyhawk | x0.85 | - | - | - | - |
| 2 | **Harbour Light Courier** | Cessna 172 Skyhawk, Robin DR400 | x0.95 | 10 h | - | 40 | - |
| 3 | **Skyline Air Taxi** | Diamond DA40 NG, Cirrus SR22 | x1.05 | 50 h | 3 h | 50 | 30 h piston aircraft |
| 3 | **Alpine Scenic Flights** | Cessna 172 Skyhawk, Diamond DA40 NG, Beechcraft Bonanza G36 | x1.00 | 100 h | 4 h | 55 | 60 h piston aircraft |
| 4 | **Coastline Air Ambulance** | Beechcraft Baron G58, Diamond DA62 | x1.25 | 200 h | 6 h | 60 | 120 h piston aircraft |
| 5 | **Northwind Regional Freight** | Cessna 208B Grand Caravan EX | x1.15 | 300 h | 8 h | 60 | 25 h twin aircraft |
| 6 | **Summit Executive Aviation** | Pilatus PC-12 NGX, Daher TBM 930, Beechcraft King Air 350i | x1.35 | 500 h | 10 h | 65 | 50 h turboprop aircraft |
| 7 | **Apex Jet Charter** | Cessna Citation CJ4 | x1.50 | 900 h | 12 h | 70 | 120 h turboprop aircraft |
| 8 | **Meridian Airways** | Airbus A320neo, Boeing 737-800 | x1.45 | 1500 h | 15 h | 72 | 100 h jet aircraft |
| 9 | **Atlas Global Cargo & Long-Haul** | Boeing 747-8 Intercontinental, Boeing 787-10 Dreamliner | x1.70 | 3500 h | 20 h | 78 | 400 h airliner aircraft |

Companies supply the aircraft (you do not need to own them) but **the aircraft must be installed in your sim** (see below).
A company pays you an **hourly rate** for the block time you fly, rising with the company's tier (about $136 an hour at
Bluebird, about $1,250 at Atlas, before the flight score adjusts it). The company covers fuel and running costs; wear is
the company's problem. Your own life, licences and training are not.

### Starting experience
The setup wizard lets you start as:
- **New to flying (0 h)**: skill 35
- **Student pilot (~40 h)**: skill 45
- **Private pilot (~150 h)**: skill 55
- **Commercial pilot (~800 h)**: skill 65
- **Airline transport pilot (~3,000 h)**: skill 75

Pick *New to flying* to climb the whole ladder, or start higher if you are already a pilot.

## Messenger
Every company that hired you has a conversation thread, plus your own **Operations** desk. In a company thread:

1. The dispatcher greets you and asks **how long you have free** (quick-answer chips: 30 min, 1 h, 2 h, 3 h, 4+ h, or just type or say "an hour and a half").
2. They **assign you one flight** that fits that time from where you are now, shown as a card. **You do not choose it and you
   cannot decline it**: you fly it, or abandon it, which costs more reputation than walking away from a freelance job.
3. You cannot be rostered while your licence or medical has expired, or while you already have a flight assigned.
4. After each flight the dispatcher debriefs you and asks how long you have for the next one.

Each company has its own dispatcher with a name and a voice, and each of its flights has a **flight number** (for example
BBA214: airline code BBA, flight 214). The same route always has the same number and the opposite direction gets the next
one. SimBrief's dispatch page is prefilled with the airline code and flight number.

The dispatcher is an AI (LM Studio) but the flights are built by the app from rules, so numbers are always correct and it all works with the AI offline.

## Freelance contracts
The **Freelance** board is for **owner-operators**: pilots who own an aircraft. You choose the work, fly your own aircraft and
pay for the fuel and upkeep. Without an aircraft the page tells you to buy one or get hired.

## Training, licences and money
Being employed does not make you free. The **Training** page covers:

| What | Detail |
|---|---|
| **Pilot licence** | Valid a year. Renew it in its last 30 days ($700). |
| **Medical** | Valid 90 days. Renew it in its last 30 days ($350). |
| **Expired** | You cannot take contracts or be rostered until it is renewed. You are warned 14 days ahead. |
| **Ratings** | Earned once by paying for a course; ground school takes real days. Instrument (40 h, $9,000), multi-engine (60 h, $6,500), turboprop (150 h, $13,000, needs instrument), jet (400 h, $24,000, needs instrument) and airline (900 h, $38,000, needs jet). |
| **Who needs what** | A company wants a rating for each kind of aircraft it flies, plus the instrument rating from tier 3. Your own aircraft needs the same ratings. |
| **Monthly bills** | Every 30 real days: living costs by rank ($900 student to $4,200 chief pilot) plus hangar and insurance on each aircraft you own (0.6% of its price). Come back after a long break and at most two months are charged. |

Difficulty scales fees, bills and course length (relaxed is cheaper and quicker, realistic dearer and slower). Pilots from
earlier versions keep the ratings they already use, and a new pilot's starter aircraft and starting experience come with
the matching ratings.

## Copilot
On the **Flight** tab the copilot sits in the right seat. Ask by typing, with the hold-to-talk button, or with the push-to-talk key (the key talks to the copilot while a flight is running, otherwise to the dispatcher).

- **Quick buttons:** checklist for the current phase, fuel check (endurance vs. distance to go vs. a 45-minute reserve), descent plan (3:1 rule), approach brief (runway, speeds, pattern altitude), weather (live METAR) and status.
- **Free questions:** answered by the AI from your *live* telemetry. If the AI server is offline the copilot falls back to the same deterministic answers.
- **Proactive callouts** (can be switched off): "Positive rate", "Top of descent in N miles", "One thousand", "Five hundred, check speed and sink rate", "Sink rate!", approach-speed and fuel warnings. They are rule-based, instant and never repeat.
- Two personalities (Sam, Nina) with their own voices.

## Aircraft installed in your sim
Jobs, company flights and the dealer only use aircraft you actually have installed.

- **Windows with MSFS:** detected automatically from your packages folder (Community and Official, 2020 and 2024).
- **Mac/Linux connected to your Windows PC:** the Windows PC reports what is installed on the Windows PC when it connects.
- **Anything else:** Settings > Simulator > *Choose manually*, or point at a packages folder with *Browse*.
- If SkyDispatch cannot tell what is installed it does **not** restrict anything. Aircraft you have flown in the sim always count as installed.
- A company whose aircraft you do not have installed is shown as unavailable instead of offering flights you could not fly.
