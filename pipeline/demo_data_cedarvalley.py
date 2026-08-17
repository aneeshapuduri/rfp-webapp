"""Hand-authored SBA-quality classification for the Cedar Valley school district RFP — a
genuinely different mix from the other two samples: contains clear, ambiguous, AND
assumption_needed items together (Northfield was almost entirely ambiguous; Lakeview was
almost entirely clear). Used to broaden Phase 6 test coverage beyond the original two cases."""

CEDARVALLEY_ITEMS = [
    {
        "requirement": "Replace core network switches across all 12 school buildings to support gigabit connectivity to every classroom",
        "source_section": "scope_of_work (a)",
        "status": "clear",
        "reasoning": "Building count and target connectivity standard (gigabit) are both specified — sufficient to size the hardware refresh.",
    },
    {
        "requirement": "Provide wireless access point coverage sufficient for the District's 1:1 student device program in all classrooms",
        "source_section": "scope_of_work (b)",
        "status": "ambiguous",
        "reasoning": "No student enrollment, classroom count, or device density figures are given, despite this being exactly the kind of volume detail needed to size AP counts (unscoped volume/scale).",
        "clarification_question": "What is the District's total student enrollment and classroom count, so we can size wireless access point density appropriately for full 1:1 coverage?",
    },
    {
        "requirement": "Improve the District's overall network security posture",
        "source_section": "scope_of_work (c)",
        "status": "ambiguous",
        "reasoning": "References security improvement in general terms without naming a target standard, framework, or specific current gap (undefined compliance/security bar).",
        "clarification_question": "Is there a specific security framework or standard (e.g. NIST, state K-12 cybersecurity guidelines) the District wants this network security improvement measured against?",
    },
    {
        "requirement": "Ensure content filtering complies with the Children's Internet Protection Act (CIPA)",
        "source_section": "scope_of_work (d)",
        "status": "clear",
        "reasoning": "Names a specific, well-defined federal standard (CIPA) with established compliance requirements a vendor can design against directly.",
    },
    {
        "requirement": "Migrate the District's on-premises file servers to a cloud environment",
        "source_section": "scope_of_work (e)",
        "status": "ambiguous",
        "reasoning": "No data volume, current server count, or target cloud provider/environment is specified, all of which materially affect migration effort and cost (missing implementation detail).",
        "clarification_question": "Approximately how much data and how many file servers need to be migrated, and does the District have a preferred cloud provider (e.g. Azure, AWS, Google Cloud)?",
    },
    {
        "requirement": "Provide a network monitoring and alerting dashboard for District IT staff",
        "source_section": "scope_of_work (f)",
        "status": "clear",
        "reasoning": "The deliverable and its audience are specific enough to design and estimate using standard network monitoring tooling.",
    },
    {
        "requirement": "Complete all work during the summer break before the next school year begins",
        "source_section": "scope_of_work (g)",
        "status": "assumption_needed",
        "reasoning": "A real, well-understood constraint in K-12 contracting, but exact calendar dates aren't given. A standard assumption about the summer window is lower-risk than a blocking clarification question.",
        "assumption_text": "We assume a summer construction window of approximately 10 weeks (early June through mid-August); exact start/end dates will be confirmed with the District's academic calendar during discovery.",
    },
    {
        "requirement": "Provide staff training on any new network security tools introduced",
        "source_section": "scope_of_work (h)",
        "status": "assumption_needed",
        "reasoning": "Training format and duration aren't specified, but a standard onboarding package is a low-risk assumption that doesn't require blocking the bid.",
        "assumption_text": "We assume a half-day, in-person training session for District IT staff on any newly introduced security tools, with recorded sessions provided for future reference.",
    },
]
