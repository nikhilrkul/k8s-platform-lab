# syntax=docker/dockerfile:1
# Multi-stage build: dependencies are installed in a builder, and only the
# virtualenv + code are copied into a small, non-root runtime image.
FROM python:3.13-slim AS builder
WORKDIR /build
RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.13-slim
# Pull in OS security fixes so the Trivy gate has fewer fixable CVEs to flag.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=builder /venv /venv
COPY app ./app
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
ENTRYPOINT ["uvicorn", "app.main:_app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
