FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir "mcp[cli]"

FROM python:3.12-slim AS production
WORKDIR /app

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=base /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=base /app/src /app/src
COPY --from=base /app/pyproject.toml /app/

# Writable scratch dir for GPX downloads. KOMOOT_DATA_DIR controls
# where tools save default-named files; align it with this path.
RUN mkdir -p /tmp/komoot && chown nobody:nogroup /tmp/komoot

# /app must be readable by the unprivileged user; mark it owned by
# nobody so future code paths that want to write here also work.
RUN chown -R nobody:nogroup /app

ENV PYTHONUNBUFFERED=1
ENV KOMOOT_DATA_DIR=/tmp/komoot

USER nobody
EXPOSE 3007
CMD ["python", "-m", "komoot_mcp.server", "--transport", "http", "--port", "3007", "--host", "0.0.0.0"]
