targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep-deployment-stack'
  owner: ownerTag
  costProfile: 'temporary-saw-bootstrap-egress'
  trustZone: 'untrusted-agent-workspace'
  platformPhase: 'wp5-saw-bootstrap'
  disposable: 'true'
  expiresAt: expiresAt
})

resource bootstrapPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-${projectName}-saw-bootstrap-${environment}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 4
    deleteOption: 'Delete'
  }
}

resource bootstrapNat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-${projectName}-saw-bootstrap-${environment}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 4
    publicIpAddresses: [
      {
        id: bootstrapPublicIp.id
      }
    ]
  }
}

output natGatewayName string = bootstrapNat.name
output publicIpName string = bootstrapPublicIp.name