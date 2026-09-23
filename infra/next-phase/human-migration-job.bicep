targetScope = 'resourceGroup'

param location string = 'eastus'
param environmentId string
param sqlAdminIdentityId string
param sqlAdminClientId string
param registryServer string
@secure()
param image string
param sqlServer string
param sqlDatabase string
param expiresAt string
param principals object
param invoiceProbeOnly bool = false
param invoiceApply bool = false
param invoiceVerifyOnly bool = false
param invoicePrincipals object = {}

resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-learningnemo-human-mig-dev'
  location: location
  tags: {
    project: 'learningnemo'
    owner: 'learningnemo-portfolio'
    disposable: 'true'
    expiresAt: expiresAt
    purpose: 'one-shot-human-schema-migration'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${sqlAdminIdentityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      identitySettings: [{ identity: sqlAdminIdentityId, lifecycle: 'Main' }]
      registries: [{ server: registryServer, identity: sqlAdminIdentityId }]
      secrets: []
    }
    template: {
      containers: [{
        name: 'migrator'
        image: image
        command: invoiceVerifyOnly ? ['python', '/app/scripts/apply-invoice-migrations.py', '--verify-only'] : ['python', invoiceApply ? '/app/scripts/apply-invoice-migrations.py' : invoiceProbeOnly ? '/app/scripts/verify-invoice-sql.py' : '/app/scripts/apply-human-migrations.py']
        env: [
          { name: 'AZURE_CLIENT_ID', value: sqlAdminClientId }
          { name: 'LEARNINGNEMO_SQL_SERVER', value: sqlServer }
          { name: 'LEARNINGNEMO_SQL_DATABASE', value: sqlDatabase }
          { name: 'LEARNINGNEMO_MIGRATION_EXPIRES_AT', value: expiresAt }
          { name: 'LEARNINGNEMO_HUMAN_PRINCIPALS', value: string(principals) }
          { name: 'LEARNINGNEMO_INVOICE_PRINCIPALS', value: string(invoicePrincipals) }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}