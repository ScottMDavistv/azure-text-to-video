@description('Existing Log Analytics workspace receiving Container Apps console logs.')
param workspaceName string = 'log-textvideo-dev'

@description('Existing Azure Monitor Action Group resource ID to receive notifications.')
param actionGroupResourceId string

@description('Explicit deployment opt-in; default deploys no alert resources.')
param activateAlerts bool = false

@description('Resource region.')
param location string = 'eastus'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

// Diagnostic logs must be routed to this workspace for the rule to function.
// Confirm the actual emitted table/column using a test execution before enabling.
// This rule is a deployment blueprint, not active until separately authorized.
resource recoveryAnomaly 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (activateAlerts) {
  name: 'textvideo-dispatch-recovery-anomalies'
  location: location
  kind: 'LogAlert'
  properties: {
    description: 'Uncertain or incomplete non-paid dispatch reconciliation'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT15M'
    windowSize: 'PT30M'
    scopes: [workspace.id]
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: '''
            ContainerAppConsoleLogs_CL
            | where TimeGenerated > ago(30m)
            | where Log_s contains "dispatch_reconciliation_requires_attention"
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          dimensions: []
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroupResourceId]
    }
  }
}
output alertsEnabled bool = activateAlerts
