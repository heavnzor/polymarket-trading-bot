# Control Plane Frontend

Next.js 16 dashboard for the Django control-plane.

UI stack:
- Tailwind CSS 4
- app-level CSS tokens in `app/globals.css`

## Quickstart

```bash
cd apps/frontend
npm install
npm run dev
```

Environment variables:
- `NEXT_PUBLIC_CONTROL_PLANE_URL`
- `NEXT_PUBLIC_CONTROL_PLANE_WS_URL`
- `NEXT_PUBLIC_CONTROL_PLANE_TOKEN` (optional for token-auth local tests)
