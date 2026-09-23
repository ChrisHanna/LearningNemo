targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param platformResourceGroupName string
param containerAppsEnvironmentName string
param databaseResourceGroupName string
param databaseName string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

@secure()
param migrationBundle string

param migrationBundleSha256 string
param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'one-shot-consumption-job'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-database-migration'
  disposable: 'true'
  expiresAt: expiresAt
  imageDigest: last(split(imageReference, '@sha256:'))
  migrationBundleHash: migrationBundleSha256
})

resource managedEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: containerAppsEnvironmentName
}

resource sqlAdminIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(databaseResourceGroupName)
  name: 'id-${projectName}-sql-admin-${environment}'
}

resource migrationJob 'Microsoft.App/jobs@2025-01-01' = {
  name: 'caj-${projectName}-migrate-${environment}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sqlAdminIdentity.id}': {}
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
          identity: sqlAdminIdentity.id
          lifecycle: 'Main'
        }
      ]
      registries: [
        {
          server: registryServer
          identity: sqlAdminIdentity.id
        }
      ]
      secrets: [
        {
          name: 'migration-bundle'
          value: migrationBundle
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrator'
          image: imageReference
          command: [
            'python3'
          ]
          args: [
            '/opt/learningnemo/scripts/apply-sql-migrations.py'
          ]
          env: [
            {
              name: 'LEARNINGNEMO_SQL_MIGRATION_BUNDLE'
              value: '/var/run/learningnemo/migrations.sql'
            }
            {
              name: 'LEARNINGNEMO_SQL_MIGRATION_SHA256'
              value: migrationBundleSha256
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
              value: sqlAdminIdentity.properties.clientId
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          volumeMounts: [
            {
              volumeName: 'migration-bundle'
              mountPath: '/var/run/learningnemo'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'migration-bundle'
          storageType: 'Secret'
          secrets: [
            {
              secretRef: 'migration-bundle'
              path: 'migrations.sql'
            }
          ]
        }
      ]
    }
  }
}

output migrationJobName string = migrationJob.name
output migrationBundleSha256 string = migrationBundleSha256
output expiresAt string = expiresAt