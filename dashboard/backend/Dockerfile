# qsdash API / ws hub / engine runner — one image, three commands.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# engine first (its deps are the heavy layer and change rarely)
COPY pyproject.toml README.md /app/engine/
COPY src /app/engine/src
COPY config /app/engine/config
RUN pip install "/app/engine[broker]"

COPY dashboard/backend /app/backend
# pyarrow: parquet IO for the research monitors (down-shock daily tracker),
# baked in so the timer job does not pip-install on every run
RUN pip install /app/backend pyarrow

# default: API. compose overrides command for ws-worker / engine.
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://127.0.0.1:8000/api/health', timeout=4).raise_for_status()"
CMD ["uvicorn", "qsdash.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
