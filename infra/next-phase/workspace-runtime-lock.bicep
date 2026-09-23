targetScope = 'resourceGroup'

param environment string
param projectName string

resource workspaceNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: 'nsg-vnet-${projectName}-saw-${environment}-workspace'
}

resource denyImds 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workspaceNsg
  name: 'deny-sandbox-host-imds'
  properties: {
    priority: 121
    access: 'Deny'
    direction: 'Outbound'
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: 'AzurePlatformIMDS'
    description: 'Deny direct metadata access after the OpenShell runtime is bootstrapped.'
  }
}

resource allowAzureDns 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workspaceNsg
  name: 'allow-azure-platform-dns'
  properties: {
    priority: 122
    access: 'Allow'
    direction: 'Outbound'
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '53'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: '168.63.129.16/32'
    description: 'Retain the Azure platform DNS resolver after general Internet egress is denied.'
  }
}

resource allowAzureHttps 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workspaceNsg
  name: 'allow-approved-azure-https'
  properties: {
    priority: 125
    access: 'Allow'
    direction: 'Outbound'
    protocol: 'Tcp'
    sourcePortRange: '*'
    destinationPortRange: '443'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: 'AzureCloud'
    description: 'Allow Azure-hosted APIs only; OpenShell applies the narrower host, method, and path policy.'
  }
}

resource denyInternet 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workspaceNsg
  name: 'deny-general-internet-runtime'
  properties: {
    priority: 130
    access: 'Deny'
    direction: 'Outbound'
    protocol: '*'
    sourcePortRange: '*'
    destinationPortRange: '*'
    sourceAddressPrefix: '*'
    destinationAddressPrefix: 'Internet'
    description: 'Remove bootstrap-only Internet egress before the SAW is declared ready.'
  }
}

output ruleNames array = [
  denyImds.name
  allowAzureDns.name
  allowAzureHttps.name
  denyInternet.name
]