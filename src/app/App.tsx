import { useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { Button, PageSection, Title } from '@patternfly/react-core';
import CommunityBanner from './components/CommunityBanner';
import { PageIntro } from './components/PageIntro';
import { MaasOverviewPage } from './components/maas/MaasOverviewPage';
import { RunDetail } from './components/RunDetail';
import { RunHistory } from './components/RunHistory';
import { RunTrigger } from './components/RunTrigger';
import { ScenarioCatalog } from './components/ScenarioCatalog';
import { useRotatingLogo } from './logos';
import { PalPet } from './pal/PalPet';
import { PalProvider, usePal, useSecretClicks } from './pal/usePal';
import type { Scenario } from './api/client';
import './styles/tokens.css';
import './styles/theme.css';

// Rendered by the RHOAI dashboard at /maaspal/* (src/rhoai/extensions.ts). The
// dashboard owns the page chrome — masthead, sidebar (where "Scenarios",
// "Runs" and "MaaS overview" live) and the scroll container — so this renders
// content only. Paths are absolute so they work under the host's router and
// the standalone dev router (basename /maaspal) alike.
const SCENARIOS_PATH = '/maaspal/scenarios';
const RUNS_PATH = '/maaspal/runs';
const OVERVIEW_PATH = '/maaspal/overview';

function PageHeader() {
  // A different mascot on every page, from the set that shows up on the
  // current theme (src/app/logos.ts).
  const logo = useRotatingLogo(useLocation().pathname);
  // Easter egg: five quick clicks on the logo hatch PAL (src/app/pal/), five
  // more put it away.
  const { toggle } = usePal();
  const secretClick = useSecretClicks();
  return (
    <div className="maaspal-page-header">
      <img
        src={logo.src}
        alt="MaaS:PAL logo"
        className="maaspal-page-header__logo"
        onClick={() => secretClick() && toggle(logo.name)}
      />
      <Title headingLevel="h1" size="2xl" className="maaspal-page-header__title">
        MaaS:PAL
      </Title>
    </div>
  );
}

export function ScenariosPage() {
  const navigate = useNavigate();
  const [triggerScenario, setTriggerScenario] = useState<Scenario | null>(null);

  return (
    <PageSection>
      <PageIntro title="Scenarios">
        Each scenario asks one question about your MaaS setup. Pick one to configure and run it.
      </PageIntro>
      <ScenarioCatalog onRun={setTriggerScenario} />

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
    </PageSection>
  );
}

export function RunsPage() {
  const navigate = useNavigate();
  return (
    <PageSection>
      <PageIntro
        title="Runs"
        actions={
          <Button variant="secondary" onClick={() => navigate(SCENARIOS_PATH)}>
            Run a scenario
          </Button>
        }
      >
        Every scenario run, newest first. Open one for its results, checks and logs.
      </PageIntro>
      <RunHistory onViewRun={(runId) => navigate(`${RUNS_PATH}/${runId}`)} />
    </PageSection>
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
    <PalProvider>
      <div className="community-plugin-layout">
        {/* [SHARED] Do not remove — all community plugins must display the CommunityBanner */}
        <CommunityBanner />
        <div className="community-plugin-content maaspal-plugin">
          <PageHeader />
          <PalPet />
          <Routes>
            <Route path="/" element={<Navigate to={SCENARIOS_PATH} replace />} />
            <Route path="scenarios" element={<ScenariosPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="runs/:runId" element={<RunDetailPage />} />
            <Route path="overview/*" element={<MaasOverviewPage />} />
            {/* The MaaS overview's old path, kept for bookmarks. */}
            <Route path="setup/*" element={<Navigate to={OVERVIEW_PATH} replace />} />
            <Route path="*" element={<Navigate to={SCENARIOS_PATH} replace />} />
          </Routes>
        </div>
      </div>
    </PalProvider>
  );
}

export default App;
