targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param platformResourceGroupName string
param vmName string
param adminUsername string
param expiresAt string
param openShellVersion string
param openShellPackageUrl string
param openShellPackageSha256 string
param sandboxImageReference string
param supervisorImageReference string
param sandboxVcpus int
param sandboxMemoryMiB int
param sandboxOverlayMiB int

@secure()
param sqlServerHostname string

resource workspaceVm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = {
  name: vmName
}

resource diagnosticApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-diagnostic-${environment}'
}

resource remediationApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'ca-${projectName}-remediation-${environment}'
}

var bootstrapSource = loadTextContent('../../scripts/bootstrap-saw-openshell.sh')
var planningPolicy = replace(loadTextContent('./openshell/planning-policy.yaml'), '__DIAGNOSTIC_HOST__', diagnosticApp.properties.configuration.ingress.fqdn)
var executionPolicy = replace(loadTextContent('./openshell/execution-policy.yaml'), '__REMEDIATION_HOST__', remediationApp.properties.configuration.ingress.fqdn)
var bootstrap01 = replace(bootstrapSource, '__ADMIN_USER__', adminUsername)
var bootstrap02 = replace(bootstrap01, '__EXPIRES_AT__', expiresAt)
var bootstrap03 = replace(bootstrap02, '__OPENSHELL_VERSION__', openShellVersion)
var bootstrap04 = replace(bootstrap03, '__OPENSHELL_PACKAGE_URL__', openShellPackageUrl)
var bootstrap05 = replace(bootstrap04, '__OPENSHELL_PACKAGE_SHA256__', openShellPackageSha256)
var bootstrap06 = replace(bootstrap05, '__SANDBOX_IMAGE__', sandboxImageReference)
var bootstrap07 = replace(bootstrap06, '__SUPERVISOR_IMAGE__', supervisorImageReference)
var bootstrap08 = replace(bootstrap07, '__SQL_SERVER__', sqlServerHostname)
var bootstrap09 = replace(bootstrap08, '__DIAGNOSTIC_HOST__', diagnosticApp.properties.configuration.ingress.fqdn)
var bootstrap10 = replace(bootstrap09, '__REMEDIATION_HOST__', remediationApp.properties.configuration.ingress.fqdn)
var bootstrap11 = replace(bootstrap10, '__SANDBOX_VCPUS__', string(sandboxVcpus))
var bootstrap12 = replace(bootstrap11, '__SANDBOX_MEMORY_MIB__', string(sandboxMemoryMiB))
var bootstrap13 = replace(bootstrap12, '__SANDBOX_OVERLAY_MIB__', string(sandboxOverlayMiB))
var bootstrap14 = replace(bootstrap13, '__PLANNING_POLICY_B64__', base64(planningPolicy))
var bootstrap15 = replace(bootstrap14, '__EXECUTION_POLICY_B64__', base64(executionPolicy))
var bootstrapScript = replace(bootstrap15, '__PROBE_POLICY_B64__', base64(loadTextContent('./openshell/probe-policy.yaml')))

resource bootstrap 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: workspaceVm
  name: 'bootstrap-openshell'
  location: location
  properties: {
    asyncExecution: false
    timeoutInSeconds: 2700
    treatFailureAsDeploymentFailure: true
    source: {
      script: bootstrapScript
    }
  }
}

output bootstrapCommandName string = bootstrap.name
output expiresAt string = expiresAt