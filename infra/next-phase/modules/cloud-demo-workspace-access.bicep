param principalId string
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = { name: 'vm-learningnemo-saw-dev' }

resource readRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'cloud-demo-observe')
  properties: {
    roleName: 'LearningNeMo cloud workspace observer'
    description: 'Read only the VM, network, and deployment-stack state used by the fixed proof controller.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: [
        'Microsoft.Compute/virtualMachines/read'
        'Microsoft.Compute/virtualMachines/instanceView/read'
        'Microsoft.Network/networkInterfaces/read'
        'Microsoft.Network/networkSecurityGroups/securityRules/read'
        'Microsoft.Network/virtualNetworks/subnets/read'
        'Microsoft.Network/natGateways/read'
        'Microsoft.Resources/deploymentStacks/read'
      ]
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource executeRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'cloud-demo-run-fixed-proof')
  properties: {
    roleName: 'LearningNeMo workspace proof transport'
    description: 'Run Command transport on the single SAW VM. The controller, not RBAC, restricts the script.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: ['Microsoft.Compute/virtualMachines/runCommand/action']
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource observer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, 'id-learningnemo-cloud-controller-dev', 'cloud-demo-observe')
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: readRole.id }
}
resource executor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, 'id-learningnemo-cloud-controller-dev', 'cloud-demo-execute')
  scope: vm
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: executeRole.id }
}