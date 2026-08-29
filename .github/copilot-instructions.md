# Sloty Backend AI Instructions

Always read `AGENTS.md` before planning or making code changes.

Treat `AGENTS.md` as the engineering source of truth for this repository.

Source-of-truth order:

1. `AGENTS.md`
2. Current locked contracts under `docs/` (currently
   `docs/recurring-bookings-contract.txt`)
3. `README.md`
4. Historical planning documents (`docs/business-analysis.txt`,
   `docs/documentation.txt`, `docs/sprints.txt`) for product context only

Do not implement or restore architecture from historical planning documents
when they conflict with `AGENTS.md` or current locked contracts.

After every code change, review whether `AGENTS.md` needs an update.

Update `AGENTS.md` when the change affects:
- architecture
- folder structure
- request flow
- commands
- testing strategy
- tooling
- permissions/authentication
- database/query conventions
- repeated project patterns

If no update is needed, state:
“AGENTS.md reviewed; no update needed.”
