# Project Tracker

A Django web app for tracking projects across your team.

## Features
- Register / login with full name or email
- Dark & light mode (persists across sessions)
- Dashboard with sortable columns, search, status & priority filters
- Traffic light system: 🟢 30+ days · 🟡 15–30 days · 🔴 ≤15 days · ⚫ TBC
- Slide-in edit panel with full project details
- Installation date: exact date OR month (Early/Mid/End/Any)
- "Same as delivery date" shortcut with automatic sync
- Date validation: installation cannot be before delivery
- Customer autocomplete (builds itself from saved projects)
- Per-project change log + global Activity Log page
- Outfit font throughout

## Quick Start

```bash
cd project_tracker
python -m venv venv
# Windows: venv\Scripts\activate  |  Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/register/ to create the first account.

## Deploy to Railway

1. Push to GitHub
2. New Railway project → Deploy from GitHub
3. Set env vars: SECRET_KEY, DEBUG=False, ALLOWED_HOSTS=yourapp.railway.app
4. Run: python manage.py migrate && python manage.py createsuperuser
