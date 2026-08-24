# Incident Triage

Initech (free), native, **tier 1 — SOP only, no tools**. An alert plus recent deploys goes in; a
recommendation comes out: roll back a named deployment, escalate to on-call, or monitor.

The agent *recommends*; it does not act. That is what keeps it tier 1, and it is also why
this is the flood generator for proof 1 (`dos demo flood --tenant initech`) — no tool calls
means 200 concurrent jobs stay fast and cheap while still being real agent work.

Install: `dos tenant install initech incident-triage`
