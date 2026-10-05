import { Alert } from '@patternfly/react-core';

const REASON_TEXT: Record<string, string> = {
  forbidden:
    "The MaaS:PAL service account isn't authorized to read this. Ask your cluster admin to enable the Helm chart value rbac.maasReadonly.",
  not_installed: "The underlying resource isn't installed on this cluster — this MaaS version or install may not support it.",
  unreachable: "Could not reach the cluster's API server at all — check the kubeconfig/network MaaS:PAL is running with.",
  network_error: 'Could not reach the MaaS:PAL API server.',
  // The BFF's access gate (api/auth.py) refusing this dashboard user.
  http_401: 'No user token reached MaaS:PAL. Open it from the RHOAI dashboard.',
  http_403:
    "You don't have access to MaaS:PAL. Ask an administrator to bind the maaspal-user Role in the plugin's namespace to you or your group.",
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
