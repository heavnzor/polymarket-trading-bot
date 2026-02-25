# Role : Developpeur Frontend

## Identite

- **Nom** : Frontend
- **Expertise** : React, Next.js, TypeScript, data visualization, UI/UX, accessibilite
- **Philosophie** : "Un dashboard de trading doit etre lisible en 2 secondes, fiable en toutes circonstances, et utilisable sur n'importe quel device."

## Perimetre

- UI/UX du dashboard (overview, MM, CD, positions, performance)
- Visualisations (charts P&L, equity curve, heatmaps)
- Responsive design et mobile
- Tests frontend (unit, integration, E2E)
- Securite frontend (auth, XSS, CSP)
- Performance et accessibilite

## Diagnostic actuel (score : 5/10)

### Ce qui existe
- Next.js 16 App Router avec TypeScript strict
- TanStack Query pour le state management server-side
- Tailwind CSS + custom design system (CSS variables)
- 11 pages (overview, positions, trades, mm, cd, performance, settings, learning, chat, journal, access)
- WebSocket temps reel via Django Channels
- API layer centralise (`lib/api.ts`)

### Problemes identifies

#### CRITIQUE

1. **ZERO tests** — aucun fichier test
   - Pas de jest/vitest config
   - Pas de Playwright/Cypress
   - Pas de scripts test dans package.json
   - Changement = regression potentielle non detectee

2. **Securite auth D-** — `apps/frontend/app/api/auth/login/route.ts`
   - Cookie contient le mot de passe en base64
   - Session de 30 jours sans revalidation
   - Token API expose dans le bundle JS (`NEXT_PUBLIC_*`)
   - Pas de rate limiting sur login

#### HAUTE

3. **Pas de responsive mobile** — tables overflow, pas de breakpoints mobile
   - `apps/frontend/components/data-table.tsx` : pas de horizontal scroll
   - Nav : flex-wrap mais pas de menu hamburger
   - Font sizes fixes pour certains elements

4. **Tables basiques** — `apps/frontend/components/data-table.tsx`
   - Pas de tri, filtrage, ni pagination
   - Utilise array index comme key (anti-pattern React)
   - Pas de navigation clavier

5. **Pas de visualisations** — aucun chart
   - Pas d'equity curve
   - Pas de graphe P&L
   - Pas de heatmap positions
   - Donnees uniquement en tableaux textuels

6. **WebSocket fragile** — `apps/frontend/components/realtime-feed.tsx`
   - Pas de reconnection automatique
   - Pas d'error boundary
   - Affiche seulement les 14 derniers events

7. **Accessibilite D** — pas d'ARIA labels, pas de focus management, pas de keyboard nav

#### MOYENNE

8. **Pas de loading states** — pas de skeletons, juste un spinner generique
9. **Pas d'error boundaries** — une erreur dans un composant crashe la page
10. **Pas d'i18n** — texte francais hardcode
11. **Pas de dark mode** (malgre des couleurs dark sur la page d'acces)
12. **Pas d'export** (CSV/JSON)
13. **Dockerfile non optimise** — pas de multi-stage build, run as root

## Actions prioritaires

### P0 — Securite auth

**Action 1 : Refaire le systeme d'auth**
- Fichier : `apps/frontend/app/api/auth/login/route.ts`
  ```typescript
  import { SignJWT, jwtVerify } from 'jose'

  const SECRET = new TextEncoder().encode(process.env.JWT_SECRET!)

  export async function POST(request: Request) {
    const { password } = await request.json()

    if (password !== process.env.DASHBOARD_PASSWORD) {
      // Rate limit: 5 tentatives / minute
      return Response.json({ error: 'Invalid password' }, { status: 401 })
    }

    const token = await new SignJWT({ role: 'admin' })
      .setProtectedHeader({ alg: 'HS256' })
      .setExpirationTime('8h')
      .setIssuedAt()
      .sign(SECRET)

    const response = Response.json({ ok: true })
    response.headers.set('Set-Cookie',
      `session=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=28800`
    )
    return response
  }
  ```

**Action 2 : Proxy API server-side**
- Nouveau : `apps/frontend/app/api/proxy/[...path]/route.ts`
  - Toutes les requetes API passent par ce proxy
  - Le token `CONTROL_PLANE_TOKEN` reste cote serveur (plus de `NEXT_PUBLIC_`)
  - Verifie le JWT session avant de forwarder

### P1 — Visualisations

**Action 3 : Ajouter les charts de performance**
- Dependance : `recharts` (leger, React-native)
- Nouveaux composants :
  - `components/charts/equity-curve.tsx` — courbe d'equity cumulative
  - `components/charts/pnl-bar.tsx` — P&L par jour (barres vertes/rouges)
  - `components/charts/drawdown.tsx` — courbe de drawdown
  - `components/charts/spread-heatmap.tsx` — heatmap des spreads MM par marche
- Integrer dans les pages :
  - `/overview` : equity curve + P&L journalier
  - `/performance` : drawdown + metriques detaillees
  - `/mm` : heatmap spreads + fill rate

**Action 4 : Ameliorer les tables**
- Fichier : `apps/frontend/components/data-table.tsx`
  ```typescript
  interface DataTableProps<T> {
    data: T[]
    columns: Column<T>[]
    sortable?: boolean
    filterable?: boolean
    pageSize?: number
    exportable?: boolean  // CSV/JSON
    emptyMessage?: string
  }
  ```
  - Tri par colonne (click header)
  - Filtre texte global
  - Pagination (10/25/50 items)
  - Export CSV/JSON
  - Keys stables (pas d'index)
  - Responsive : horizontal scroll sur mobile

### P1 — Tests

**Action 5 : Setup Vitest + React Testing Library**
- Fichier : `apps/frontend/vitest.config.ts`
- Fichier : `apps/frontend/vitest.setup.ts`
- Ajouter dans `package.json` : `"test": "vitest", "test:coverage": "vitest --coverage"`
- Tests prioritaires :
  - `__tests__/components/data-table.test.tsx` — rendu, tri, pagination
  - `__tests__/components/realtime-feed.test.tsx` — connexion WS, affichage events
  - `__tests__/lib/api.test.ts` — fetch, erreurs, retry
  - `__tests__/app/overview/page.test.tsx` — rendu page, loading, erreur

**Action 6 : Setup Playwright E2E**
- Fichier : `apps/frontend/playwright.config.ts`
- Tests :
  - `e2e/auth.spec.ts` — login, session, logout
  - `e2e/overview.spec.ts` — navigation, donnees affichees
  - `e2e/mm.spec.ts` — page MM, quotes, inventaire

### P1 — Responsive

**Action 7 : Mobile-first redesign**
- Fichier : `apps/frontend/app/globals.css`
  - Ajouter breakpoints : `sm: 640px`, `md: 768px`, `lg: 1024px`
- Fichier : `apps/frontend/components/dashboard-shell.tsx`
  - Menu hamburger sur mobile (< 768px)
  - Nav bottom bar sur mobile (les 5 pages principales)
- Toutes les tables : horizontal scroll wrapper sur mobile
- Cards au lieu de tables pour les donnees simples sur mobile

### P1 — WebSocket robuste

**Action 8 : Reconnection automatique**
- Fichier : `apps/frontend/components/realtime-feed.tsx`
  ```typescript
  function useWebSocket(url: string) {
    const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
    const reconnectDelay = useRef(1000)

    useEffect(() => {
      let ws: WebSocket
      let reconnectTimer: NodeJS.Timeout

      function connect() {
        ws = new WebSocket(url)
        ws.onopen = () => {
          setStatus('connected')
          reconnectDelay.current = 1000 // reset
        }
        ws.onclose = () => {
          setStatus('disconnected')
          reconnectTimer = setTimeout(() => {
            reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30000)
            connect()
          }, reconnectDelay.current)
        }
        ws.onmessage = (event) => { /* ... */ }
      }

      connect()
      return () => { ws?.close(); clearTimeout(reconnectTimer) }
    }, [url])

    return { status }
  }
  ```

### P2 — Error boundaries et loading states

**Action 9 : Error boundary global**
- Nouveau : `apps/frontend/components/error-boundary.tsx`
  ```typescript
  'use client'
  import { Component, ReactNode } from 'react'

  export class ErrorBoundary extends Component<
    { children: ReactNode; fallback?: ReactNode },
    { hasError: boolean; error?: Error }
  > {
    state = { hasError: false, error: undefined as Error | undefined }

    static getDerivedStateFromError(error: Error) {
      return { hasError: true, error }
    }

    render() {
      if (this.state.hasError) {
        return this.props.fallback ?? (
          <div className="panel" style={{ textAlign: 'center', padding: '2rem' }}>
            <h3>Erreur inattendue</h3>
            <p>{this.state.error?.message}</p>
            <button onClick={() => this.setState({ hasError: false })}>Reessayer</button>
          </div>
        )
      }
      return this.props.children
    }
  }
  ```

**Action 10 : Loading skeletons**
- Nouveau : `apps/frontend/components/skeleton.tsx`
- Remplacer les spinners par des skeletons qui mimiquent la forme des donnees
- Utiliser `Suspense` de React 19 avec les skeletons comme fallback

### P2 — Accessibilite

**Action 11 : ARIA et keyboard navigation**
- Ajouter `aria-label` sur tous les boutons et liens dans `dashboard-shell.tsx`
- Ajouter `scope="col"` sur les headers de table dans `data-table.tsx`
- Focus visible sur tous les elements interactifs
- Skip-to-content link
- Contraste : verifier avec axe-core

### P3 — Dark mode et i18n

**Action 12 : Theme toggle**
- CSS variables deja en place → ajouter un jeu de variables dark
- Toggle dans `dashboard-shell.tsx`
- Persister en `localStorage`

**Action 13 : i18n basique**
- Fichier : `apps/frontend/lib/i18n.ts` avec dictionnaires FR/EN
- Wrapper `t('key')` dans les pages

## Criteres de validation

- [ ] Auth par JWT httpOnly secure (plus de mot de passe en cookie)
- [ ] Token API invisible cote client (proxy server-side)
- [ ] Charts : equity curve, P&L journalier, drawdown fonctionnels
- [ ] Tables : tri, filtre, pagination, export CSV
- [ ] Vitest : > 50% coverage composants
- [ ] Playwright : parcours login → overview → MM fonctionnel
- [ ] Responsive : dashboard utilisable sur iPhone SE (375px)
- [ ] WebSocket : reconnection automatique avec backoff
- [ ] Error boundaries sur toutes les pages
- [ ] Loading skeletons (pas de flash de contenu vide)
- [ ] Score axe-core : 0 violations critical/serious

## Regles

- Chaque composant est testable (pas de side effects dans le rendu)
- TypeScript strict : pas de `any`, pas de `// @ts-ignore`
- Les styles utilisent le design system (CSS variables), pas de valeurs hardcodees
- Mobile-first : construire pour mobile, enrichir pour desktop
- Performance : pas de re-renders inutiles (React.memo, useMemo quand necessaire)
- Accessibilite : chaque element interactif est atteignable au clavier
