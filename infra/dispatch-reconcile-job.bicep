@description('Existing Container Apps environment. Deploy only after separate authorization.')
param environmentName string = 'cae-textvideo-dev'

@description('Existing least-privilege reconciliation identity; must be provisioned and audited separately.')
param reconciliationIdentityName string

@description('Existing Azure Container Registry.')
param registryName string = 'acrtextvideo1794671698'

@description('Only immutable ACR image@sha256:<64 hex> references may be approved; validate through the deployment readiness gate.')
param workerImage string

@description('Storage account and queue names, not credentials.')
param storageAccountName string = 'sttextvideo1794671698'
param queueName string = 'textvideo-jobs'
param jobsTableName string = 'VideoJobs'

@description('Approved UTC schedules: every 15 minutes or hourly. Slower schedules reduce cost.')
@allowed([ '*/15 * * * *', '0 * * * *' ])
param cronExpression string = '0 * * * *'

@description('Explicit opt-in: false schedules only the offline --check preflight.')
param activateReconciliation bool = false

@description('Deployment region.')
param location string = 'eastus'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: reconciliationIdentityName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

// Development-only deployment blueprint, not deployed by this PR.
// No paid-provider credentials, public ingress, or render worker is created.
// Production activation requires scoped Queue Sender, Table Data Contributor,
// read-only monitoring, and an independent Azure RBAC/IAM review.
// No scheduled Azure job is created at all until a separately authorized activation.
resource scheduledReconciler 'Microsoft.App/jobs@2024-03-01' = if (activateReconciliation) {
  name: 'job-textvideo-dispatch-reconcile-dev'
  location: location
  tags: {
    application: 'textvideo'
    environment: 'dev'
    component: 'dispatch-reconciliation'
    execution: activateReconciliation ? 'explicitly-enabled' : 'preflight-only'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      secrets: []
      triggerType: 'Schedule'
      replicaTimeout: 300
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: cronExpression
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
          name: 'dispatch-reconciliation'
          image: workerImage
          command: [ 'python', '-m', 'dispatch_reconcile_cli' ]
          args: activateReconciliation ? [ '--run-once', '--batch-size', '25', '--max-pages', '20' ] : [ '--check' ]
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT', value: storageAccountName }
            { name: 'AZURE_JOBS_QUEUE', value: queueName }
            { name: 'AZURE_JOBS_TABLE', value: jobsTableName }
            { name: 'ENABLE_DISPATCH_RECONCILIATION', value: activateReconciliation ? 'true' : 'false' }
            { name: 'VIDEO_RENDER_PROVIDER', value: 'disabled' }
            { name: 'ENABLE_PAID_FAL_WORKER', value: 'false' }
            { name: 'ENABLE_LIVE_VIDEO_RENDERING', value: 'false' }
            { name: 'ENABLE_WORKER_CONSUMPTION', value: 'false' }
            { name: 'ENABLE_PROJECT_ROUTES', value: 'false' }
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
output scheduledJobName string = scheduledReconciler.name
output liveQueueReconciliation bool = activateReconciliation
output paidFalEnabled bool = false
