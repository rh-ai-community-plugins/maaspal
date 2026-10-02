import { useEffect, useState } from 'react';
import { RawYamlModal } from './RawYamlModal';
import { getRunConfig } from '../api/client';

interface Props {
  runId: string;
  onClose: () => void;
}

export function RunSettingsModal({ runId, onClose }: Props) {
  const [yamlText, setYamlText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    getRunConfig(runId).then((text) => {
      if (active) {
        setYamlText(text);
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [runId]);

  return (
    <RawYamlModal
      title="Run Settings"
      downloadFileName={`${runId}.yaml`}
      yamlText={yamlText}
      loading={loading}
      emptyMessage="Settings aren't available yet — the run may not have started."
      onClose={onClose}
    />
  );
}
