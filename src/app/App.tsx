import palLogo from './assets/pal-logo.png';
import { useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { Grid, GridItem, PageSection } from '@patternfly/react-core';
import CommunityBanner from './components/CommunityBanner';
import { MaasOverviewPage } from './components/maas/MaasOverviewPage';
import { RunDetail } from './components/RunDetail';
import { RunHistory } from './components/RunHistory';
import { RunTrigger } from './components/RunTrigger';
import { ScenarioList } from './components/ScenarioList';
import type { Scenario } from './api/client';
import './styles/theme.css';

// Rendered by the RHOAI dashboard at /maaspal/* (src/rhoai/extensions.ts). The
// dashboard owns the page chrome — masthead, sidebar (where "Test runs" and
// "MaaS setup" live) and the scroll container — so this renders content only.
// Paths are absolute so they work under the host's router and the standalone
// dev router (basename /maaspal) alike.
const RUNS_PATH = '/maaspal/runs';

function PageHeader() {
  return (
    <div className="maaspal-page-header">
      <img src={palLogo} alt="MaaS:PAL" className="maaspal-page-header__logo" />
      <span className="maaspal-page-header__subtitle">RHOAI MaaS test harness</span>
    </div>
  );
}

export function RunsPage() {
  const navigate = useNavigate();
  const [triggerScenario, setTriggerScenario] = useState<Scenario | null>(null);

  return (
    <>
      <PageSection>
        <Grid hasGutter>
          <GridItem span={4}>
            <ScenarioList onRun={setTriggerScenario} />
          </GridItem>
          <GridItem span={8}>
            <RunHistory onViewRun={(runId) => navigate(`${RUNS_PATH}/${runId}`)} />
          </GridItem>
        </Grid>
      </PageSection>

      {triggerScenario !== null && (
        <RunTrigger
          scenario={triggerScenario}
          onConfirm={(runId) => {
            setTriggerScenario(null);
            navigate(`${RUNS_PATH}/${runId}`);
          }}
          onCancel={() => setTriggerScenario(null)}
        />
      )}
    </>
  );
}

function RunDetailPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  if (!runId) return <Navigate to={RUNS_PATH} replace />;
  return <RunDetail runId={runId} onBack={() => navigate(RUNS_PATH)} />;
}

function App() {
  return (
    <div className="community-plugin-layout">
      {/* [SHARED] Do not remove — all community plugins must display the CommunityBanner */}
      <CommunityBanner />
      <div className="community-plugin-content maaspal-plugin">
        <PageHeader />
        <Routes>
          <Route path="/" element={<Navigate to={RUNS_PATH} replace />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="runs/:runId" element={<RunDetailPage />} />
          <Route path="setup/*" element={<MaasOverviewPage />} />
          <Route path="*" element={<Navigate to={RUNS_PATH} replace />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
