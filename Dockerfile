FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir huawei-lte-api flask flask-sock requests gunicorn

WORKDIR /app
COPY gateway.py .
COPY wsgi.py .

RUN mkdir -p /data

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "120", "wsgi:app"]
