targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('FastAPI Container App name')
param backendAppName string = 'ca-textvideo-api-dev'

@description('Existing Container Apps environment')
param containerAppsEnvironmentName string = 'cae-textvideo-dev'

@description('Existing user-assigned managed identity')
param backendIdentityName string = 'id-textvideo-api-dev'

@description('Verified Docker image in Azure Container Registry')
param backendImage string = 'acrtextvideo1794671698.azurecr.io/textvideo-api:9c8fa57d1442ba15e89d81214142aeb9c5da4fc7'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerAppsEnvironmentName
}

resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: backendIdentityName
}

resource backendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${backendIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: 'acrtextvideo1794671698.azurecr.io'
          identity: backendIdentity.id
        }
      ]
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'textvideo-api'
          image: backendImage
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: backendIdentity.properties.clientId
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT'
              value: 'sttextvideo1794671698'
            }
            {
              name: 'AZURE_BLOB_CONTAINER'
              value: 'videos'
            }
            {
              name: 'AZURE_JOBS_TABLE'
              value: 'VideoJobs'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output backendAppId string = backendApp.id
