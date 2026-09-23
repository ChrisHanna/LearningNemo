targetScope = 'resourceGroup'

param environmentId string
param image string
param expiresAt string

resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-learningnemo-human-mig-dev'
  location: 'eastus'
  tags: {
    owner: 'learningnemo-portfolio'
    project: 'learningnemo'
    purpose: 'retained-disabled-human-migration'
    expiresAt: expiresAt
  }
  identity: { type: 'None' }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 60
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      identitySettings: []
      registries: []
      secrets: []
    }
    template: {
      containers: [{
        name: 'migrator'
        image: image
        command: ['python', '-c', 'raise SystemExit("retained migration job disabled")']
        env: []
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}