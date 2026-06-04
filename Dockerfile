FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Cloud-only image — the local Parakeet provider is not bundled (it needs a
# separate Python 3.13 + NeMo + GPU environment).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg is only needed for the local provider; omitted from this cloud image.

# Create non-root user
RUN useradd -m -u 1000 appuser

COPY --chown=appuser:appuser server.py transcribe_cli.py ./
COPY --chown=appuser:appuser static/ static/

USER appuser

EXPOSE 8700

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8700/health')"

CMD ["python", "server.py"]
