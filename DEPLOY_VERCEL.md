# Deploy Smart Home to Vercel + MongoDB Atlas

## 1. MongoDB Atlas (free)

1. Go to [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas) and create a free account.
2. Create a **free M0 cluster**.
3. **Database Access** → Add user (username + password). Save the password.
4. **Network Access** → Add IP **0.0.0.0/0** (allow from anywhere — required for Vercel).
5. **Connect** → Drivers → copy the connection string, e.g.:
   ```
   mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/smart_home?retryWrites=true&w=majority
   ```
   Replace `USER`, `PASSWORD`, and set database name to `smart_home`.

## 2. Vercel deploy

1. Go to [vercel.com](https://vercel.com) and sign in with **GitHub**.
2. **Add New Project** → import **rohithkumar505/smart-home**.
3. Framework Preset: **Other** (Vercel auto-detects Flask from `api/index.py`).
4. Add **Environment Variables**:

   | Name | Value |
   |------|--------|
   | `MONGODB_URI` | Your Atlas connection string |
   | `FLASK_SECRET_KEY` | Random long string (e.g. `openssl rand -hex 32`) |
   | `CRON_SECRET` | Random string for schedule cron auth |
   | `FLASK_ENV` | `production` |

5. Click **Deploy**. Wait 2–3 minutes.

## 3. After deploy

- Open your Vercel URL: `https://your-project.vercel.app`
- Login: **admin** / **admin**
- AI assistant robot is bottom-right on mobile and desktop.

## 4. Schedules & automation on Vercel

Vercel has no always-on server. A **cron job** runs every minute at `/api/cron/tick` to fire schedules and automation rules.

Optional: set `CRON_SECRET` in Vercel and add the same value in Vercel Cron authorization if you lock the endpoint down.

## 5. Local dev (still works without MongoDB)

```bash
cd ~/Desktop/smart_home
python3 app.py
```

Uses SQLite locally. To test MongoDB locally:

```bash
export MONGODB_URI="mongodb+srv://..."
python3 app.py
```

## 6. Push updates

```bash
cd ~/Desktop/smart_home
git add .
git commit -m "your message"
git push origin main
```

Vercel redeploys automatically from GitHub.
