ARG INVOICE_AGENT_IMAGE
FROM ${INVOICE_AGENT_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgssapi-krb5-2 libltdl7 libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY containers/cloud-console.requirements.lock /tmp/invoice-controller.requirements.lock
RUN /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/invoice-controller.requirements.lock
COPY src/task_agent /opt/venv/lib/python3.12/site-packages/task_agent
COPY infra/next-phase/openshell/invoice-planning-policy.yaml infra/next-phase/openshell/invoice-execution-policy.yaml infra/next-phase/openshell/invoice-planning-provider-profile.yaml infra/next-phase/openshell/invoice-execution-provider-profile.yaml /app/invoice-policies/
ENV PATH=/opt/venv/bin:$PATH
RUN chmod -R a+rX /app /opt/venv && python -c "import mssql_python; import azure.mgmt.compute; import task_agent.console.invoice_deployed"
USER 65532:65532
EXPOSE 8080
CMD ["python", "-m", "task_agent.console.invoice_deployed"]