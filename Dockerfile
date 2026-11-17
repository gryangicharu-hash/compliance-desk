# Compliance Desk application image. Build from the repository root:
#   docker build -t compliance-desk .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /srv/compliance-desk
COPY pyproject.toml README.md ./
COPY src ./src
COPY web/requirements.txt web/requirements.txt
RUN pip install . -r web/requirements.txt
COPY tests/fixtures ./tests/fixtures
COPY web ./web
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh

WORKDIR /srv/compliance-desk/web
# Static files are collected at build time; placeholder settings only (no secrets in the image).
RUN DJANGO_SECRET_KEY=build-only FIELD_ENCRYPTION_KEYS=build-only BLIND_INDEX_KEY=build-only \
    python manage.py collectstatic --noinput \
    && mkdir -p /srv/inbox /srv/outbox /srv/documents && chown app:app /srv/inbox /srv/outbox /srv/documents

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status != 200)"
ENTRYPOINT ["/srv/compliance-desk/deploy/entrypoint.sh"]
CMD ["web"]
