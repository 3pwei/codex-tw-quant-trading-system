FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY tw_quant ./tw_quant
COPY data ./data
RUN python -m pip install --no-cache-dir ".[server,shioaji]"

EXPOSE 8000
CMD ["uvicorn", "tw_quant.live.api:app", "--host", "0.0.0.0", "--port", "8000"]
