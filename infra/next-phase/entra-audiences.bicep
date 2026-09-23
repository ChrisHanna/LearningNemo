targetScope = 'resourceGroup'

extension microsoftGraphV1

param environment string
param projectName string

@secure()
param serviceApplicationIds object

var services = [
  {
    mode: 'diagnostic'
    key: 'diagnostic'
  }
  {
    mode: 'query-runner'
    key: 'queryRunner'
  }
  {
    mode: 'remediation'
    key: 'remediation'
  }
  {
    mode: 'verifier'
    key: 'verifier'
  }
]

resource applications 'Microsoft.Graph/applications@v1.0' = [for service in services: {
  uniqueName: '${projectName}-${service.mode}-${environment}'
  displayName: '${projectName}-${service.mode}-${environment}'
  description: 'LearningNeMo ${service.mode} workload audience managed by Bicep.'
  signInAudience: 'AzureADMyOrg'
  identifierUris: [
    'api://${serviceApplicationIds[service.key]}'
  ]
  isFallbackPublicClient: false
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: []
    preAuthorizedApplications: []
  }
  appRoles: []
  keyCredentials: []
  passwordCredentials: []
  publicClient: {
    redirectUris: []
  }
  requiredResourceAccess: []
  spa: {
    redirectUris: []
  }
  tags: [
    'learningnemo'
    'wp2b-trusted-worker'
    service.mode
    environment
  ]
  web: {
    implicitGrantSettings: {
      enableAccessTokenIssuance: false
      enableIdTokenIssuance: false
    }
    redirectUris: []
  }
}]

resource servicePrincipals 'Microsoft.Graph/servicePrincipals@v1.0' = [for (service, index) in services: {
  appId: serviceApplicationIds[service.key]
  accountEnabled: true
  appRoleAssignmentRequired: false
  displayName: '${projectName}-${service.mode}-${environment}'
  description: 'LearningNeMo ${service.mode} workload audience principal managed by Bicep.'
  keyCredentials: []
  passwordCredentials: []
  tags: [
    'learningnemo'
    'wp2b-trusted-worker'
    service.mode
    environment
  ]
  dependsOn: [
    applications[index]
  ]
}]