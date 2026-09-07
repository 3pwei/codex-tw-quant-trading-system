FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN python -m pip install --no-cache-dir "uv==0.11.33"
COPY pyproject.toml uv.lock README.md ./
COPY tw_quant ./tw_quant
COPY data ./data
RUN uv sync --locked --no-dev --extra server --extra shioaji --no-editable \
    && python -m pip uninstall --yes uv

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "tw_quant.live.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
