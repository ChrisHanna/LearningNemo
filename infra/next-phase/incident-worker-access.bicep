targetScope = 'resourceGroup'

param workers array

resource apps 'Microsoft.App/containerApps@2025-01-01' = [for worker in workers: {
  name: worker.name
  location: worker.location
  tags: worker.tags
  identity: worker.identity
  properties: worker.properties
}]

resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = [for (worker, index) in workers: {
  name: '${apps[index].name}/current'
  properties: worker.auth
}]