FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgssapi-krb5-2 libltdl7 libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY containers/human-services.requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY src/task_agent/__init__.py /app/src/task_agent/__init__.py
COPY src/task_agent/control /app/src/task_agent/control
COPY src/task_agent/console /app/src/task_agent/console
COPY src/task_agent/security/__init__.py /app/src/task_agent/security/__init__.py
COPY src/task_agent/security/jwt_claims.py /app/src/task_agent/security/jwt_claims.py
COPY scripts/apply-human-migrations.py /app/scripts/apply-human-migrations.py
COPY scripts/verify-invoice-sql.py /app/scripts/verify-invoice-sql.py
COPY scripts/apply-invoice-migrations.py /app/scripts/apply-invoice-migrations.py
COPY infra/next-phase/review-service /app/migrations
RUN chmod -R a+rX /app
USER 65532:65532
RUN python -c "import mssql_python; from task_agent.console.incident_service import deployed_incident_app; from task_agent.console.review_service import deployed_review_app"
EXPOSE 8080
CMD ["python", "-m", "task_agent.console.incident_service"]