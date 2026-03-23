# Morning Digest — Setup Guide

## What you're deploying

A single Railway service that:
- Fetches 9 RSS feeds server-side (no CORS issues)
- Serves the PWA frontend
- Stores your thumbs up/down feedback in Postgres
- Gets smarter over time as Claude learns your taste

---

## Step 1 — Push to GitHub

Create a new GitHub repo and push this folder structure:

```
morning-digest/
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── railway.toml
└── frontend/
    ├── index.html
    └── manifest.json
```

---

## Step 2 — Deploy on Railway

1. Go to **railway.app** → New Project → Deploy from GitHub
2. Select your repo
3. Railway will detect the `railway.toml` and deploy automatically
4. Once deployed, go to **Settings → Domains** → Generate a domain
   - You'll get something like `morning-digest-production.up.railway.app`

---

## Step 3 — Add Postgres

1. In your Railway project, click **+ New** → Database → PostgreSQL
2. Once provisioned, Railway automatically sets `DATABASE_URL` in your service's environment
3. Redeploy (or it picks it up automatically) — the app creates tables on first boot

---

## Step 4 — Update the frontend URL

Open `frontend/index.html` and find this line near the top of the `<script>` tag:

```js
const API_BASE = window.location.origin.includes("localhost") || window.location.origin.includes("railway")
  ? window.location.origin
  : "REPLACE_WITH_YOUR_RAILWAY_URL";
```

Since you're serving the frontend FROM Railway, `window.location.origin` will automatically
be your Railway URL — **you don't need to change anything**. The app just works.

---

## Step 5 — Add to iPhone home screen

1. Open your Railway URL in **Safari** on your iPhone
   (must be Safari — Chrome doesn't support PWA install on iOS)
2. Tap the **Share** button (box with arrow at bottom of screen)
3. Scroll down → **"Add to Home Screen"**
4. Name it "Brief" → **Add**

It will appear as a full-screen app with no browser chrome.

---

## How the feedback learning works

Every time you tap 👍 or 👎 on an article, it's saved to Postgres.

The next time you build a brief, Claude receives your last ~15 liked and ~15 disliked articles
as context in its ranking prompt. It uses this to weight selections toward what you've responded
to and away from what you haven't.

After ~20-30 ratings, you'll notice the picks getting noticeably better calibrated to your taste.

---

## Optional: Add a home screen icon

Railway will use the browser's default favicon for the home screen icon unless you add real icons.
To add a proper icon:

1. Create a 512×512 PNG image (your initials, a ☕, whatever)
2. Save as `frontend/icon-512.png` and `frontend/icon-192.png`
3. Push to GitHub — Railway redeploys automatically

---

## Troubleshooting

**"Couldn't reach the server"** → Check Railway logs. Usually a missing dependency or startup error.

**Some feeds show ✗** → Normal. A few RSS feeds block certain user agents. The others will still
give you plenty of articles for Claude to pick from.

**Feedback not persisting** → Make sure Postgres is provisioned and `DATABASE_URL` is set
in Railway environment variables.

**App doesn't update after pushing** → Railway auto-deploys on push. Check the deployment
tab in Railway to see if it's building.
