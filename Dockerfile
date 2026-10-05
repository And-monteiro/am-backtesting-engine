FROM python:3.12-slim AS base
WORKDIR /app
ENV AM_STATE_ROOT=/state AM_CONFIG_ROOT=/app/config AM_DATA_ROOT=/data
COPY pyproject.toml ./
COPY am_backtesting ./am_backtesting
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home engine
COPY config ./config
COPY examples ./examples
RUN mkdir /state && chown engine:engine /state
USER engine
FROM base AS test
USER root
RUN pip install --no-cache-dir '.[test]'
COPY tests ./tests
USER engine
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM base AS runtime
EXPOSE 8000
CMD ["uvicorn", "am_backtesting.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
