"""Hand-authored narrative sections + compliance matrix for the Lakeview case, mapped to the
actual REQ-00X ids produced by Phase 1's demo_data.py, used to test Phase 4 without a live API
key. The compliance matrix intentionally goes through the exact same completeness-check code
path real Claude output would."""

EXEC_SUMMARY = (
    "The City of Lakeview Public Works Department is seeking a qualified partner to replace "
    "a 15-year-old citizen service request system that no longer meets the needs of residents "
    "or staff. Meridian Systems Group proposes to design, build, and deploy a modern, "
    "cloud-hosted service request portal that resolves each gap identified in this RFP within "
    "the City's nine-month timeline, backed by our Agile-Gov delivery framework, in-house "
    "Security Operations Center, and a three-year track record of zero missed contractual "
    "milestones on comparable public-sector modernizations."
)

UNDERSTANDING = (
    "Lakeview's current service request system was not designed for how residents and staff "
    "work today. Residents expect mobile-friendly reporting with visible status tracking; "
    "staff need the system to talk to the City's GIS and work-order platforms instead of "
    "requiring manual, duplicative data entry. With 380,000 historical records at stake, this "
    "transition also carries real data-integrity risk if migration isn't planned with the same "
    "rigor as the new system itself, and WCAG 2.1 AA and NIST 800-53 requirements must be "
    "designed in from day one, not retrofitted before launch."
)

TIMELINE = [
    {"phase": "Discovery & Planning", "duration": "Weeks 1-4", "description": "Requirements validation, GIS/work-order interface mapping, finalized project plan."},
    {"phase": "Architecture & Design", "duration": "Weeks 5-8", "description": "Solution architecture, accessibility/security design review, UX design."},
    {"phase": "Core Development - Phase 1", "duration": "Weeks 9-16", "description": "Resident-facing portal, submission flow, initial GIS integration."},
    {"phase": "Core Development - Phase 2", "duration": "Weeks 17-24", "description": "Work-order integration, staff dashboards, public status dashboard."},
    {"phase": "Data Migration & Validation", "duration": "Weeks 21-28", "description": "Historical data migration, reconciliation, parallel-run validation."},
    {"phase": "UAT & Accessibility Audit", "duration": "Weeks 29-32", "description": "City staff UAT, WCAG 2.1 AA audit, NIST 800-53 control validation."},
    {"phase": "Launch & Cutover", "duration": "Weeks 33-36", "description": "Production cutover, legacy decommission plan, go-live support."},
    {"phase": "Post-Launch Support", "duration": "Months 9-21", "description": "12 months of SOC-monitored support and knowledge transfer to City IT."},
]

PAST_PERFORMANCE = (
    "State Department of Transportation ($4.2M, 2021-2024): Meridian modernized a legacy "
    "permitting system processing 1.2M annual transactions, reducing processing time by 63% "
    "and achieving 99.95% uptime — directly comparable to replacing Lakeview's legacy system "
    "at scale without service interruption.\n"
    "County Health & Human Services Agency ($2.8M, 2020-2023): We delivered a HIPAA-compliant "
    "case management platform for 45 caseworkers, cutting resolution time from 21 to 9 days — "
    "the role-based access and compliance rigor mirror this RFP's staff-role and NIST 800-53 "
    "requirements.\n"
    "Regional Transit Authority ($1.9M, 2022-2025): Meridian built a real-time fleet analytics "
    "dashboard integrating GPS, maintenance, and ridership data across 340 vehicles — direct "
    "experience with the real-time, multi-system integration and public dashboard this RFP "
    "requires."
)

CLOSING = (
    "Meridian Systems Group is prepared to begin work on the Modernization of the Citizen "
    "Service Request Portal immediately upon award. We welcome any questions the evaluation "
    "committee may have about our technical approach, schedule, or past performance. Please "
    "direct inquiries to Dana Whitfield, VP of Proposals & Partnerships, at "
    "d.whitfield@meridiansystemsgroup.example or (512) 555-0148. We thank the City of Lakeview "
    "Public Works Department for the opportunity to be considered for this project."
)

# Mapped to the exact REQ-001..REQ-010 ids produced by demo_data.LAKEVIEW_ITEMS.
COMPLIANCE_MATRIX = [
    {"requirement_id": "REQ-001", "response": "Delivered as a responsive, auto-scaling cloud-native application, capacity engineered beyond the stated 50,000 MAU volume.", "status": "Full Compliance"},
    {"requirement_id": "REQ-002", "response": "Integrated via ArcGIS REST services for geospatial plotting and location-based routing.", "status": "Full Compliance"},
    {"requirement_id": "REQ-003", "response": "Built as an event-driven sync layer; exact system specifics confirmed during discovery per our stated assumption.", "status": "Full Compliance"},
    {"requirement_id": "REQ-004", "response": "Included as a standard module surfacing aggregate status and resolution-time metrics.", "status": "Full Compliance"},
    {"requirement_id": "REQ-005", "response": "Built to WCAG 2.1 AA from the first sprint, with accessibility scanning in every release.", "status": "Full Compliance"},
    {"requirement_id": "REQ-006", "response": "Four distinct role-based views and permission sets delivered as part of the core platform.", "status": "Full Compliance"},
    {"requirement_id": "REQ-007", "response": "Staged ETL migration with full reconciliation and parallel-run validation prior to cutover.", "status": "Full Compliance"},
    {"requirement_id": "REQ-008", "response": "Architecture designed to NIST 800-53 moderate-impact controls, led by our CISSP-certified Security & Compliance Lead.", "status": "Full Compliance"},
    {"requirement_id": "REQ-009", "response": "12 months of SOC-monitored post-launch support with a structured knowledge transfer plan.", "status": "Full Compliance"},
    {"requirement_id": "REQ-010", "response": "Project plan sequences discovery through launch within a 36-week (~9-month) schedule.", "status": "Full Compliance"},
]

# Deliberately missing REQ-008 (NIST 800-53) to test the completeness check.
BROKEN_COMPLIANCE_MATRIX = [item for item in COMPLIANCE_MATRIX if item["requirement_id"] != "REQ-008"]

DEMO_NARRATIVE = {
    "executive_summary": EXEC_SUMMARY,
    "understanding": UNDERSTANDING,
    "timeline": TIMELINE,
    "past_performance": PAST_PERFORMANCE,
    "closing": CLOSING,
    "compliance_matrix": COMPLIANCE_MATRIX,
}
