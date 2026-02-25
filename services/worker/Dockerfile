FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/root-requirements.txt
COPY services/worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir -r /tmp/root-requirements.txt -r /tmp/worker-requirements.txt

COPY . /app

CMD ["python", "services/worker/bridge.py"]
