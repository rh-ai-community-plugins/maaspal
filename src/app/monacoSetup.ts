// Import the slim editor core (editor.api.js) plus just the YAML language
// definition, instead of the full `monaco-editor` package entry — which pulls
// in tokenizers for every one of Monaco's ~80 bundled languages (pushed this
// lazy-loaded chunk over 4MB) when this app only ever displays YAML.
import * as monaco from 'monaco-editor/editor/editor.api.js';
import 'monaco-editor/languages/definitions/yaml/register.js';
import { loader } from '@monaco-editor/react';

// @monaco-editor/react defaults to lazy-loading Monaco's AMD bundle from a
// public CDN at runtime. This app bundles everything else into the container
// image (see Containerfile) and is meant to run inside OpenShift clusters that
// may have restricted egress — importing monaco-editor directly here and
// pointing the loader at it keeps the YAML editor working with no external
// network dependency, at the cost of the extra bundle size.
// The worker is bundled by webpack 5's native `new Worker(new URL(...))`
// support; with `publicPath: 'auto'` it is served from the plugin's own
// origin path (/_mf/maaspal/ inside the dashboard), like every other chunk.
self.MonacoEnvironment = {
  getWorker: () =>
    new Worker(new URL('monaco-editor/editor/editor.worker.js', import.meta.url), {
      type: 'module',
    }),
};

loader.config({ monaco });
