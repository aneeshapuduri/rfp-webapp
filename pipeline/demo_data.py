"""
Hand-authored requirement classifications for both sample RFPs, written the way a careful
senior business analyst would classify them against config/ambiguity_criteria.md. This lets
us test the Phase 1 schema, validation, and halt/proceed decision logic end-to-end without
requiring a live ANTHROPIC_API_KEY — the exact same code path (schema.py's from_raw/validate)
that real Claude output will flow through in production.
"""

# Lakeview Citizen Service Request Portal — a well-specified RFP.
# Expect: mostly clear, one reasonable assumption, ZERO ambiguous -> pipeline should PROCEED.
LAKEVIEW_ITEMS = [
    {
        "requirement": "Cloud-hosted portal accessible via web and mobile browser, supporting 50,000+ monthly active residents",
        "source_section": "scope_of_work (a)",
        "status": "clear",
        "reasoning": "Platform type, access channels, and a concrete volume target are all specified — sufficient for a solutions architect to size the work.",
    },
    {
        "requirement": "Integration with the City's Esri ArcGIS platform for location-based reporting and routing",
        "source_section": "scope_of_work (b)",
        "status": "clear",
        "reasoning": "Names the specific GIS platform (Esri ArcGIS), which has well-documented REST integration patterns a vendor can design against.",
    },
    {
        "requirement": "Integration with the City's work-order management system for automatic ticket creation and status sync",
        "source_section": "scope_of_work (c)",
        "status": "assumption_needed",
        "reasoning": "The specific work-order system and its API/interface type are not named, which would normally be an implementation-detail gap. However, this is a common, standard integration pattern (event-driven ticket sync) that can be designed generically and confirmed during discovery without blocking the bid — treated as an assumption rather than a blocking ambiguity.",
        "assumption_text": "We assume the City's work-order system exposes a REST API or webhook mechanism for ticket creation and status updates; exact integration specifics will be confirmed during the discovery phase with City IT.",
    },
    {
        "requirement": "Public-facing dashboard showing aggregate request status and resolution times",
        "source_section": "scope_of_work (d)",
        "status": "clear",
        "reasoning": "The deliverable and its content (aggregate status, resolution times) are specific enough to design and estimate.",
    },
    {
        "requirement": "WCAG 2.1 AA accessibility compliance",
        "source_section": "scope_of_work (e)",
        "status": "clear",
        "reasoning": "Names a specific, well-defined standard with a specified conformance level.",
    },
    {
        "requirement": "Role-based access for dispatchers, field crews, supervisors, and administrators",
        "source_section": "scope_of_work (f)",
        "status": "clear",
        "reasoning": "Enumerates the exact roles needed; standard RBAC design applies.",
    },
    {
        "requirement": "Migration of approximately 380,000 historical request records",
        "source_section": "scope_of_work (g)",
        "status": "clear",
        "reasoning": "A concrete record volume is given, which is what's needed to size a migration effort.",
    },
    {
        "requirement": "Security architecture consistent with NIST 800-53 moderate-impact controls",
        "source_section": "scope_of_work (h)",
        "status": "clear",
        "reasoning": "Names both the standard (NIST 800-53) and the specific baseline (moderate-impact), which is exactly the level of detail rule 5 requires to not be ambiguous.",
    },
    {
        "requirement": "12 months of post-launch support and a knowledge transfer plan to City IT staff",
        "source_section": "scope_of_work (i)",
        "status": "clear",
        "reasoning": "Duration and deliverable (knowledge transfer plan) are both specified.",
    },
    {
        "requirement": "Delivery within 9 months of contract award",
        "source_section": "scope_of_work (j)",
        "status": "clear",
        "reasoning": "A concrete deadline is given relative to a clear trigger event (contract award).",
    },
]

# Northfield Emergency Dispatch System Upgrade — a deliberately vague RFP.
# Expect: multiple genuine ambiguities -> pipeline should HALT for clarification.
NORTHFIELD_ITEMS = [
    {
        "requirement": "Upgrade dispatch software to improve reliability and reduce downtime",
        "source_section": "scope_of_work (a)",
        "status": "ambiguous",
        "reasoning": "No baseline or target metric for 'reliability' or 'downtime' is given (undefined acceptance criteria), and it's unclear whether an in-place upgrade or a full replacement is expected (missing implementation detail).",
        "clarification_question": "Should the vendor propose an upgrade to the existing dispatch software, or is a full platform replacement in scope? Additionally, is there a target reliability/uptime metric (e.g. 99.9%) or a documented downtime baseline we should design against?",
    },
    {
        "requirement": "Integrate with responder communication tools",
        "source_section": "scope_of_work (b)",
        "status": "ambiguous",
        "reasoning": "The specific communication tools/systems are not named, and no interface type is given — insufficient detail to scope an integration.",
        "clarification_question": "Which specific responder communication tools/systems must this integrate with (e.g. radio dispatch systems, mobile data terminals, specific vendor platforms), and what integration interfaces do they expose?",
    },
    {
        "requirement": "Support the County's dispatch volume, which has grown in recent years",
        "source_section": "scope_of_work (c)",
        "status": "ambiguous",
        "reasoning": "No current or projected volume figures are given (unscoped volume/scale) despite implying growth that materially affects system sizing.",
        "clarification_question": "What is the County's current daily/annual dispatch call volume, and is there a projected growth rate we should design capacity around?",
    },
    {
        "requirement": "Provide appropriate data security for sensitive dispatch and responder information",
        "source_section": "scope_of_work (d)",
        "status": "ambiguous",
        "reasoning": "References security in general terms without naming a compliance standard or control baseline (undefined compliance/security bar).",
        "clarification_question": "Is there a specific security/compliance standard (e.g. CJIS, NIST 800-53, state-specific requirements) this system must meet given it handles emergency dispatch data?",
    },
    {
        "requirement": "Host the solution in an environment consistent with County IT standards",
        "source_section": "scope_of_work (e)",
        "status": "ambiguous",
        "reasoning": "The County's IT standards are referenced but not described or attached, and it's unstated whether/when the County will make them available (unstated ownership/dependency), while also leaving hosting/infrastructure undefined (undefined infrastructure constraints).",
        "clarification_question": "Can the County provide its IT hosting standards/policies (e.g. cloud vs. on-prem requirements, approved vendors, network architecture constraints) so we can design a compliant hosting approach?",
    },
    {
        "requirement": "Migrate existing dispatch records to the new system",
        "source_section": "scope_of_work (f)",
        "status": "ambiguous",
        "reasoning": "No record volume, date range, or format is given, and migration effort scales heavily with volume (unscoped volume/scale directly affecting cost).",
        "clarification_question": "Approximately how many historical dispatch records need to be migrated, over what date range, and in what format/system are they currently stored?",
    },
    {
        "requirement": "Provide staff training and ongoing support after go-live",
        "source_section": "scope_of_work (g)",
        "status": "assumption_needed",
        "reasoning": "Training format and support duration aren't specified, but a standard, clearly-labeled support package is a low-risk assumption that doesn't require blocking the bid on a client round-trip.",
        "assumption_text": "We assume a standard onboarding training program (in-person or virtual sessions for dispatch staff) plus 90 days of post-go-live business-hours support, adjustable based on County preference during contract negotiation.",
    },
    {
        "requirement": "Complete the project as quickly as reasonably possible given the urgency of the need",
        "source_section": "scope_of_work (h)",
        "status": "ambiguous",
        "reasoning": "No concrete deadline or duration is given at all — 'as quickly as reasonably possible' provides no basis for a timeline commitment or schedule-risk pricing (undefined acceptance criteria).",
        "clarification_question": "Is there a specific target completion date or maximum project duration the County needs us to commit to?",
    },
]
