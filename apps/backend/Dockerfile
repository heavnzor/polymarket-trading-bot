FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/backend /app

EXPOSE 8000

CMD ["sh", "-c", "until python manage.py migrate --noinput; do echo 'Waiting for PostgreSQL...'; sleep 2; done; python manage.py collectstatic --noinput || true; daphne -b 0.0.0.0 -p 8000 control_plane.asgi:application"]
