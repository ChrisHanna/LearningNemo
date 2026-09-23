"""Load only the APIM client credential via managed identity, then start NeMo."""

import os
import tempfile
from urllib.parse import urlsplit

import httpx
import yaml
from azure.identity import ManagedIdentityCredential


def main() -> None:
    vault = os.environ["LEARNINGNEMO_GATEWAY_VAULT"]
    if not vault.isalnum():
        raise ValueError("Expected a Key Vault name")
    for name in ("OPENAI_BASE_URL", "OPENAI_GUARDRAIL_BASE_URL"):
        parsed = urlsplit(os.environ[name])
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".azure-api.net"):
            raise ValueError("Model calls must use the Azure API Management gateway")
    credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    token = credential.get_token("https://vault.azure.net/.default").token
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(
            f"https://{vault}.vault.azure.net/secrets/llm-gateway-client-key",
            params={"api-version": "7.4"}, headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise RuntimeError("Unable to load internal APIM credential via managed identity")
    os.environ["OPENAI_API_KEY"] = response.json()["value"]
    credential.close()
    with open("/app/configs/agent.yml", encoding="utf-8") as source:
        config = yaml.safe_load(source)
    config["general"]["front_end"]["host"] = "0.0.0.0"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as destination:
        yaml.safe_dump(config, destination)
        path = destination.name
    os.execvp("nat", ["nat", "serve", "--config_file", path])


if __name__ == "__main__":
    main()