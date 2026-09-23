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

param tags object

resource managedEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: containerAppsEnvironmentName
}

resource probeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-diagnostic-${environment}'
}

resource probeJob 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${projectName}-netprobe-${environment}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${probeIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 180
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registryServer
          identity: probeIdentity.id
        }
      ]
      secrets: []
    }
    template: {
      containers: [
        {
          name: 'probe'
          image: imageReference
          command: [
            '/usr/local/bin/python3'
          ]
          args: [
            '/opt/learningnemo/scripts/probe-sql-odbc.py'
          ]
          env: [
            {
              name: 'LEARNINGNEMO_SQL_SERVER'
              value: sqlServerHostname
            }
            {
              name: 'LEARNINGNEMO_SQL_DATABASE'
              value: 'master'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: probeIdentity.properties.clientId
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output probeIdentityName string = probeIdentity.name
output probeJobName string = probeJob.name