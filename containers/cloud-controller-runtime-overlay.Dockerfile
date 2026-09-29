FROM crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-console@sha256:2cc6634bac53ccd77bb350cf38216c79ec82dfd39f0a00211e23f606b454c1d8
USER root
COPY src/task_agent/console/live_workspace.py /app/src/task_agent/console/live_workspace.py
RUN chmod 0444 /app/src/task_agent/console/live_workspace.py
USER 65532:65532
