targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param databaseName string
param ownerTag string

@minValue(1)
@maxValue(50)
param monthlyCostCeiling int

@minValue(15)
@maxValue(10080)
param autoPauseDelayMinutes int

@allowed([
  '0.5'
])
param minVcores string

@minValue(1)
@maxValue(4)
param maxVcores int

@minValue(1073741824)
@maxValue(34359738368)
param maxSizeBytes int

param useFreeLimit bool

@allowed([
  'AutoPause'
])
param freeLimitExhaustionBehavior string

param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'azure-sql-free-serverless'
  monthlyCostCeiling: string(monthlyCostCeiling)
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-database'
  disposable: 'false'
  freeLimit: 'auto-pause-on-exhaustion'
})
var sqlServerName = 'sql-${projectName}-${environment}-${uniqueString(subscription().subscriptionId, resourceGroup().id)}'
var sqlAdminIdentityName = 'id-${projectName}-sql-admin-${environment}'

resource sqlAdminIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: sqlAdminIdentityName
  location: location
  tags: union(tags, {
    identityPurpose: 'database-bootstrap-admin'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlServerName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sqlAdminIdentity.id}': {}
    }
  }
  properties: {
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      login: sqlAdminIdentity.name
      principalType: 'Application'
      sid: sqlAdminIdentity.properties.principalId
      tenantId: subscription().tenantId
    }
    isIPv6Enabled: 'Disabled'
    minimalTlsVersion: '1.2'
    primaryUserAssignedIdentityId: sqlAdminIdentity.id
    publicNetworkAccess: 'Disabled'
    restrictOutboundNetworkAccess: 'Enabled'
    version: '12.0'
  }
}

resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sqlServer
  name: databaseName
  location: location
  tags: tags
  sku: {
    name: 'GP_S_Gen5_${maxVcores}'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: maxVcores
  }
  properties: {
    autoPauseDelay: autoPauseDelayMinutes
    createMode: 'Default'
    freeLimitExhaustionBehavior: freeLimitExhaustionBehavior
    highAvailabilityReplicaCount: 0
    isLedgerOn: false
    licenseType: 'LicenseIncluded'
    maxSizeBytes: maxSizeBytes
    minCapacity: json(minVcores)
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
    useFreeLimit: useFreeLimit
    zoneRedundant: false
  }
}

output databaseName string = database.name
output costProfile string = 'Free-limit General Purpose serverless; auto-pause on exhaustion; no public access.'
output sqlAdminIdentityName string = sqlAdminIdentity.name
