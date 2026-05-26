# Deploying EZR Project Tracker to Railway

## One-time setup (~15 minutes)

### 1. Create a Railway account
Go to https://railway.app and sign up (GitHub login is easiest)

### 2. Push code to GitHub
Railway deploys from GitHub. You need a (free) GitHub account.

In GitHub, create a new **private** repository called `ezr-project-tracker`

On your PC, open PowerShell in your project folder and run:
```
git init
git add .
git commit -m "Initial deploy"
git remote add origin https://github.com/YOUR_USERNAME/ezr-project-tracker.git
git push -u origin main
```

### 3. Create Railway project
1. Go to https://railway.app/new
2. Click "Deploy from GitHub repo"
3. Select your `ezr-project-tracker` repo
4. Click "Deploy Now"

### 4. Add PostgreSQL database
1. In your Railway project, click "+ New"
2. Select "Database" → "PostgreSQL"
3. Railway automatically sets the DATABASE_URL environment variable

### 5. Set environment variables
In Railway → your service → "Variables" tab, add:

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | Any long random string (e.g. from https://djecrety.ir) |
| `DEBUG` | `false` |
| `ALLOWED_HOSTS` | `your-app.up.railway.app` (Railway gives you this URL) |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.up.railway.app` |
| `EMAIL_HOST_USER` | your Gmail address |
| `EMAIL_HOST_PASSWORD` | your Gmail App Password |
| `DEFAULT_FROM_EMAIL` | `EZR Project Tracker <your@email.com>` |

### 6. Create admin user
Once deployed, open the Railway shell (service → Shell tab):
```
python manage.py createsuperuser
```

### 7. Import your data (optional)
To bring your existing projects/stock across:
- On your local PC: `python manage.py dumpdata > backup.json`
- Upload backup.json to Railway shell and run: `python manage.py loaddata backup.json`

### Updating the app
Every time you push to GitHub, Railway redeploys automatically:
```
git add .
git commit -m "Description of changes"
git push
```

## Costs
Railway free tier: $5 credit/month — enough for a small internal app
PostgreSQL: included in free tier
Estimated cost: £0–£3/month depending on usage

## Your app URL
Railway gives you a URL like: `https://ezr-project-tracker.up.railway.app`
You can add a custom domain later if needed.
