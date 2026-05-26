web: python manage.py migrate --noinput && python manage.py setup_initial_data && python manage.py collectstatic --noinput && gunicorn project_tracker.wsgi --workers 2 --timeout 120
