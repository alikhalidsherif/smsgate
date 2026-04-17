FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir huawei-lte-api flask flask-sock requests

WORKDIR /app
COPY gateway.py .

RUN mkdir -p /data

EXPOSE 5000

CMD ["python", "gateway.py"]
