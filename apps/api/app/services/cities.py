"""City packs — the demo, re-conditioned for four Indian metros.

A route optimizer that only ever runs on one city proves very little: the whole
argument of this project is that Indian urban freight is shaped by *local*
policy and *local* geography. Bengaluru's binding constraint is the CBD peak
restriction; Delhi's is the MCD goods-vehicle no-entry window and winter fog;
Mumbai's is the island-city heavy-vehicle ban and monsoon flooding at Hindmata
and the Andheri subway; Hyderabad's is the Old City around Charminar.

Each pack therefore carries its own depots, demand, fleet, curfews, congestion
profile, flood corridors and weather character. Swapping cities swaps all of
it at once.

Everything here is [Assumption] — coordinates are approximate neighbourhood
centroids and the restriction windows are modelled on publicly discussed
policy, not scraped from a gazette. They are plausible, not authoritative, and
production would take them from a municipal feed (Plan.md §12.4).
"""

from __future__ import annotations

from dataclasses import dataclass

# Shipment tuple:
# (code, customer, lat, lon, kg, m3, tw_start, tw_end, priority,
#  L, W, H cm, fragile, stackable, requires_feature)
Shipment = tuple

# Overlay tuple: (name, lat, lon, radius_km, ban_start, ban_end, notes)
Overlay = tuple


@dataclass
class City:
    id: str
    label: str
    region: str
    center: tuple[float, float]
    # Free-flow LCV speed before congestion. Mumbai's island roads are simply
    # slower than Delhi's arterials, and the plans should reflect that.
    free_flow_kmh: float
    # Speed multiplier by hour of day: 1.0 is free flow.
    traffic_by_hour: dict[int, float]
    depots: list[tuple[str, float, float]]
    vehicles: list[tuple[str, str, int, float, tuple[int, int, int], str]]
    shipments: list[Shipment]
    overlays: list[Overlay]
    # Where an ad-hoc order can land mid-shift.
    adhoc_spots: list[tuple[str, float, float]]
    # Corridors that close under heavy weather, with a crude radius.
    hazard_corridors: list[tuple[str, float, float, float]]
    # What "bad weather" means here, and when it is most likely.
    storm_label: str = "heavy rain"
    rain_label: str = "rain"
    storm_hours: tuple[int, int] = (14, 20)
    storm_bias: float = 0.45
    notes: str = ""


# Peak shapes differ by city: Delhi's freight peak is defined by the no-entry
# window rather than by commuter traffic, Mumbai crawls for most of the day.
BLR_TRAFFIC = {
    6: 1.15, 7: 0.80, 8: 0.55, 9: 0.50, 10: 0.62, 11: 0.78,
    12: 0.85, 13: 0.88, 14: 0.88, 15: 0.82, 16: 0.68, 17: 0.52,
    18: 0.45, 19: 0.48, 20: 0.62, 21: 0.85,
}

MUM_TRAFFIC = {
    6: 1.10, 7: 0.78, 8: 0.52, 9: 0.45, 10: 0.55, 11: 0.68,
    12: 0.75, 13: 0.78, 14: 0.78, 15: 0.72, 16: 0.60, 17: 0.46,
    18: 0.40, 19: 0.42, 20: 0.55, 21: 0.78,
}

DEL_TRAFFIC = {
    6: 1.20, 7: 0.85, 8: 0.62, 9: 0.58, 10: 0.66, 11: 0.82,
    12: 0.90, 13: 0.92, 14: 0.90, 15: 0.84, 16: 0.70, 17: 0.55,
    18: 0.48, 19: 0.52, 20: 0.68, 21: 0.90,
}

HYD_TRAFFIC = {
    6: 1.18, 7: 0.88, 8: 0.66, 9: 0.60, 10: 0.70, 11: 0.84,
    12: 0.90, 13: 0.92, 14: 0.92, 15: 0.86, 16: 0.74, 17: 0.60,
    18: 0.52, 19: 0.56, 20: 0.72, 21: 0.92,
}


BENGALURU = City(
    id="bengaluru",
    label="Bengaluru",
    region="Karnataka",
    center=(12.9716, 77.5946),
    free_flow_kmh=34.0,
    traffic_by_hour=BLR_TRAFFIC,
    notes="CBD peak-hour commercial vehicle restriction is the binding constraint.",
    storm_label="heavy rain",
    storm_hours=(14, 20),
    depots=[
        ("Peenya Hub", 13.0280, 77.5190),
        ("Whitefield Hub", 12.9850, 77.7360),
    ],
    vehicles=[
        ("TRK-01", "Peenya Hub", 800, 8, (300, 180, 180), ""),
        ("TRK-02", "Peenya Hub", 1000, 10, (430, 200, 200), ""),
        ("TRK-03", "Peenya Hub", 600, 6, (260, 165, 170), "reefer"),
        ("TRK-04", "Whitefield Hub", 900, 9, (400, 195, 195), ""),
        ("TRK-05", "Whitefield Hub", 700, 7, (300, 180, 180), "tail_lift"),
    ],
    shipments=[
        # Inside the CBD no-entry radius: these force the curfew constraint.
        ("SHP-01", "MG Road Retail", 12.9750, 77.6060, 60, 0.4, 8 * 60, 18 * 60, 2, 80, 60, 80, False, True, ""),
        ("SHP-02", "Shivajinagar Market", 12.9850, 77.6050, 90, 0.6, 8 * 60, 18 * 60, 1, 100, 70, 90, False, True, ""),
        ("SHP-03", "Cubbon Park Office", 12.9763, 77.5929, 40, 0.3, 9 * 60, 17 * 60, 1, 70, 50, 70, True, False, ""),
        ("SHP-04", "Koramangala", 12.9352, 77.6245, 120, 0.8, 8 * 60, 14 * 60, 1, 110, 80, 90, False, True, ""),
        ("SHP-05", "Indiranagar", 12.9784, 77.6408, 80, 0.5, 10 * 60, 18 * 60, 1, 90, 60, 90, False, True, ""),
        ("SHP-06", "Jayanagar", 12.9308, 77.5838, 90, 0.6, 8 * 60, 18 * 60, 1, 100, 70, 85, False, True, ""),
        ("SHP-07", "Malleshwaram", 13.0035, 77.5640, 70, 0.4, 8 * 60, 13 * 60, 1, 80, 60, 80, False, True, ""),
        ("SHP-08", "BTM Layout", 12.9166, 77.6101, 100, 0.7, 8 * 60, 18 * 60, 1, 100, 75, 90, False, True, ""),
        ("SHP-09", "HSR Layout", 12.9116, 77.6389, 85, 0.5, 12 * 60, 18 * 60, 1, 90, 65, 85, False, True, ""),
        # Long haul: what a greedy plan gets wrong.
        ("SHP-10", "Whitefield ITPL", 12.9698, 77.7500, 150, 1.0, 9 * 60, 17 * 60, 1, 120, 90, 95, False, True, ""),
        ("SHP-11", "Marathahalli", 12.9591, 77.6974, 110, 0.7, 8 * 60, 18 * 60, 1, 105, 75, 90, False, True, ""),
        ("SHP-12", "Electronic City", 12.8452, 77.6602, 140, 0.9, 8 * 60, 16 * 60, 1, 115, 85, 95, False, True, ""),
        ("SHP-13", "Yelahanka", 13.1007, 77.5963, 110, 0.7, 8 * 60, 18 * 60, 1, 100, 75, 90, False, True, ""),
        ("SHP-14", "Hebbal", 13.0358, 77.5970, 95, 0.6, 8 * 60, 18 * 60, 1, 95, 70, 85, False, True, ""),
        # Cold chain the solver must never drop.
        ("SHP-15", "Apollo Pharma (cold)", 12.9260, 77.5938, 45, 0.3, 8 * 60, 11 * 60, 3, 70, 55, 75, True, False, "reefer"),
    ],
    overlays=[
        ("Bengaluru CBD morning no-entry (template)", 12.9760, 77.6030, 3.0, 8 * 60, 11 * 60,
         "Assumption - verify against local traffic police notification. "
         "Peak-hour commercial vehicle restriction."),
        ("Bengaluru CBD evening no-entry (template)", 12.9760, 77.6030, 3.0, 17 * 60, 21 * 60,
         "Assumption - verify locally."),
    ],
    adhoc_spots=[
        ("Sarjapur Road", 12.9099, 77.6826),
        ("Banaswadi", 13.0140, 77.6510),
        ("Rajajinagar", 12.9910, 77.5550),
        ("JP Nagar", 12.9070, 77.5850),
        ("KR Puram", 13.0070, 77.6960),
        ("Bellandur", 12.9260, 77.6780),
        ("Peenya Industrial", 13.0290, 77.5220),
        ("Hoskote Road", 13.0680, 77.7900),
    ],
    hazard_corridors=[
        ("Silk Board junction", 12.9170, 77.6230, 2.5),
        ("KR Puram underpass", 13.0070, 77.6960, 2.0),
        ("Hebbal flyover approach", 13.0358, 77.5970, 2.5),
    ],
)


MUMBAI = City(
    id="mumbai",
    label="Mumbai",
    region="Maharashtra (MMR)",
    center=(19.0760, 72.8777),
    # An island city on two north-south corridors: slower than anywhere else.
    free_flow_kmh=28.0,
    traffic_by_hour=MUM_TRAFFIC,
    notes="Island-city heavy-vehicle restriction plus monsoon flooding at the "
          "known chronic spots. Warehousing sits outside the city, at Bhiwandi.",
    storm_label="monsoon downpour",
    rain_label="monsoon rain",
    storm_hours=(11, 21),
    storm_bias=0.65,
    depots=[
        ("Bhiwandi Logistics Park", 19.2813, 73.0483),
        ("Vashi APMC Yard", 19.0700, 73.0000),
    ],
    vehicles=[
        ("MH-01", "Bhiwandi Logistics Park", 1000, 10, (430, 200, 200), ""),
        ("MH-02", "Bhiwandi Logistics Park", 800, 8, (300, 180, 180), ""),
        ("MH-03", "Bhiwandi Logistics Park", 600, 6, (260, 165, 170), "reefer"),
        ("MH-04", "Vashi APMC Yard", 900, 9, (400, 195, 195), ""),
        ("MH-05", "Vashi APMC Yard", 700, 7, (300, 180, 180), "tail_lift"),
    ],
    shipments=[
        # Island city, inside the heavy-vehicle restriction: the only legal
        # delivery window is the gap between the morning and evening bans, and
        # a Bhiwandi round trip eats most of it.
        ("SHP-01", "Crawford Market", 18.9470, 72.8347, 95, 0.6, 11 * 60, 16 * 60 + 30, 1, 100, 70, 90, False, True, ""),
        ("SHP-02", "Fort / CST", 18.9400, 72.8350, 70, 0.4, 11 * 60, 16 * 60 + 30, 2, 85, 60, 80, False, True, ""),
        ("SHP-03", "Colaba Causeway", 18.9150, 72.8258, 55, 0.35, 11 * 60, 16 * 60 + 30, 1, 75, 55, 75, True, False, ""),
        ("SHP-04", "Lower Parel Mills", 18.9960, 72.8250, 130, 0.85, 11 * 60, 16 * 60, 1, 115, 85, 95, False, True, ""),
        ("SHP-05", "Dadar TT", 19.0180, 72.8440, 90, 0.6, 8 * 60, 18 * 60, 1, 100, 70, 88, False, True, ""),
        # Suburbs.
        ("SHP-06", "Bandra Kurla Complex", 19.0650, 72.8690, 110, 0.7, 9 * 60, 18 * 60, 1, 105, 75, 90, False, True, ""),
        ("SHP-07", "Andheri MIDC", 19.1180, 72.8690, 120, 0.8, 8 * 60, 16 * 60, 1, 110, 80, 92, False, True, ""),
        ("SHP-08", "Powai", 19.1180, 72.9050, 80, 0.5, 10 * 60, 18 * 60, 1, 90, 65, 85, False, True, ""),
        ("SHP-09", "Goregaon Film City Rd", 19.1650, 72.8500, 85, 0.55, 8 * 60, 18 * 60, 1, 95, 70, 85, False, True, ""),
        ("SHP-10", "Borivali", 19.2300, 72.8570, 100, 0.65, 8 * 60, 18 * 60, 1, 100, 72, 88, False, True, ""),
        # Mainland: the long legs.
        ("SHP-11", "Thane Wagle Estate", 19.1900, 72.9700, 140, 0.9, 8 * 60, 17 * 60, 1, 118, 88, 95, False, True, ""),
        ("SHP-12", "Turbhe MIDC", 19.0700, 73.0200, 150, 1.0, 8 * 60, 16 * 60, 1, 120, 90, 95, False, True, ""),
        ("SHP-13", "Chembur", 19.0520, 72.9000, 95, 0.6, 8 * 60, 18 * 60, 1, 98, 70, 88, False, True, ""),
        ("SHP-14", "Panvel", 18.9890, 73.1170, 130, 0.85, 9 * 60, 17 * 60, 1, 115, 85, 92, False, True, ""),
        ("SHP-15", "Parel Hospital (cold)", 19.0000, 72.8420, 45, 0.3, 8 * 60, 11 * 60, 3, 70, 55, 75, True, False, "reefer"),
    ],
    overlays=[
        ("Island city heavy-vehicle no-entry, morning (template)", 18.9400, 72.8330, 4.5,
         8 * 60, 11 * 60,
         "Assumption - modelled on the South Mumbai peak-hour goods vehicle "
         "restriction. Verify against the current MTP notification."),
        ("Island city heavy-vehicle no-entry, evening (template)", 18.9400, 72.8330, 4.5,
         17 * 60, 21 * 60,
         "Assumption - verify locally."),
    ],
    adhoc_spots=[
        ("Malad West", 19.1860, 72.8480),
        ("Ghatkopar", 19.0860, 72.9080),
        ("Kalyan", 19.2400, 73.1300),
        ("Mulund", 19.1720, 72.9560),
        ("Worli", 19.0170, 72.8180),
        ("Vikhroli", 19.1100, 72.9280),
        ("Airoli", 19.1550, 72.9990),
        ("Mira Road", 19.2810, 72.8710),
    ],
    hazard_corridors=[
        # The three names every Mumbai dispatcher watches in July.
        ("Hindmata, Dadar", 19.0130, 72.8390, 1.5),
        ("Andheri subway", 19.1190, 72.8430, 1.2),
        ("Sion circle", 19.0400, 72.8620, 1.5),
        ("Kurla LBS junction", 19.0700, 72.8790, 1.5),
    ],
)


DELHI = City(
    id="delhi",
    label="Delhi NCR",
    region="Delhi / Haryana / UP",
    center=(28.6139, 77.2090),
    free_flow_kmh=38.0,
    traffic_by_hour=DEL_TRAFFIC,
    notes="The goods-vehicle no-entry window is wide and strictly enforced; "
          "winter fog, not rain, is what destroys morning ETAs.",
    storm_label="dense fog",
    rain_label="drizzle and haze",
    # Fog is a morning problem, which is the opposite of Bengaluru's afternoon
    # thunderstorm — and it hits exactly when the no-entry window lifts.
    storm_hours=(6, 10),
    storm_bias=0.55,
    depots=[
        ("Narela Industrial Estate", 28.8420, 77.0900),
        ("Udyog Vihar, Gurugram", 28.5030, 77.0870),
    ],
    vehicles=[
        ("DL-01", "Narela Industrial Estate", 1000, 10, (430, 200, 200), ""),
        ("DL-02", "Narela Industrial Estate", 800, 8, (300, 180, 180), ""),
        ("DL-03", "Narela Industrial Estate", 600, 6, (260, 165, 170), "reefer"),
        ("DL-04", "Udyog Vihar, Gurugram", 900, 9, (400, 195, 195), ""),
        ("DL-05", "Udyog Vihar, Gurugram", 700, 7, (300, 180, 180), "tail_lift"),
    ],
    shipments=[
        # Inside the no-entry cordon. A Delhi dispatcher does not promise these
        # customers a morning slot — goods vehicles cannot legally reach them
        # until 11:00, so the windows sit in the gap between the two bans.
        ("SHP-01", "Chandni Chowk", 28.6560, 77.2300, 95, 0.6, 11 * 60, 16 * 60 + 30, 1, 100, 70, 90, False, True, ""),
        ("SHP-02", "Connaught Place", 28.6315, 77.2167, 70, 0.45, 11 * 60, 16 * 60 + 30, 2, 85, 60, 82, False, True, ""),
        ("SHP-03", "Karol Bagh", 28.6520, 77.1900, 85, 0.55, 11 * 60, 16 * 60 + 30, 1, 95, 68, 85, False, True, ""),
        ("SHP-04", "Sadar Bazaar", 28.6600, 77.2130, 110, 0.7, 11 * 60, 16 * 60, 1, 105, 75, 90, False, True, ""),
        # Ring and south Delhi.
        ("SHP-05", "Nehru Place", 28.5490, 77.2500, 80, 0.5, 9 * 60, 18 * 60, 1, 90, 65, 85, False, True, ""),
        ("SHP-06", "Saket", 28.5245, 77.2100, 75, 0.5, 10 * 60, 18 * 60, 1, 88, 62, 84, False, True, ""),
        ("SHP-07", "Okhla Phase II", 28.5350, 77.2730, 130, 0.85, 8 * 60, 17 * 60, 1, 115, 85, 95, False, True, ""),
        ("SHP-08", "Mayapuri Industrial", 28.6280, 77.1250, 140, 0.9, 8 * 60, 17 * 60, 1, 118, 88, 95, False, True, ""),
        ("SHP-09", "Azadpur Mandi", 28.7070, 77.1750, 150, 1.0, 6 * 60, 11 * 60, 2, 120, 90, 95, False, True, ""),
        ("SHP-10", "Rohini Sector 7", 28.7360, 77.1150, 90, 0.6, 8 * 60, 18 * 60, 1, 98, 70, 88, False, True, ""),
        ("SHP-11", "Dwarka Sector 21", 28.5520, 77.0580, 100, 0.65, 8 * 60, 18 * 60, 1, 100, 72, 88, False, True, ""),
        # NCR: the long legs that cross state lines.
        ("SHP-12", "Noida Sector 62", 28.6270, 77.3720, 120, 0.8, 9 * 60, 18 * 60, 1, 110, 80, 92, False, True, ""),
        ("SHP-13", "Sahibabad, Ghaziabad", 28.6790, 77.3300, 110, 0.7, 8 * 60, 18 * 60, 1, 105, 78, 90, False, True, ""),
        ("SHP-14", "Faridabad Sector 24", 28.3900, 77.3130, 135, 0.88, 9 * 60, 18 * 60, 1, 116, 86, 94, False, True, ""),
        ("SHP-15", "AIIMS Pharmacy (cold)", 28.5670, 77.2100, 45, 0.3, 8 * 60, 11 * 60, 3, 70, 55, 75, True, False, "reefer"),
    ],
    overlays=[
        ("Delhi goods-vehicle no-entry, morning (template)", 28.6450, 77.2200, 6.0,
         7 * 60, 11 * 60,
         "Assumption - modelled on the MCD no-entry hours for goods vehicles. "
         "Verify against the current notification; NCR permits differ by class."),
        ("Delhi goods-vehicle no-entry, evening (template)", 28.6450, 77.2200, 6.0,
         17 * 60, 21 * 60,
         "Assumption - verify locally. GRAP stages add further restrictions in winter."),
    ],
    adhoc_spots=[
        ("Lajpat Nagar", 28.5700, 77.2430),
        ("Peeragarhi", 28.6800, 77.0950),
        ("Shahdara", 28.6730, 77.2890),
        ("Gurugram Sector 44", 28.4500, 77.0700),
        ("Noida Sector 18", 28.5700, 77.3210),
        ("Bawana Industrial", 28.7960, 77.0450),
        ("Kirti Nagar", 28.6550, 77.1450),
        ("Sonipat Road", 28.9200, 77.0800),
    ],
    hazard_corridors=[
        # Waterlogging points that make the news every single monsoon.
        ("Minto Bridge underpass", 28.6330, 77.2230, 1.0),
        ("ITO crossing", 28.6280, 77.2410, 1.5),
        ("Zakhira underpass", 28.6650, 77.1550, 1.2),
        ("Pul Prahladpur", 28.5000, 77.2900, 1.5),
    ],
)


HYDERABAD = City(
    id="hyderabad",
    label="Hyderabad",
    region="Telangana",
    center=(17.3850, 78.4867),
    free_flow_kmh=36.0,
    traffic_by_hour=HYD_TRAFFIC,
    notes="Old City access around Charminar is the binding restriction; the "
          "IT corridor demand sits 20 km away on the other side of the city.",
    storm_label="heavy rain",
    storm_hours=(15, 21),
    storm_bias=0.5,
    depots=[
        ("Jeedimetla Industrial", 17.5000, 78.4500),
        ("Shamshabad Cargo Hub", 17.2400, 78.4300),
    ],
    vehicles=[
        ("TS-01", "Jeedimetla Industrial", 1000, 10, (430, 200, 200), ""),
        ("TS-02", "Jeedimetla Industrial", 800, 8, (300, 180, 180), ""),
        ("TS-03", "Jeedimetla Industrial", 600, 6, (260, 165, 170), "reefer"),
        ("TS-04", "Shamshabad Cargo Hub", 900, 9, (400, 195, 195), ""),
        ("TS-05", "Shamshabad Cargo Hub", 700, 7, (300, 180, 180), "tail_lift"),
    ],
    shipments=[
        ("SHP-01", "Charminar Bazaar", 17.3616, 78.4747, 90, 0.6, 8 * 60, 18 * 60, 1, 100, 70, 90, False, True, ""),
        ("SHP-02", "Abids", 17.3900, 78.4750, 70, 0.45, 8 * 60, 17 * 60, 2, 85, 62, 82, False, True, ""),
        ("SHP-03", "Begum Bazaar", 17.3780, 78.4700, 105, 0.68, 8 * 60, 15 * 60, 1, 104, 74, 90, False, True, ""),
        ("SHP-04", "Secunderabad", 17.4400, 78.5000, 85, 0.55, 8 * 60, 18 * 60, 1, 95, 68, 86, False, True, ""),
        ("SHP-05", "Begumpet", 17.4440, 78.4680, 75, 0.5, 9 * 60, 18 * 60, 1, 90, 64, 84, False, True, ""),
        ("SHP-06", "HITEC City", 17.4480, 78.3800, 130, 0.85, 9 * 60, 17 * 60, 1, 115, 85, 94, False, True, ""),
        ("SHP-07", "Gachibowli", 17.4400, 78.3480, 120, 0.78, 9 * 60, 17 * 60, 1, 110, 80, 92, False, True, ""),
        ("SHP-08", "Kukatpally", 17.4850, 78.4100, 95, 0.62, 8 * 60, 18 * 60, 1, 98, 70, 88, False, True, ""),
        ("SHP-09", "Uppal", 17.4050, 78.5590, 100, 0.66, 8 * 60, 18 * 60, 1, 100, 72, 88, False, True, ""),
        ("SHP-10", "LB Nagar", 17.3500, 78.5500, 110, 0.72, 8 * 60, 17 * 60, 1, 106, 76, 90, False, True, ""),
        ("SHP-11", "Mehdipatnam", 17.3950, 78.4370, 80, 0.52, 8 * 60, 18 * 60, 1, 92, 66, 85, False, True, ""),
        ("SHP-12", "Patancheru", 17.5300, 78.2600, 145, 0.95, 8 * 60, 16 * 60, 1, 118, 88, 95, False, True, ""),
        ("SHP-13", "Medchal", 17.6280, 78.4800, 125, 0.82, 8 * 60, 17 * 60, 1, 112, 82, 92, False, True, ""),
        ("SHP-14", "Attapur", 17.3600, 78.4200, 85, 0.55, 8 * 60, 18 * 60, 1, 94, 68, 86, False, True, ""),
        ("SHP-15", "Banjara Hills Clinic (cold)", 17.4130, 78.4480, 45, 0.3, 8 * 60, 11 * 60, 3, 70, 55, 75, True, False, "reefer"),
    ],
    overlays=[
        ("Old City no-entry, morning (template)", 17.3616, 78.4747, 2.5, 9 * 60, 12 * 60,
         "Assumption - modelled on Old City goods vehicle restrictions. Verify "
         "against the current Hyderabad traffic police notification."),
        ("Old City no-entry, evening (template)", 17.3616, 78.4747, 2.5, 17 * 60, 20 * 60,
         "Assumption - verify locally."),
    ],
    adhoc_spots=[
        ("Kompally", 17.5350, 78.4830),
        ("Miyapur", 17.4960, 78.3580),
        ("Nacharam", 17.4260, 78.5560),
        ("Shamirpet", 17.6000, 78.5700),
        ("Bowenpally", 17.4680, 78.4790),
        ("Rajendranagar", 17.3200, 78.4000),
        ("Kothapet", 17.3680, 78.5390),
        ("Sanathnagar", 17.4560, 78.4400),
    ],
    hazard_corridors=[
        ("Musi river crossing", 17.3700, 78.4900, 1.5),
        ("Kukatpally nala", 17.4900, 78.4000, 1.5),
        ("Shaikpet nala", 17.4100, 78.4100, 1.2),
    ],
)


CITIES: dict[str, City] = {c.id: c for c in (BENGALURU, MUMBAI, DELHI, HYDERABAD)}

DEFAULT_CITY = "bengaluru"


def get_city(city_id: str | None) -> City:
    """Never fail on an unknown city — a demo should degrade, not crash."""
    return CITIES.get((city_id or "").lower(), CITIES[DEFAULT_CITY])


def city_list() -> list[dict]:
    return [
        {
            "id": c.id,
            "label": c.label,
            "region": c.region,
            "center": {"lat": c.center[0], "lon": c.center[1]},
            "free_flow_kmh": c.free_flow_kmh,
            "depots": len(c.depots),
            "vehicles": len(c.vehicles),
            "shipments": len(c.shipments),
            "hazard": c.storm_label,
            "notes": c.notes,
        }
        for c in CITIES.values()
    ]
