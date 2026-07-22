FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# The embedding model used to be baked in here at build time, but that
# needs outbound access to huggingface.co, which the corporate SSL-
# inspection proxy blocks from inside the build sandbox. It now lives in
# the hf-model-cache named volume (see docker-compose.yml) instead, seeded
# once via `docker cp` from a machine where the download succeeded — that
# volume persists independently of this image, so rebuilding here is safe.

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]