FROM crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-services@sha256:10a6e72e356b47dcf8e8ca9bca6fe4c1b11c3717ad464a0a062a5c53b1d96af1
USER 0:0
COPY src/task_agent/console/invoice_service.py /tmp/guest-invoice-service.py
COPY src/task_agent/control/invoice_repository.py /tmp/guest-invoice-repository.py
COPY src/task_agent/control/invoice_governance.py /tmp/guest-invoice-governance.py
COPY src/task_agent/control/invoice_contract.py /tmp/guest-invoice-contract.py
COPY src/task_agent/console/invoice_deployed.py /tmp/guest-invoice-deployed.py
# Resolve the installed module rather than assuming a Python-version-specific path.
# The contract is replaced first: the repository and the composition module import its new helpers.
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_contract').origin); shutil.copyfile('/tmp/guest-invoice-contract.py',p); p.chmod(0o444); pathlib.Path('/tmp/guest-invoice-contract.py').unlink(); import task_agent.control.invoice_contract"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.console.invoice_service').origin); shutil.copyfile('/tmp/guest-invoice-service.py',p); p.chmod(0o444); pathlib.Path('/tmp/guest-invoice-service.py').unlink(); import task_agent.console.invoice_service"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_repository').origin); shutil.copyfile('/tmp/guest-invoice-repository.py',p); p.chmod(0o444); pathlib.Path('/tmp/guest-invoice-repository.py').unlink(); import task_agent.control.invoice_repository"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.control.invoice_governance').origin); shutil.copyfile('/tmp/guest-invoice-governance.py',p); p.chmod(0o444); pathlib.Path('/tmp/guest-invoice-governance.py').unlink(); import task_agent.control.invoice_governance"]
RUN ["python3", "-c", "import importlib.util,pathlib,shutil; p=pathlib.Path(importlib.util.find_spec('task_agent.console.invoice_deployed').origin); shutil.copyfile('/tmp/guest-invoice-deployed.py',p); p.chmod(0o444); pathlib.Path('/tmp/guest-invoice-deployed.py').unlink(); import task_agent.console.invoice_deployed; from task_agent.control.invoice_repository import VERIFICATION_FAILED; from task_agent.control.invoice_contract import missing_operations"]
USER 65532:65532
