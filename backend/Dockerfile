# Builds the ClueStack FastAPI backend container with kubectl for cluster mode.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN addgroup --system infrawatch \
    && adduser --system --ingroup infrawatch infrawatch \
    && mkdir -p /tmp/infrawatch /data \
    && chown -R infrawatch:infrawatch /app /tmp/infrawatch /data

EXPOSE 8000
USER infrawatch
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
