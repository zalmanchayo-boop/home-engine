# Deploy to Render (Blueprint)
1. Push this repo to a private GitHub repo.
2. Render Dashboard > New > Blueprint > pick the repo > Apply (uses render.yaml: Docker, $7/mo 0.5c-512mb, 1 GB disk at /var/data).
3. Enter NATIONAL_RATE_FEED_URL when prompted (or leave blank).
4. ADMIN_PASSWORD is auto-generated: Service > Environment to reveal it. Admin at /admin.
Local: `./run.sh` (data in ./data, uploads in ./data/uploads).
