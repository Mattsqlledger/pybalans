# PyBalans webapp. Bouwen: docker build -t pybalans .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# WeasyPrint (PDF-documenten, fase 4) heeft Pango en lettertypen nodig; pg_dump voor Back-up maken (6d-1)
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 \
      libharfbuzz0b fonts-dejavu-core fonts-liberation2 postgresql-client && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY sqlledger ./sqlledger
COPY sqlledger_web ./sqlledger_web
RUN pip install ".[web]" "psycopg[binary]>=3.1" \
 && useradd --system --uid 10001 --home /app sqlledger \
 && mkdir -p /data && chown sqlledger /data
# datamap (geüploade back-ups, bijlagen, sjablonen, sleutel): eigen map, in compose een volume; /app is van root
ENV SQLLEDGER_DATA_DIR=/data
USER sqlledger
EXPOSE 8000
# achter een https-proxy: --proxy zet X-Forwarded-For/-Proto aan (stel --forwarded-allow-ips in)
CMD ["pybalans", "serve", "--host", "0.0.0.0", "--port", "8000"]
