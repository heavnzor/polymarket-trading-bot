# Frontend Client

Location: `apps/frontend`

## Stack

- Next.js App Router
- React + TypeScript
- TanStack Query
- Tailwind CSS (enabled via `tailwindcss` + `@tailwindcss/postcss`)
- Existing app CSS tokens/components (`app/globals.css`) remain available

## Main Pages

- `/access` (password authentication)
- `/overview`
- `/positions`
- `/trades`
- `/performance`
- `/settings`
- `/learning`
- `/journal`
- `/chat`
- `/mm` (market-making live view, 10s polling)
- `/cd` (crypto directional signals, 30s polling)

## Data Access

API client lives in `apps/frontend/lib/api.ts` and calls control-plane endpoints under `/api/v1`.
