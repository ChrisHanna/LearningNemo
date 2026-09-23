targetScope = 'resourceGroup'

extension microsoftGraphV1

param environment string
param projectName string

var services = [
  'diagnostic'
  'query-runner'
  'remediation'
  'verifier'
]

resource applications 'Microsoft.Graph/applications@v1.0' = [for service in services: {
  uniqueName: '${projectName}-${service}-${environment}'
  displayName: '${projectName}-${service}-${environment}'
  description: 'LearningNeMo ${service} workload audience managed by Bicep.'
  signInAudience: 'AzureADMyOrg'
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
    service
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