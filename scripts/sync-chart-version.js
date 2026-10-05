const { version } = require('../package.json');
const fs = require('fs');

const chartPath = 'chart/Chart.yaml';
let chart = fs.readFileSync(chartPath, 'utf8');
chart = chart.replace(/^version:.*/m, `version: ${version}`);
chart = chart.replace(/^appVersion:.*/m, `appVersion: "${version}"`);
fs.writeFileSync(chartPath, chart);

// The BFF is a Python project: keep its pyproject.toml version in step.
const pyprojectPath = 'bff/pyproject.toml';
let pyproject = fs.readFileSync(pyprojectPath, 'utf8');
pyproject = pyproject.replace(/^version = .*/m, `version = "${version}"`);
fs.writeFileSync(pyprojectPath, pyproject);

const pluginPath = 'plugin.yaml';
let plugin = fs.readFileSync(pluginPath, 'utf8');
plugin = plugin.replace(/^version:.*/m, `version: ${version}`);
plugin = plugin.replace(/^(\s+tag:)\s.*$/gm, `$1 "${version}"`);
fs.writeFileSync(pluginPath, plugin);

const versionFlag = /--version \S+/g;
for (const docPath of ['README.md', 'docs/deployment/OPENSHIFT_DEPLOY.md']) {
  const content = fs.readFileSync(docPath, 'utf8');
  const updated = content.replace(versionFlag, `--version ${version}`);
  if (updated !== content) {
    fs.writeFileSync(docPath, updated);
  }
}
