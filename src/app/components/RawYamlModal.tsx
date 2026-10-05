import { Button, Modal, ModalBody, ModalFooter, ModalHeader, Spinner } from '@patternfly/react-core';
import { CodeEditor, Language } from '@patternfly/react-code-editor';
import '../monacoSetup';

interface Props {
  title: string;
  downloadFileName: string;
  yamlText: string | null;
  loading?: boolean;
  emptyMessage?: string;
  onClose: () => void;
}

// Shared read-only YAML viewer — mirrors OpenShift console's own "View YAML"
// (same underlying component family: PatternFly CodeEditor / Monaco). Used by
// RunSettingsModal (a run's resolved config) and the MaaS Setup tabs (a raw
// cluster object), so both look and behave identically.
export function RawYamlModal({
  title,
  downloadFileName,
  yamlText,
  loading = false,
  emptyMessage = 'Not available.',
  onClose,
}: Props) {
  return (
    <Modal isOpen onClose={onClose} aria-label={title} variant="large" className="maaspal-modal">
      <ModalHeader title={title} />
      <ModalBody>
      {loading ? (
        <Spinner size="md" aria-label={`Loading ${title}`} />
      ) : yamlText ? (
        <CodeEditor
          isReadOnly
          isDarkTheme
          isCopyEnabled
          isDownloadEnabled
          isLineNumbersVisible
          language={Language.yaml}
          code={yamlText}
          height="480px"
          downloadFileName={downloadFileName}
          copyButtonToolTipText="Copy YAML"
          copyButtonSuccessTooltipText="Copied!"
        />
      ) : (
        <p style={{ color: '#888', fontStyle: 'italic' }}>{emptyMessage}</p>
      )}
      </ModalBody>
      <ModalFooter>
        <Button variant="link" onClick={onClose}>
          Close
        </Button>
      </ModalFooter>
    </Modal>
  );
}
