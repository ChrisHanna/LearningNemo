targetScope = 'resourceGroup'

param expiresAt string
param workers array

resource apps 'Microsoft.App/containerApps@2025-01-01' existing = [for worker in workers: {
  name: worker.name
}]

resource leases 'Microsoft.Resources/tags@2021-04-01' = [for (worker, index) in workers: {
  name: 'default'
  scope: apps[index]
  properties: { tags: union(worker.tags, { expiresAt: expiresAt }) }
}]