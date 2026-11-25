"""The company profile the agent judges fit against.

Kept in one place so the bids team can edit it without touching prompts or
scoring code. Anything not listed here is treated as UNKNOWN, never assumed.
"""

IDIAZ_PROFILE = {
    "name": "idiaz.io",
    "summary": "A US-registered, AI-native engineering studio with a small senior team.",
    "builds": [
        "Automation platforms",
        "Compliance tooling",
        "AI-integrated operations software",
        "Web and mobile apps",
    ],
    "known_facts": [
        "US-registered company",
        "Small team of senior engineers (cannot staff large headcount or 24/7 on-site crews)",
        "AI-native: LLMs, agents, workflow automation, integrations",
    ],
    # Things the brief does NOT tell us. The agent must report these as
    # 'unknown' rather than guess.
    "unknown": [
        "Socio-economic set-aside certifications (8(a), HUBZone, SDVOSB, WOSB, EDWOSB)",
        "Small-business size status under a given NAICS code",
        "Facility or personnel security clearances",
        "FedRAMP / StateRAMP authorisations, CMMC level, ISO 27001, SOC 2",
        "Specific past-performance references, bonding capacity, insurance limits",
        "State or local vendor registrations",
    ],
}


def profile_as_text() -> str:
    p = IDIAZ_PROFILE
    lines = [
        f"COMPANY: {p['name']} -- {p['summary']}",
        "WHAT WE BUILD: " + "; ".join(p["builds"]) + ".",
        "KNOWN: " + "; ".join(p["known_facts"]) + ".",
        "NOT KNOWN (treat as 'unknown', never assume we have them): " + "; ".join(p["unknown"]) + ".",
    ]
    return "\n".join(lines)
