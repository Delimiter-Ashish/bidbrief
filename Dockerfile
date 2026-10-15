# For Hugging Face Spaces (Docker SDK) or any container host.
# HF Spaces: create a Docker Space, push this repo, add GEMINI_API_KEY as a Space secret.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr ghostscript libgl1 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app
WORKDIR /home/app/bidbrief
COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
USER app

ENV BIDBRIEF_MODEL_CACHE=/home/app/.cache/bidbrief \
    PYTHONUNBUFFERED=1
# Download the small search models into the image so the demo starts fast
RUN python -m bidbrief doctor --download || true

EXPOSE 7860
CMD ["python", "-m", "bidbrief", "serve", "--port", "7860"]
