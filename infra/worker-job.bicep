@description('Region for the worker job.')
param location string = 'eastus'

@description('Name of the worker job.')
param workerJobName string = 'job-textvideo-worker-dev'

@description('Existing Container Apps managed environment.')
param environmentName string = 'cae-textvideo-dev'

@description('Existing user-assigned managed identity.')
param identityName string = 'id-textvideo-api-dev'

@description('Existing Azure Container Registry.')
param registryName string = 'acrtextvideo1794671698'

@description('Immutable image reference containing the worker code.')
param workerImage string

@description('Azure Storage account.')
param storageAccountName string = 'sttextvideo1794671698'

@description('Azure Queue used by the video backend.')
param queueName string = 'textvideo-jobs'

@description('Azure Table used by the video backend.')
param jobsTableName string = 'VideoJobs'

@description('Private Blob container for rendered video.')
param blobContainerName string = 'videos'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

// A manual, inspection-only job.
//
// This template intentionally does not:
// - start executions
// - poll Azure Queue
// - enable live rendering
// - configure Fal credentials
// - expose public ingress
// - create or update existing infrastructure dependencies

resource worker 'Microsoft.App/jobs@2024-03-01' = {
  name: workerJobName
  location: location

  tags: {
    application: 'textvideo'
    environment: 'dev'
    component: 'worker'
    activation: 'disabled'
  }

  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }

  properties: {
    environmentId: environment.id

    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 120
      replicaRetryLimit: 0

      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }

      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }

    template: {
      containers: [
        {
          name: 'video-worker'
          image: workerImage

          // Explicit safe command: inspect only.
          command: [
            'python'
          ]
          args: [
            'worker_runtime.py'
            '--check'
          ]

          env: [
            {
              name: 'FAL_KEY'
              secretRef: 'fal-api-key'
            }
            {
              name: 'FAL_MODEL_ID'
              value: 'fal-ai/kling-video/v3/pro/text-to-video'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT'
              value: storageAccountName
            }
            {
              name: 'AZURE_JOBS_QUEUE'
              value: queueName
            }
            {
              name: 'AZURE_JOBS_TABLE'
              value: jobsTableName
            }
            {
              name: 'AZURE_BLOB_CONTAINER'
              value: blobContainerName
            }
            {
              name: 'VIDEO_RENDER_PROVIDER'
              value: 'disabled'
            }
            {
              name: 'ENABLE_WORKER_CONSUMPTION'
              value: 'false'
            }
            {
              name: 'ENABLE_LIVE_VIDEO_RENDERING'
              value: 'false'
            }
          ]

          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output workerName string = worker.name
output workerResourceId string = worker.id
output executionMode string = 'Manual'
output liveRenderingEnabled bool = false
