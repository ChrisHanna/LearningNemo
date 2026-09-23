targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param expiresAt string
param ownerTag string
param monthlyCostCeiling int
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
  identityPurpose: 'database-bootstrap-admin'
  temporaryCrossRegionUse: 'sql-migration-job'
  migrationScopeExpiresAt: expiresAt
})

resource sqlAdminIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-sql-admin-${environment}'
  location: location
  tags: tags
  properties: {
    isolationScope: 'None'
  }
}

output isolationScope string = sqlAdminIdentity.properties.isolationScope
output expiresAt string = expiresAt