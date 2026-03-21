# GitHub Auto-Deploy (Free + Easy)

This guide sets up automatic deployment whenever you push to GitHub. The recommended free and easy cloud host is **Render** (supports Flask, GitHub hooks and a free tier).

## 1. Create GitHub repository
1. Initialize git in local folder (already present) and commit all files.
2. Create repo on GitHub and push.

## 2. Create Render service
1. Sign up at https://render.com and confirm email.
2. Connect GitHub account and select your repository.
3. Choose **Web Service**.
4. Set:
   - Name: `pollution-monitoring` (or any value)
   - Branch: `main` (or your default branch)
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app` (or `python app.py` if no WSGI)
   - Region: nearest
   - Instance: `free` (if offered)
5. Create service and wait for first deploy quality check.

Render auto-deploys on every push to the branch, so deployment is already automatic and free.

## 3. Optional: GitHub Actions + Render API (CI workflow)
Use this if you want explicit action-based deploys (advantage: transparent deploy logs in GitHub actions):

1. Add GitHub secret: 
   - `RENDER_API_KEY` with your Render API key (`Account -> API Keys`).
2. Create folder `.github/workflows` in repo root (if not existing).
3. Add file `.github/workflows/deploy-render.yml`:

```yaml
name: Deploy to Render
on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install dependencies
        run: pip install requests
      - name: Deploy to Render via API
        env:
          RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}
          RENDER_SERVICE_ID: your-render-service-id-here
        run: |
          curl -X POST \
            -H "Accept: application/json" \
            -H "Authorization: Bearer $RENDER_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{"type":"deploy"}' \
            "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys"
```

4. Replace `your-render-service-id-here` with the service ID from render dashboard (service URL path or service settings).

## 4. Use GitHub Actions only (no Render)
If you prefer a completely GitHub-hosted automated preview, you can use **Railway** or **Fly.io** with their GitHub integrations as well, similarly by connecting repo and setting environment variables.

## 5. QA
- Make a small commit and push.
- Verify Render logs show success and app is live.
- Access service URL from Render and check map endpoint.

## 6. Notes
- If you are not using Render, ensure the cloud provider supports Python Flask apps and auto-deploy from GitHub.
- For a static-only version, GitHub Pages doesn’t support Flask backend.

## 7. Quick one-line on your side
`git push origin main` triggers Render auto-deploy on every push once integration is set up.
