targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param sawResourceGroupName string
param sawVnetName string
param workspaceSubnetName string
param vmName string
param vmSize string
param adminUsername string

@secure()
param sshPublicKey string

param imagePublisher string
param imageOffer string
param imageSku string
param imageVersion string
param expiresAt string
param openShellVersion string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep-deployment-stack'
  owner: ownerTag
  costProfile: 'ephemeral-saw-openshell-poc'
  trustZone: 'untrusted-agent-workspace'
  platformPhase: 'wp5-wp6-saw-openshell'
  disposable: 'true'
  expiresAt: expiresAt
  sawMaturity: 'openshell-runtime-poc'
  openShellVersion: openShellVersion
  openShellDriver: 'microvm'
})

resource sawVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: sawVnetName
}

resource workspaceSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: sawVnet
  name: workspaceSubnetName
}

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${projectName}-saw-runtime-${environment}'
  location: location
  tags: union(tags, {
    identityPurpose: 'saw-host-runtime-no-rbac'
  })
  properties: {
    isolationScope: 'Regional'
  }
}

resource workspaceNic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-${vmName}'
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    enableIPForwarding: false
    ipConfigurations: [
      {
        name: 'private'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: workspaceSubnet.id
          }
        }
      }
    ]
  }
}

resource workspaceVm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${runtimeIdentity.id}': {}
    }
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: imagePublisher
        offer: imageOffer
        sku: imageSku
        version: imageVersion
      }
      osDisk: {
        name: 'osdisk-${vmName}'
        createOption: 'FromImage'
        deleteOption: 'Delete'
        diskSizeGB: 64
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      allowExtensionOperations: true
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        enableVMAgentPlatformUpdates: true
        patchSettings: {
          assessmentMode: 'ImageDefault'
          patchMode: 'ImageDefault'
        }
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: workspaceNic.id
          properties: {
            deleteOption: 'Delete'
            primary: true
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
  }
}

output vmName string = workspaceVm.name
output nicName string = workspaceNic.name
output runtimeIdentityName string = runtimeIdentity.name
output sawResourceGroupName string = sawResourceGroupName
output expiresAt string = expiresAt