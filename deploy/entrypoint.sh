#!/bin/sh
# web           migrate, then serve with gunicorn (default)
# jobs <names>  run scheduled jobs, e.g. "jobs nightly" or "jobs fetch_un_list"
# manage <args> any manage.py command, e.g. "manage createsuperuser"
set -e
case "${1:-web}" in
  web)
    python manage.py migrate --noinput
    python manage.py createcachetable
    exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" --timeout 120 \
         --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" --access-logfile - ;;
  jobs)
    shift
    exec python manage.py runjobs "$@" ;;
  manage)
    shift
    exec python manage.py "$@" ;;
  *)
    exec "$@" ;;
esac
