import { Alert } from '@patternfly/react-core';

const REASON_TEXT: Record<string, string> = {
  forbidden:
    "The MaaS:PAL service account isn't authorized to read this. Ask your cluster admin to enable the Helm chart value rbac.maasReadonly.",
  not_installed: "The underlying resource isn't installed on this cluster — this MaaS version or install may not support it.",
  unreachable: "Could not reach the cluster's API server at all — check the kubeconfig/network MaaS:PAL is running with.",
  network_error: 'Could not reach the MaaS:PAL API server.',
};

interface Props {
  reason: string | null;
}

export function MaasUnavailableNotice({ reason }: Props) {
  const detail =
    (reason && REASON_TEXT[reason]) ||
    (reason ? `Unavailable (${reason}).` : 'Unavailable — no reason reported.');

  return (
    <Alert variant="warning" isInline title="MaaS visibility unavailable">
      {detail}
    </Alert>
  );
}
