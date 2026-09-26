ARG NAT_IMAGE
FROM ${NAT_IMAGE} AS runtime
FROM ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:c2a43bb0d765774e2790b3babfb20997bb2eac7b4bf4c6d7d8661e99817bf904
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=runtime /usr/local /usr/local
COPY --from=runtime /opt/venv /opt/venv
COPY src/task_agent /opt/venv/lib/python3.12/site-packages/task_agent
COPY configs/invoice-planning.yml configs/invoice-execution.yml /app/configs/
RUN chmod -R a+rX /app /opt/venv && /opt/venv/bin/python -c "import nat; import task_agent.control.invoice_agent"
# The invoice OpenShell policies use Landlock hard_requirement: every listed path must exist.
RUN for path in /usr /bin /lib /lib64 /etc /opt/venv /app /var/log /proc /dev/urandom /tmp /usr/local/bin/python3.12; do test -e "$path"; done
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NAT_TELEMETRY_ENABLED=false HOME=/tmp
WORKDIR /tmp
USER sandbox
ENTRYPOINT []
CMD ["/bin/sleep", "infinity"]