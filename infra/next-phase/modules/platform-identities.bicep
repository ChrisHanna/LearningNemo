targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param tags object

resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-control-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'control-plane'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource diagnosticIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-diagnostic-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'diagnostic-api'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource remediationIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-remediation-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'remediation-broker'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource verifierIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-verifier-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'independent-verifier'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource queryRunnerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-query-runner-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'demo-query-runner'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource workspaceControllerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-workspace-controller-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'workspace-controller'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

output identityNames array = [
  controlIdentity.name
  diagnosticIdentity.name
  remediationIdentity.name
  verifierIdentity.name
  queryRunnerIdentity.name
  workspaceControllerIdentity.name
]