# IndiaMart Lead Monitor

A serverless monitoring system for IndiaMart leads, deploying easily to Vercel.

## Deployment to Vercel

### 1. Requirements
- A [Vercel](https://vercel.com) account.
- A [Vercel KV](https://vercel.com/docs/storage/vercel-kv) (Redis) database.
- An [ntfy.sh](https://ntfy.sh) topic name.

### 2. Environment Variables
Configure the following in your Vercel Project Settings > Environment Variables:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `REDIS_URL` | Your Upstash Redis URL | `rediss://default:...@...` |
| `NTFY_TOPIC` | Notification topic on ntfy.sh | `indiamart_leads_123` |
| `INDIAMART_COOKIE` | IndiaMart session cookie | `IPL=...; PHPSESSID=...` |
| `CRON_SECRET` | Secret key for cron-job.org | `my_random_secret_123` |

### 3. Setup cron-job.org
To automate scans without Vercel Crons:
1.  Create a free account at [cron-job.org](https://cron-job.org).
2.  Create a new job:
    *   **URL**: `https://your-app.vercel.app/api/cron?secret=YOUR_CRON_SECRET` (Replace with your actual URL and secret).
    *   **Schedule**: Every 10 minutes.
3.  Ensure your `CRON_SECRET` in Vercel matches the one in the URL.

## Developing Locally
1. Install dependencies: `pip install -r requirements.txt`
2. Run the local server: `python run_local.py`
3. Open `http://localhost:5000` in your browser.
