targetScope = 'resourceGroup'
param location string = 'eastus'
param repairDiskId string
@secure()
param sshPublicKey string
param imageReference object
param expiresAt string
var tags = {
  owner: 'learningnemo-portfolio'
  project: 'learningnemo'
  purpose: 'offline-retained-repair'
  retention: 'owner-testing-hold'
  expiresAt: expiresAt
}
resource address 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-learningnemo-repair-egress'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}
resource nat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-learningnemo-repair'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIpAddresses: [{ id: address.id }] }
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-learningnemo-repair'
  location: location
  tags: tags
  properties: {
    securityRules: [
      { name: 'deny-inbound', properties: { priority: 100, access: 'Deny', direction: 'Inbound', protocol: '*', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '*' } }
      { name: 'allow-azure-https', properties: { priority: 100, access: 'Allow', direction: 'Outbound', protocol: 'Tcp', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: 'AzureCloud', destinationPortRange: '443' } }
      { name: 'allow-azure-dns', properties: { priority: 110, access: 'Allow', direction: 'Outbound', protocol: '*', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '168.63.129.16/32', destinationPortRange: '53' } }
      { name: 'deny-other-egress', properties: { priority: 120, access: 'Deny', direction: 'Outbound', protocol: '*', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '*' } }
    ]
  }
}
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-learningnemo-repair'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.60.0.0/27'] }
    subnets: [{ name: 'repair', properties: { addressPrefix: '10.60.0.0/27', networkSecurityGroup: { id: nsg.id }, natGateway: { id: nat.id }, defaultOutboundAccess: false } }]
  }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-learningnemo-repair'
  location: location
  tags: tags
  properties: {
    enableIPForwarding: false
    ipConfigurations: [{ name: 'private', properties: { privateIPAllocationMethod: 'Dynamic', subnet: { id: '${vnet.id}/subnets/repair' } } }]
  }
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: 'vm-learningnemo-repair'
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: 'Standard_D2s_v5' }
    securityProfile: { securityType: 'TrustedLaunch', uefiSettings: { secureBootEnabled: true, vTpmEnabled: true } }
    storageProfile: {
      imageReference: imageReference
      osDisk: { name: 'osdisk-learningnemo-repair-host', createOption: 'FromImage', deleteOption: 'Detach', managedDisk: { storageAccountType: 'StandardSSD_LRS' } }
      dataDisks: [{ lun: 0, name: last(split(repairDiskId, '/')), createOption: 'Attach', deleteOption: 'Detach', managedDisk: { id: repairDiskId } }]
    }
    osProfile: {
      computerName: 'learningnemo-repair'
      adminUsername: 'repairadmin'
      linuxConfiguration: { disablePasswordAuthentication: true, provisionVMAgent: true, ssh: { publicKeys: [{ path: '/home/repairadmin/.ssh/authorized_keys', keyData: sshPublicKey }] } }
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { deleteOption: 'Detach', primary: true } }] }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}