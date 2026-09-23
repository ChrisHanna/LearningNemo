FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY containers/cloud-agent.requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN /opt/venv/bin/pip install --no-deps --no-cache-dir /app

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NAT_TELEMETRY_ENABLED=false HOME=/tmp
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY configs/agent.yml /app/configs/agent.yml
COPY scripts/run-cloud-agent.py /app/scripts/run-cloud-agent.py
RUN chmod -R a+rX /app
USER 65532:65532
EXPOSE 8001
ENTRYPOINT ["python", "/app/scripts/run-cloud-agent.py"]