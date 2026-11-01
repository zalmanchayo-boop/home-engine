# Deploy notes
Local: `./run.sh` (reads .env, port 8765). Admin at /admin, password = ADMIN_PASSWORD in .env.

## Fastest temporary (today): Cloudflare quick tunnel, no account
`~/bin/cloudflared tunnel --url http://localhost:8765` -> prints https://<random>.trycloudflare.com.
Temporary: URL changes on restart, no uptime guarantee, box must stay on. Fine for a soft preview, not ideal for a public launch link.

## Durable (recommended for launch): Render (needs Zalman's account)
1. Push this folder to a private GitHub repo (exclude .env, .venv, data/*.db).
2. Render -> New -> Blueprint -> pick repo (uses render.yaml + Dockerfile). Starter plan (~$7/mo) + 5GB disk for uploads.
3. Set ADMIN_PASSWORD in Render env. NOTE: SQLite DB lives in ./data inside the container; for persistence point it at the disk
   (move data/ to /data or symlink) before real traffic. Uploads already go to /data/uploads.
4. Add custom domain (e.g. calc.yourdomain.com) via CNAME.
Alternatives: Railway (`railway up`, add a volume at /data) or Fly.io (`fly launch`, `fly volumes create data`). Same Dockerfile.

## Before public launch
- Compliance review of disclaimers/consent (TCPA, GLBA privacy notice, NMLS display, Equal Housing logo, state ad rules).
- HTTPS is required (tunnel/Render provide it). Set cookie secure=True in v1.py admin_login once on HTTPS-only.
- National rate feed: set NATIONAL_RATE_FEED_URL returning {"rate": x, "source": "..."} or adapt v1.fetch_national().
