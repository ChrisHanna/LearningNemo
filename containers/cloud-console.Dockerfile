FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY containers/cloud-console.requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY src/task_agent/__init__.py /app/src/task_agent/__init__.py
COPY src/task_agent/console /app/src/task_agent/console
COPY src/task_agent/security/__init__.py /app/src/task_agent/security/__init__.py
COPY src/task_agent/security/jwt_claims.py /app/src/task_agent/security/jwt_claims.py
COPY src/task_agent/security/policies.py /app/src/task_agent/security/policies.py
COPY docs/guides/build-and-reproduce.md docs/guides/capability-demo.md /app/docs/guides/
COPY docs/archive/openshell-bootstrap-diagnostic-record.md /app/docs/archive/
RUN chmod -R a+rX /app
USER 65532:65532
RUN python -c "import task_agent.console.cloud; import task_agent.console.cloud_controller"
EXPOSE 8080
CMD ["python", "-m", "task_agent.console.cloud"]