FROM crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-console@sha256:a87ee3b7ade4af17af1e2a49c3d4676a9d5dca3a42b0cb43261f5b5a3d4b8443
USER root
COPY src/task_agent/console/app.py /app/src/task_agent/console/app.py
COPY src/task_agent/console/cloud.py /app/src/task_agent/console/cloud.py
COPY src/task_agent/console/public_demo_auth.py /app/src/task_agent/console/public_demo_auth.py
COPY src/task_agent/console/remote_invoice.py /app/src/task_agent/console/remote_invoice.py
COPY src/task_agent/console/static/index.html /app/src/task_agent/console/static/index.html
COPY src/task_agent/console/static/invoice-view.js /app/src/task_agent/console/static/invoice-view.js
COPY src/task_agent/console/static/invoice-scene.js /app/src/task_agent/console/static/invoice-scene.js
COPY src/task_agent/console/static/invoice-evidence.js /app/src/task_agent/console/static/invoice-evidence.js
COPY src/task_agent/console/static/invoice-experience.js /app/src/task_agent/console/static/invoice-experience.js
COPY src/task_agent/console/static/invoice-workflow.js /app/src/task_agent/console/static/invoice-workflow.js
COPY src/task_agent/console/static/learningnemo.js /app/src/task_agent/console/static/learningnemo.js
COPY src/task_agent/console/static/live-workspace.js /app/src/task_agent/console/static/live-workspace.js
COPY src/task_agent/console/static/mission.js /app/src/task_agent/console/static/mission.js
COPY src/task_agent/console/static/mission-flow.js /app/src/task_agent/console/static/mission-flow.js
COPY src/task_agent/console/static/pattern.js /app/src/task_agent/console/static/pattern.js
COPY src/task_agent/console/static/invoice.css /app/src/task_agent/console/static/invoice.css
COPY src/task_agent/console/static/openai-theme.css /app/src/task_agent/console/static/openai-theme.css
COPY src/task_agent/console/static/session.js /app/src/task_agent/console/static/session.js
RUN chmod 0444 \
    /app/src/task_agent/console/app.py \
    /app/src/task_agent/console/cloud.py \
    /app/src/task_agent/console/public_demo_auth.py \
    /app/src/task_agent/console/remote_invoice.py \
    /app/src/task_agent/console/static/index.html \
    /app/src/task_agent/console/static/invoice-view.js \
    /app/src/task_agent/console/static/invoice-scene.js \
    /app/src/task_agent/console/static/invoice-evidence.js \
    /app/src/task_agent/console/static/invoice-experience.js \
    /app/src/task_agent/console/static/invoice-workflow.js \
    /app/src/task_agent/console/static/learningnemo.js \
    /app/src/task_agent/console/static/live-workspace.js \
    /app/src/task_agent/console/static/mission.js \
    /app/src/task_agent/console/static/mission-flow.js \
    /app/src/task_agent/console/static/pattern.js \
    /app/src/task_agent/console/static/invoice.css \
    /app/src/task_agent/console/static/openai-theme.css \
    /app/src/task_agent/console/static/session.js
USER 65532:65532
