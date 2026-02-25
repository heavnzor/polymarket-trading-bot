# Specialists virtuels — Polymarket Bot v3

7 agents specialistes que Claude peut endosser pour ameliorer le bot.
Chaque fiche definit un mandat, un diagnostic, des actions prioritaires avec references fichiers, et des criteres de validation.

## Usage

Demander a Claude :
- "Agis en tant que [role] et traite les actions P0"
- "Endosse le role de quant et backtest la strategie CD"
- "Fais une revue securite en tant que security engineer"

## Roles disponibles

| # | Fichier | Role | Scope |
|---|---------|------|-------|
| 1 | `devops-sre.md` | DevOps / SRE | Backup, monitoring, CI/CD, infra |
| 2 | `security-engineer.md` | Security Engineer | Secrets, auth, hardening, audit |
| 3 | `quant.md` | Ingenieur Quantitatif | Modeles, backtest, calibration, parametres |
| 4 | `backend-senior.md` | Dev Backend Senior | Worker bugs, Django, perf, tests |
| 5 | `risk-manager.md` | Risk Manager | Stop-loss, VaR, reconciliation, limites |
| 6 | `frontend-dev.md` | Dev Frontend | UI/UX, charts, responsive, tests |
| 7 | `product-manager.md` | Product Manager | Roadmap, KPIs, priorisation, specs |

## Priorite de traitement

P0 (critique) → P1 (urgent) → P2 (important) → P3 (souhaitable)
