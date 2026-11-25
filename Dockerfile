FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 appuser
WORKDIR /home/appuser/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser app ./app
COPY --chown=appuser static ./static
USER appuser

EXPOSE 7860
# $PORT is set by Render/Railway; Hugging Face uses 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
