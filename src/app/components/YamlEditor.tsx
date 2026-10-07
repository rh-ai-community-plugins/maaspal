import { CodeEditor, Language } from '@patternfly/react-code-editor';
import '../monacoSetup';

// Editable YAML editor (Monaco, self-hosted — see monacoSetup.ts). Its own
// module so the Import dialog can load it lazily, like RawYamlModal.
export function YamlEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <CodeEditor
      isDarkTheme
      isLineNumbersVisible
      language={Language.yaml}
      code={value}
      onCodeChange={onChange}
      height="400px"
      aria-label="Scenario YAML"
    />
  );
}
