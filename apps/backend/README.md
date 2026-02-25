# Control Plane Backend

Django 6 control-plane with DRF, Channels and Celery.

## Quickstart

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_control_plane --username admin --email admin@example.com --password admin
python manage.py runserver 0.0.0.0:8000
```

For websocket support in production, run ASGI (`daphne` or `uvicorn`) instead of Django dev server.
