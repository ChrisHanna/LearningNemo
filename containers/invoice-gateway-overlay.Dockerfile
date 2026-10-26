ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0:0
COPY src/task_agent/control/invoice_model.py /tmp/invoice_model.py
COPY src/task_agent/control/invoice_rails.py /tmp/invoice_rails.py
COPY src/task_agent/console/invoice_gateway.py /tmp/invoice_gateway.py
COPY src/task_agent/control/invoice_contract.py /tmp/invoice_contract.py
COPY src/task_agent/control/invoice_repository.py /tmp/invoice_repository.py
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_model').origin); shutil.copyfile('/tmp/invoice_model.py',p); p.chmod(0o444); pathlib.Path('/tmp/invoice_model.py').unlink(); import task_agent.control.invoice_model"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_rails').origin); shutil.copyfile('/tmp/invoice_rails.py',p); p.chmod(0o444); pathlib.Path('/tmp/invoice_rails.py').unlink(); import task_agent.control.invoice_rails"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.console.invoice_gateway').origin); shutil.copyfile('/tmp/invoice_gateway.py',p); p.chmod(0o444); pathlib.Path('/tmp/invoice_gateway.py').unlink(); import task_agent.console.invoice_gateway"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_contract').origin); shutil.copyfile('/tmp/invoice_contract.py',p); p.chmod(0o444); pathlib.Path('/tmp/invoice_contract.py').unlink(); import task_agent.control.invoice_contract"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_repository').origin); shutil.copyfile('/tmp/invoice_repository.py',p); p.chmod(0o444); pathlib.Path('/tmp/invoice_repository.py').unlink(); from task_agent.control.invoice_repository import InvoiceRepository, VERIFICATION_FAILED"]
USER 65532:65532