import { lazy, Suspense, useRef, useState, type ChangeEvent } from 'react';
import { Alert, Button, Modal, ModalBody, ModalFooter, ModalHeader, Spinner } from '@patternfly/react-core';
import { UploadIcon } from '@patternfly/react-icons';
import { importScenario, ScenarioNameTaken, type Scenario } from '../api/client';

// Code-split: the editor pulls in Monaco.
const YamlEditor = lazy(() => import('./YamlEditor').then((m) => ({ default: m.YamlEditor })));

interface Props {
  onImported: (scenario: Scenario) => void;
  onClose: () => void;
}

// "Import scenario": a scenario YAML from a file or pasted in, stored by the
// BFF as a custom scenario (api/scenario_store.py). The BFF validates it; its
// reason for refusing is shown as received.
export function ImportScenarioModal({ onImported, onClose }: Props) {
  const [text, setText] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // An earlier import has this name — offer to replace it.
  const [canReplace, setCanReplace] = useState(false);
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function edit(value: string) {
    setText(value);
    setError(null);
    setCanReplace(false);
  }

  async function readFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      edit(await file.text());
      setFileName(file.name);
    } catch (err) {
      setError(`Could not read ${file.name}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function submit(replace: boolean) {
    setSaving(true);
    try {
      onImported(await importScenario(text, replace));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCanReplace(err instanceof ScenarioNameTaken && !err.builtIn);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal isOpen onClose={onClose} aria-labelledby="maaspal-import-title" variant="large" className="maaspal-modal">
      <ModalHeader
        title="Import scenario"
        labelId="maaspal-import-title"
        description="Upload a scenario YAML file or paste one below. It's added to the catalog as a custom scenario."
      />
      <ModalBody>
        <div className="maaspal-import__toolbar">
          <Button variant="secondary" icon={<UploadIcon />} onClick={() => fileInput.current?.click()}>
            Upload file
          </Button>
          {fileName && <span className="maaspal-import__file">{fileName}</span>}
          <input
            ref={fileInput}
            type="file"
            accept=".yaml,.yml,text/yaml,application/x-yaml"
            hidden
            data-testid="import-file-input"
            onChange={(e) => void readFile(e)}
          />
        </div>
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <YamlEditor value={text} onChange={edit} />
        </Suspense>
        {error && (
          <Alert
            variant="danger"
            isInline
            title={canReplace ? 'A scenario with this name was already imported' : 'Could not import the scenario'}
            className="maaspal-import__error"
            actionLinks={
              canReplace && (
                <Button variant="link" isInline onClick={() => void submit(true)} isDisabled={saving}>
                  Replace it
                </Button>
              )
            }
          >
            <span className="maaspal-import__error-text">{error}</span>
          </Alert>
        )}
      </ModalBody>
      <ModalFooter>
        <Button
          variant="primary"
          onClick={() => void submit(false)}
          isLoading={saving}
          isDisabled={saving || text.trim() === ''}
        >
          Import
        </Button>
        <Button variant="link" onClick={onClose} isDisabled={saving}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  );
}
