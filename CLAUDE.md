# Project Instructions

## Stack
- Frontend: React, TypeScript, Vite, and Mantine.
- Frontend state: Redux Toolkit for shared client state and RTK Query for server state.
- Backend: typed Python and FastAPI.
- Tests: use the test frameworks already configured in each package.

## Repository Map
- `frontend/`: browser application.
- `backend/`: API and document-processing logic.
- `docs/`: durable product and architecture documentation.

## Working Rules
- Inspect nearby code before introducing a new pattern.
- Keep frontend and backend contracts explicit and typed.
- Prefer focused changes over unrelated cleanup.
- Do not add dependencies without explaining why existing tools are insufficient.
- Run targeted validation after edits; expand to the full suite before release-sized changes.
- Keep the main Sonnet session responsible for architecture and integration.
- Delegate only bounded, context-heavy work to subagents.
- Define or approve API contracts before frontend and backend agents depend on them.
- Run frontend and backend agents sequentially unless their file ownership is clearly separate.
- Use the researcher only for external or version-sensitive uncertainty.
- Use the test engineer for routine focused tests; return architecturally complex tests to the main agent.

## Frontend State
- Prefer RTK Query for fetching, caching, invalidation, and server-state synchronization.
- Use Redux Toolkit slices only for genuinely shared client state.
- Keep component-local UI and form state local.
- Reuse the established store, base query, tags, and error-handling conventions.
- Do not introduce another global state library without explicit justification.

## Commands
- Frontend install: `cd frontend && npm install`
- Frontend dev: `cd frontend && npm run dev`
- Frontend checks: replace this line with the repository's actual commands.
- Backend install: replace this line with the repository's actual environment command.
- Backend dev: replace this line with the repository's actual FastAPI command.
- Backend tests: replace this line with the repository's actual pytest command.

## Documentation
- Keep this file below 200 lines.
- Store detailed repeatable procedures in `.claude/skills/`, not here.
- Update this file only for durable architecture, commands, conventions, and workflows.
- Run `/sync-claude-md` manually after meaningful structural changes; do not treat this file as a changelog.
