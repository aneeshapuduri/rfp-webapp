"""Hand-authored SA-quality output for the Lakeview (resolved/clear) case, used to test the
Phase 3 pipeline — technology approach, staffing, pricing, sanity checks — without requiring
a live API key. Runs through the exact same pricing_engine.py and sanity_checks.py code that
live Claude output will flow through."""

LAKEVIEW_TECH_APPROACH = (
    "Solution Architecture:\n"
    "We will deliver a cloud-hosted, auto-scaling portal built to support well beyond the "
    "50,000 monthly active resident target, with a responsive web front end usable on any "
    "mobile browser. Role-based access will be implemented for the four staff personas "
    "identified: dispatchers, field crews, supervisors, and administrators.\n"
    "Integration Approach:\n"
    "GIS integration with the City's Esri ArcGIS platform will use ArcGIS REST services for "
    "geospatial plotting and location-based routing. Work-order integration will be built as "
    "a configurable event-driven sync layer, validated against the City's specific system "
    "during discovery per our stated assumption.\n"
    "Data Migration:\n"
    "The approximately 380,000 historical records will be migrated using a staged ETL process "
    "with full reconciliation and parallel-run validation before cutover.\n"
    "Security & Compliance:\n"
    "Architecture will align to NIST 800-53 moderate-impact controls from the outset, with our "
    "in-house SOC providing continuous monitoring from go-live through the 12-month support "
    "period. WCAG 2.1 AA accessibility will be built in from the first sprint.\n"
    "Quality Assurance:\n"
    "Each release passes automated regression testing, accessibility scanning, and a manual "
    "UAT cycle with City stakeholders before promotion to production."
)

# Realistic staffing plan for a 9-month, moderate-complexity public-sector portal project.
LAKEVIEW_STAFFING_PLAN = [
    {"role": "Program Manager", "headcount": 1, "hours_per_person": 720,
     "rationale": "Full-time-equivalent oversight across the 9-month engagement, ~20 hrs/week average."},
    {"role": "Solutions Architect", "headcount": 1, "hours_per_person": 300,
     "rationale": "Front-loaded architecture and integration design, tapering after Phase 2."},
    {"role": "Business Analyst", "headcount": 1, "hours_per_person": 400,
     "rationale": "Requirements refinement, UAT coordination, and City stakeholder liaison."},
    {"role": "Senior / Lead Developer", "headcount": 2, "hours_per_person": 600,
     "rationale": "Two senior developers carrying core portal, GIS, and work-order integration builds."},
    {"role": "Mid-Level Developer", "headcount": 2, "hours_per_person": 500,
     "rationale": "Supporting feature development, dashboard, and staff-facing views."},
    {"role": "DevOps / Cloud Engineer", "headcount": 1, "hours_per_person": 350,
     "rationale": "Cloud infrastructure setup, CI/CD, and auto-scaling configuration."},
    {"role": "Security & Compliance Engineer", "headcount": 1, "hours_per_person": 250,
     "rationale": "NIST 800-53 control implementation and SOC onboarding."},
    {"role": "QA / Test Engineer", "headcount": 1, "hours_per_person": 400,
     "rationale": "Regression, accessibility, and UAT test cycles across all releases."},
    {"role": "UX/UI Designer", "headcount": 1, "hours_per_person": 200,
     "rationale": "Resident- and staff-facing UX design, WCAG-conscious from the start."},
    {"role": "Data Migration Specialist", "headcount": 1, "hours_per_person": 300,
     "rationale": "ETL design and execution for the 380,000-record migration."},
    {"role": "Technical Writer", "headcount": 1, "hours_per_person": 100,
     "rationale": "Knowledge transfer documentation for City IT staff."},
    {"role": "Support / Helpdesk (Tier 1-2)", "headcount": 2, "hours_per_person": 200,
     "rationale": "Post-launch support coverage during the 12-month support window."},
]

# Deliberately broken staffing plan to prove the sanity checks actually catch bad output:
# a 9-month, ~380k-record migration project staffed with 40 total hours (implausibly thin).
BROKEN_STAFFING_PLAN = [
    {"role": "Mid-Level Developer", "headcount": 1, "hours_per_person": 40,
     "rationale": "Deliberately understaffed to test the sanity-check floor."},
]