targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param platformResourceGroupName string
param containerAppsEnvironmentName string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

param databaseName string

@secure()
param serviceApplicationIds object

@secure()
@minLength(64)
@maxLength(64)
param approverHash string

@secure()
@minLength(64)
@maxLength(64)
param operatorHash string

param tags object

resource managedEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: containerAppsEnvironmentName
}

resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-control-${environment}'
}

resource diagnosticIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-diagnostic-${environment}'
}

resource diagnosticApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-diagnostic-${environment}'
}

resource queryRunnerApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-query-runner-${environment}'
}

resource remediationApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-remediation-${environment}'
}

resource verifierApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-verifier-${environment}'
}

var workerEndpoints = {
  diagnostic: diagnosticApp.properties.configuration.ingress.fqdn
  queryRunner: queryRunnerApp.properties.configuration.ingress.fqdn
  remediation: remediationApp.properties.configuration.ingress.fqdn
  verifier: verifierApp.properties.configuration.ingress.fqdn
}

resource controlJob 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${projectName}-cycle-${environment}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${controlIdentity.id}': {}
      '${diagnosticIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      identitySettings: [
        {
          identity: controlIdentity.id
          lifecycle: 'Main'
        }
      ]
      registries: [
        {
          server: registryServer
          identity: diagnosticIdentity.id
        }
      ]
      secrets: []
    }
    template: {
      containers: [
        {
          name: 'control-cycle'
          image: imageReference
          command: [
            '/usr/local/bin/python3'
          ]
          args: [
            '/opt/learningnemo/scripts/run-live-cycle-workflow.py'
          ]
          env: [
            {
              name: 'LEARNINGNEMO_WORKER_ENDPOINTS'
              value: string(workerEndpoints)
            }
            {
              name: 'LEARNINGNEMO_WORKER_AUDIENCES'
              value: string(serviceApplicationIds)
            }
            {
              name: 'LEARNINGNEMO_SQL_SERVER'
              value: sqlServerHostname
            }
            {
              name: 'LEARNINGNEMO_SQL_DATABASE'
              value: databaseName
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: controlIdentity.properties.clientId
            }
            {
              name: 'LEARNINGNEMO_APPROVER_HASH'
              value: approverHash
            }
            {
              name: 'LEARNINGNEMO_OPERATOR_HASH'
              value: operatorHash
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output controlJobName string = controlJob.name
output controlIdentityName string = controlIdentity.name
output registryIdentityName string = diagnosticIdentity.name