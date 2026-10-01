# Architecture Decision Records

This directory contains ADRs for the MaaS:PAL RHOAI MaaS Testing Harness.

## Format

Each ADR follows the [Michael Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
- **Status**: Accepted / Proposed / Deprecated / Superseded
- **Context**: the problem and constraints that motivated the decision
- **Decision**: what was chosen and why
- **Consequences**: trade-offs, positive and negative

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-single-container-image.md) | Single Container Image for API Server and Job Runner | Accepted |
| [ADR-002](ADR-002-kubernetes-jobs-per-run.md) | Kubernetes Job per Scenario Run | Accepted |
| [ADR-003](ADR-003-task-abc-deferred-cleanup.md) | Task ABC with Deferred, Scenario-wide Cleanup | Accepted |
| [ADR-004](ADR-004-shared-state-inter-task-communication.md) | shared_state Dict for Inter-task Communication | Accepted |
| [ADR-005](ADR-005-tiered-config.md) | Two-level Tiered Configuration (Global ConfigMap + Scenario YAML) | Accepted |
| [ADR-006](ADR-006-sqlite-pvc-results-storage.md) | SQLite on PVC for Run History and Results | Accepted |
| [ADR-007](ADR-007-sse-log-streaming.md) | Server-Sent Events for Live Log Streaming | Accepted |
| [ADR-008](ADR-008-service-account-token-auth.md) | Service Account Token for MaaS API Authentication | Accepted |
| [ADR-009](ADR-009-maas-subscription-via-crd.md) | MaaSSubscription Rate Limits via Kubernetes CRD | Accepted |
| [ADR-010](ADR-010-scenario-yaml-inline-assertions.md) | Scenario YAML with Inline Assertions | Accepted |
| [ADR-011](ADR-011-react-patternfly-frontend.md) | React + TypeScript + PatternFly Frontend | Accepted |
| [ADR-012](ADR-012-send-requests-overridable-url-token.md) | send_requests with Overridable URL and Token (Enabling Direct Inference) | Accepted |
| [ADR-013](ADR-013-realtime-assertion-evaluation.md) | Real-time Assertion Evaluation During Run Execution | Accepted |
| [ADR-014](ADR-014-maas-metrics-cross-validation.md) | MaaS Metrics Cross-Validation via Prometheus Baseline Delta | Accepted |
| [ADR-015](ADR-015-promql-native-assertions.md) | PromQL-Native Assertions | Accepted |
| [ADR-016](ADR-016-graceful-run-stop.md) | Graceful Run Stop via Pod Delete + SIGTERM Handler | Accepted |
| [ADR-017](ADR-017-maas-cluster-visibility.md) | MaaS Cluster Visibility via Read-Only CustomObjectsApi Reads | Accepted |
| [ADR-018](ADR-018-access-control-enforcement-testing.md) | Access-Control Enforcement Testing via a Second CRD-Writing Task | Accepted |
| [ADR-019](ADR-019-rest-only-api-key-lifecycle.md) | REST-Only API Key Lifecycle Validation | Accepted |
| [ADR-020](ADR-020-scenario-categories.md) | Scenario Categories in the UI | Accepted |
| [ADR-021](ADR-021-rate-limit-priority-precedence.md) | Rate-Limit Priority Precedence via Auto-Selection, Not Live Arbitration | Accepted |
| [ADR-022](ADR-022-platform-health-checks.md) | Read-Only Platform Health Checks | Accepted |
| [ADR-023](ADR-023-multi-user-rate-limit-sharing.md) | Multi-User Rate-Limit Sharing via ServiceAccount-Minted Identities | Accepted |
| [ADR-024](ADR-024-dynamic-model-routing-and-rate-limit-precision.md) | Dynamic Model Routing and Rate-Limit Measurement Precision | Accepted |
| [ADR-025](ADR-025-question-first-scenarios-and-run-narration.md) | Question-First Scenarios and Run Narration | Accepted |
