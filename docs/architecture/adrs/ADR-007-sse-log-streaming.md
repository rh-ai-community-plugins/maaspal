# ADR-007: Server-Sent Events for Live Log Streaming

## Status

Superseded — replaced by REST polling (see the Update below)

## Context

Scenario runs can take minutes to complete (hundreds of concurrent inference requests, metrics polling). The UI should show live logs as the Job pod emits them, rather than requiring the operator to wait for completion and then load a static result.

Options considered:

- **Polling**: the UI calls `GET /api/runs/{id}/logs` repeatedly on an interval and receives new lines each time. Simple to implement but adds latency and unnecessary load.
- **WebSocket**: bidirectional persistent connection. Technically suitable but adds complexity (connection upgrade, framing protocol) for a use case that only requires server-to-client streaming.
- **Server-Sent Events (SSE)**: a unidirectional HTTP streaming protocol. The server keeps the response open and pushes `data:` frames as new log lines arrive. The browser has native `EventSource` support.

The API server already tails pod logs using the Kubernetes Python client's `read_namespaced_pod_log(follow=True)` call, which provides an iterator over log lines. This maps naturally onto an SSE stream.

## Decision

Use **Server-Sent Events** via `GET /api/runs/{id}/logs`. The FastAPI handler opens a streaming response, subscribes to the Kubernetes pod log iterator, and emits each line as an SSE `data:` frame. The UI uses the browser's native `EventSource` API to consume the stream and append lines to the log panel.

The UI automatically reconnects if the SSE connection drops (browser `EventSource` does this natively on error).

## Consequences

**Positive:**
- Native browser support via `EventSource` — no additional client library needed.
- Unidirectional streaming matches the use case exactly (logs only flow from server to client).
- FastAPI's `StreamingResponse` and Python async generators make the server-side implementation straightforward.
- Reconnect is handled automatically by the browser.

**Negative:**
- SSE connections are HTTP/1.1 long-lived responses; browsers limit concurrent connections per origin (typically 6 for HTTP/1.1). If multiple runs are being watched simultaneously, connection exhaustion is possible. HTTP/2 mitigates this, and the OpenShift Route can be configured for HTTP/2.
- If the pod has not started yet when the UI opens the log stream, the handler must poll for pod readiness before attaching to the log stream, adding a small delay.

**Neutral:**
- The Kubernetes client's `follow=True` log streaming and the SSE response share the same async generator pattern, making the glue code minimal.

## Update: superseded by REST polling

SSE was removed. OpenShift's edge-terminated Routes (HAProxy) buffer response bodies until the connection closes, so events never reached the browser live without cluster-level proxy changes. Logs are now captured to the PVC by a background thread in the API server (`api/k8s.py:_capture_logs`), and the UI polls `GET /api/runs/{id}/logs/lines?offset=N` every second. See CLAUDE.md, "Log Streaming".
