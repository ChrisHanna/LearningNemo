targetScope = 'resourceGroup'

param location string
param appName string
param environmentId string
param identityResourceId string

@secure()
param identityClientId string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

param sqlDatabaseName string

@allowed([
  'diagnostic'
  'query-runner'
  'remediation'
  'verifier'
])
param serviceMode string

@secure()
param tenantId string

@secure()
param serviceApplicationId string

@secure()
param controlCallerApplicationId string

@secure()
param controlCallerPrincipalId string

param tags object

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: union(tags, {
    serviceMode: serviceMode
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityResourceId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      identitySettings: [
        {
          identity: identityResourceId
          lifecycle: 'Main'
        }
      ]
      ingress: {
        allowInsecure: false
        clientCertificateMode: 'ignore'
        external: true
        targetPort: 8080
        transport: 'auto'
      }
      registries: [
        {
          server: registryServer
          identity: identityResourceId
        }
      ]
      secrets: []
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: imageReference
          args: [
            '--port'
            '8080'
          ]
          env: [
            {
              name: 'LEARNINGNEMO_SERVICE_MODE'
              value: serviceMode
            }
            {
              name: 'LEARNINGNEMO_ALLOWED_CALLER_IDS'
              value: controlCallerPrincipalId
            }
            {
              name: 'LEARNINGNEMO_SQL_SERVER'
              value: sqlServerHostname
            }
            {
              name: 'LEARNINGNEMO_SQL_DATABASE'
              value: sqlDatabaseName
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8080
                scheme: 'HTTP'
              }
              failureThreshold: 10
              initialDelaySeconds: 1
              periodSeconds: 3
              successThreshold: 1
              timeoutSeconds: 2
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
                scheme: 'HTTP'
              }
              failureThreshold: 3
              initialDelaySeconds: 5
              periodSeconds: 15
              successThreshold: 1
              timeoutSeconds: 2
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/readyz'
                port: 8080
                scheme: 'HTTP'
              }
              failureThreshold: 3
              initialDelaySeconds: 2
              periodSeconds: 10
              successThreshold: 1
              timeoutSeconds: 2
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        pollingInterval: 30
        cooldownPeriod: 300
        rules: [
          {
            name: 'http-single-request'
            http: {
              metadata: {
                concurrentRequests: '1'
              }
            }
          }
        ]
      }
      terminationGracePeriodSeconds: 30
    }
  }
}

resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = {
  parent: app
  name: 'current'
  properties: {
    globalValidation: {
      excludedPaths: [
        '/healthz'
        '/readyz'
      ]
      unauthenticatedClientAction: 'Return401'
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: serviceApplicationId
          openIdIssuer: uri(environment().authentication.loginEndpoint, '${tenantId}/v2.0')
        }
        validation: {
          allowedAudiences: [
            'api://${serviceApplicationId}'
          ]
          defaultAuthorizationPolicy: {
            allowedApplications: [
              controlCallerApplicationId
            ]
            allowedPrincipals: {
              identities: [
                controlCallerPrincipalId
              ]
            }
          }
          jwtClaimChecks: {
            allowedClientApplications: [
              controlCallerApplicationId
            ]
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
  }
}

output appName string = app.name