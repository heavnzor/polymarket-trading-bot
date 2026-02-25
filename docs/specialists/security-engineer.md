# Role : Security Engineer

## Identite

- **Nom** : SecOps
- **Expertise** : Application security, cryptography, DeFi security, penetration testing
- **Philosophie** : "La securite n'est pas une feature, c'est une contrainte. Chaque ligne de code est une surface d'attaque."

## Perimetre

- Gestion des secrets (wallet, API keys, tokens)
- Authentification et autorisation
- Hardening applicatif (OWASP Top 10)
- Securite reseau (firewall, TLS, headers)
- Audit de code (SAST/DAST)
- Securite on-chain (wallet, transactions)

## Diagnostic actuel (score : 4/10)

### Ce qui existe
- `.env` gitignore (secrets hors du code)
- Repo prive
- Requetes SQL parametrees (pas d'injection)
- Bridge token pour auth worker→backend
- CORS configure

### Vulnerabilites identifiees

#### CRITIQUE
1. **Cle privee wallet en clair** — `services/worker/config.py:22`
   ```python
   self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
   ```
   En memoire cleartext, dump process = fonds voles

2. **Django SECRET_KEY par defaut** — `apps/backend/control_plane/settings.py`
   Valeur = `"django-insecure-change-me"` → sessions forgeable

3. **DEBUG = True en production** — `apps/backend/control_plane/settings.py`
   Stack traces exposees, informations sensibles

4. **ALLOWED_HOSTS = ["*"]** — accepte toutes les origines

5. **Cookie session = mot de passe base64** — `apps/frontend/app/api/auth/login/route.ts`
   Previsible, pas de signature, pas de httpOnly secure

6. **Token expose cote navigateur** — `NEXT_PUBLIC_CONTROL_PLANE_TOKEN`
   Prefix `NEXT_PUBLIC_` = inclus dans le bundle JS client

#### HAUTE
8. **Pas de rate limiting** — aucune API (REST, WebSocket, bridge)
9. **Pas de CSRF hardening** sur les mutations
10. **Bridge token statique** — pas de rotation, pas de HMAC
11. **Pas de security headers** — HSTS, X-Frame-Options, CSP absents de nginx
12. **Pas de scan de dependances** — vulnerabilites connues non detectees

#### MOYENNE
13. **Balance check bypass** — `services/worker/executor/client.py:152-160` — si RPC fail, ordre passe sans verification
14. **Messages utilisateur non bornes** — `services/worker/conversation/router.py:84-136`
15. **Pas de signature sur les commandes** du control-plane

## Actions prioritaires

### P0 — Secrets management

**Action 1 : Eliminer les secrets en clair**
- Fichier : `services/worker/config.py`
  - Charger la cle privee depuis un fichier chiffre (GPG) ou variable d'environnement injectee par le process manager
  - Ajouter un wipe en memoire apres signature : utiliser `ctypes` pour zero-fill la string
  - Ne jamais logger la cle, meme partiellement
  ```python
  import ctypes
  def _wipe_string(s: str):
      """Zero-fill string memory."""
      if s:
          ctypes.memset(id(s) + 49, 0, len(s))
  ```

**Action 2 : Fixer Django settings**
- Fichier : `apps/backend/control_plane/settings.py`
  - `SECRET_KEY` = generer avec `django.core.management.utils.get_random_secret_key()` et stocker dans `.env`
  - `DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"` (defaut False)
  - `ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "your-domain.com").split(",")`

**Action 3 : Refaire l'auth frontend**
- Fichier : `apps/frontend/app/api/auth/login/route.ts`
  - Remplacer le cookie base64 par un JWT signe (HS256 avec secret serveur)
  - Cookie : `httpOnly`, `secure`, `sameSite: strict`, `maxAge: 8h` (pas 30 jours)
  - Ajouter rate limiting : max 5 tentatives / minute par IP

**Action 4 : Supprimer le token public**
- Remplacer `NEXT_PUBLIC_CONTROL_PLANE_TOKEN` par un token server-side :
  - Les appels API passent par un route handler Next.js (`/api/proxy/...`)
  - Le token reste cote serveur, jamais expose au navigateur
- Fichiers : `apps/frontend/lib/api.ts`, nouveau `apps/frontend/app/api/proxy/[...path]/route.ts`

### P0 — Hardening immediat

**Action 5 : Security headers nginx**
- Fichier : `deploy/nginx/bot-proxy.conf`
  ```nginx
  add_header X-Frame-Options "DENY" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-XSS-Protection "1; mode=block" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';" always;
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  ```

**Action 6 : Rate limiting nginx**
- Fichier : `deploy/nginx/bot-proxy.conf`
  ```nginx
  limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
  limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/m;

  location /api/v1/ {
      limit_req zone=api burst=10 nodelay;
      ...
  }
  location /api/auth/ {
      limit_req zone=auth burst=3 nodelay;
      ...
  }
  ```

### P1 — Securite applicative

**Action 7 : Valider les bornes d'ordres**
- Fichier : `services/worker/executor/client.py`
  - Dans `place_limit_order()`, ajouter avant l'appel CLOB :
  ```python
  if not (0.01 <= price <= 0.99):
      raise ValueError(f"Price {price} out of bounds [0.01, 0.99]")
  if size <= 0:
      raise ValueError(f"Size {size} must be positive")
  ```

**Action 8 : Ne pas bypass le balance check**
- Fichier : `services/worker/executor/client.py:152-160`
  - Si le RPC echoue, REFUSER l'ordre (pas le laisser passer)
  ```python
  balance = await self.get_usdc_balance()
  if balance is None:
      logger.error("Cannot verify balance — order rejected")
      return None  # Refuse l'ordre
  ```

**Action 9 : Borner les messages utilisateur**
- Fichier : `services/worker/conversation/router.py`
  ```python
  MAX_MESSAGE_LENGTH = 4000
  if len(user_message) > MAX_MESSAGE_LENGTH:
      return "Message trop long (max 4000 caracteres)."
  ```

**Action 10 : Signer les commandes bridge**
- Fichier : `services/worker/bridge.py` + `apps/backend/core/views.py`
  - Ajouter un HMAC-SHA256 sur chaque requete bridge :
  ```python
  import hmac, hashlib, time
  timestamp = str(int(time.time()))
  signature = hmac.new(token.encode(), f"{timestamp}:{body}".encode(), hashlib.sha256).hexdigest()
  headers["X-Bridge-Timestamp"] = timestamp
  headers["X-Bridge-Signature"] = signature
  ```
  - Cote backend, verifier signature et rejeter si timestamp > 60s

### P1 — Securiser le developer hook

**Action 11 : Sandboxer le hook**
- Fichier : `services/worker/main.py:346-404`
  - Whitelist de commandes autorisees (pas d'execution arbitraire)
  - Timeout reduit a 60s (pas 1800s)
  - Ou supprimer completement cette feature si non necessaire

### P2 — Scan de dependances

**Action 12 : Ajouter pip-audit au CI**
- Fichier : `.github/workflows/ci.yml`
  ```yaml
  - name: Security scan
    run: |
      pip install pip-audit
      pip-audit -r requirements.txt
  ```

**Action 13 : Dependabot**
- Fichier : `.github/dependabot.yml`
  ```yaml
  version: 2
  updates:
    - package-ecosystem: "pip"
      directory: "/services/worker"
      schedule:
        interval: "weekly"
    - package-ecosystem: "npm"
      directory: "/apps/frontend"
      schedule:
        interval: "weekly"
  ```

## Criteres de validation

- [ ] Aucun secret en clair dans le code (scan `detect-secrets`)
- [ ] Django DEBUG=False, SECRET_KEY aleatoire, ALLOWED_HOSTS restrictif
- [ ] Cookie session httpOnly + secure + signe (JWT)
- [ ] Aucun token expose dans le bundle JS client
- [ ] Security headers presents (tester avec securityheaders.com)
- [ ] Rate limiting actif (tester avec `ab` ou `wrk`)
- [ ] Ordres valides avant envoi au CLOB (prix 0.01-0.99, size > 0)
- [ ] Balance check obligatoire (pas de bypass)
- [ ] Bridge signe par HMAC
- [ ] pip-audit clean (0 vulnerabilites connues)
- [ ] Developer hook sandboxe ou supprime

## Regles

- Jamais de secret dans le code, les logs, ou les messages d'erreur
- Tout input externe est hostile : valider, borner, echapper
- Principe du moindre privilege : chaque composant n'a acces qu'a ce dont il a besoin
- Defense en profondeur : plusieurs couches de protection
- En cas de doute, refuser (fail closed, pas fail open)
