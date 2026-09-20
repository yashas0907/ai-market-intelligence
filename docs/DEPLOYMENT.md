# Deployment (free tier)

## Options overview

| Tier | Backend | Frontend | Limits (free) |
|---|---|---|---|
| **Render** (recommended) | `render.yaml` blueprint → free web service | Vercel/Netlify static | sleeps after ~15 min idle (cold start ~30s); ephemeral disk (DB resets on redeploys — all data re-collectible from sources; watchlist resets) |
| Fly.io | free allowance w/ persistent volume | static | volume 1GB; card required at signup |
| Docker (self-host) | `docker compose up` | included (nginx) | your machine |

## Backend — Render

1. Push repo to GitHub.
2. Render dashboard → **New → Blueprint** → select the repo (Render reads `render.yaml`).
3. Set the `sync: false` env vars in the dashboard:
   - `LLM_PROVIDER=openai_compatible`
   - `LLM_MODEL=openai/gpt-oss-120b`
   - `LLM_BASE_URL=https://api.groq.com/openai/v1`
   - `LLM_API_KEY=<your Groq key — dashboard only, never committed>`
   - `CORS_ORIGINS=["https://<your-frontend>.vercel.app"]`
4. Deploy → note the URL: `https://market-intel-api.onrender.com`.

## Frontend — Vercel

1. Vercel → **New Project** → import repo → Root Directory `frontend`, Framework `Vite`.
2. Environment variable: `VITE_API_BASE=https://market-intel-api.onrender.com/api` (read at build time by `src/api.js`).
3. Deploy → open the URL. Add the Vercel URL to the backend's `CORS_ORIGINS` (step 3 above).

## Real-time notes

- Research progress streams via **SSE** (`GET /api/research/{id}/stream`) — push updates, no polling. Vercel's edge supports streaming; nginx config disables buffering (`proxy_buffering off`).
- Quote banner auto-refreshes every 60s; quotes may be delayed up to 15 minutes by the source — always stamped with retrieval time.
- Render free tier sleeps when idle; first request after sleep takes ~30s (this is a platform limit, not an app bug — health endpoint can be pinged to keep warm).

## Local git authorship (before pushing)

```bash
git config user.name "Yashas P Phatak"
git config user.email "<your GitHub email>"
git commit --amend --reset-author --no-edit   # if you want the latest commit re-attributed
```

## secrets policy

- API keys live ONLY in Render dashboard / local `.env` (git-ignored).
- `.env.example` documents every variable without real values.
