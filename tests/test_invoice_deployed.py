def test_deployed_service_module_imports():
    import task_agent.console.invoice_deployed as deployed
    assert deployed.DOMAIN=='jollybeach-503c7ed1.eastus.azurecontainerapps.io'