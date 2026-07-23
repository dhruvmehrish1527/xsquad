FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed ./seed

# SQLite location: a Fly.io volume mounts here for persistence; on ephemeral
# hosts (HF Spaces) it's a plain dir and app/seed.py restores state on boot.
# chmod 777: HF Spaces runs the container as uid 1000, not root.
RUN mkdir -p /data && chmod 777 /data
ENV XSQUAD_DB=/data/fpl_optimizer.db

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
