import palLogo from './assets/pal-logo.png';
import { useEffect, useState } from 'react';
import { Grid, GridItem, Page, PageSection } from '@patternfly/react-core';
import { MaasOverviewPage } from './components/maas/MaasOverviewPage';
import { RunDetail } from './components/RunDetail';
import { RunHistory } from './components/RunHistory';
import { RunTrigger } from './components/RunTrigger';
import { ScenarioList } from './components/ScenarioList';
import type { Scenario } from './api/client';

type AppView = { page: 'home' } | { page: 'maas' } | { page: 'run'; runId: string };

function getViewFromHash(): AppView {
  const match = window.location.hash.match(/^#run\/(.+)$/);
  if (match) return { page: 'run', runId: match[1] };
  if (window.location.hash === '#maas') return { page: 'maas' };
  return { page: 'home' };
}

function navigateTo(view: AppView): void {
  const hash = view.page === 'run' ? `#run/${view.runId}` : view.page === 'maas' ? '#maas' : '';
  window.history.pushState({}, '', window.location.pathname + hash);
}

function MaaspalMasthead({
  activeNav,
  onNavigate,
}: {
  activeNav: 'home' | 'maas' | null;
  onNavigate: (page: 'home' | 'maas') => void;
}) {
  return (
    <header className="maaspal-masthead">
      <div className="maaspal-masthead__logo">
        <img src={palLogo} alt="MaaS:PAL" className="maaspal-masthead__logo-img" />
      </div>
      <div className="maaspal-masthead__divider" />
      <span className="maaspal-masthead__subtitle">RHOAI Test Harness</span>
      {activeNav !== null && (
        <nav className="maaspal-topnav">
          <button
            className={`maaspal-topnav__link${activeNav === 'home' ? ' maaspal-topnav__link--active' : ''}`}
            onClick={() => onNavigate('home')}
          >
            Runs
          </button>
          <button
            className={`maaspal-topnav__link${activeNav === 'maas' ? ' maaspal-topnav__link--active' : ''}`}
            onClick={() => onNavigate('maas')}
          >
            MaaS Setup
          </button>
        </nav>
      )}
    </header>
  );
}

function App() {
  const [view, setView] = useState<AppView>(getViewFromHash);
  const [triggerScenario, setTriggerScenario] = useState<Scenario | null>(null);
  const [historyKey, setHistoryKey] = useState(0);

  useEffect(() => {
    function onPopState() {
      setView(getViewFromHash());
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  function handleRunStart(runId: string) {
    setTriggerScenario(null);
    setHistoryKey((k) => k + 1);
    const next: AppView = { page: 'run', runId };
    navigateTo(next);
    setView(next);
  }

  function handleViewRun(runId: string) {
    const next: AppView = { page: 'run', runId };
    navigateTo(next);
    setView(next);
  }

  function handleBack() {
    window.history.back();
  }

  function handleNavigate(page: 'home' | 'maas') {
    const next: AppView = { page };
    navigateTo(next);
    setView(next);
  }

  // One <Page> for every view, with the masthead as its header: PatternFly's
  // Page fills the viewport and owns the only scroll container, so the
  // masthead must live inside it — rendered above a full-height Page, it
  // pushed the document past the viewport and gave every view two scrollbars.
  const activeNav = view.page === 'run' ? null : view.page;
  const masthead = <MaaspalMasthead activeNav={activeNav} onNavigate={handleNavigate} />;

  if (view.page === 'run') {
    return (
      <Page header={masthead}>
        <RunDetail runId={view.runId} onBack={handleBack} />
      </Page>
    );
  }

  if (view.page === 'maas') {
    return (
      <Page header={masthead}>
        <MaasOverviewPage />
      </Page>
    );
  }

  return (
    <>
      <Page header={masthead}>
        <PageSection>
          <Grid hasGutter>
            <GridItem span={4}>
              <ScenarioList onRun={setTriggerScenario} />
            </GridItem>
            <GridItem span={8}>
              <div style={{ maxHeight: 'calc(100vh - 120px)', overflowY: 'auto' }}>
                <RunHistory
                  key={historyKey}
                  onViewRun={handleViewRun}
                />
              </div>
            </GridItem>
          </Grid>
        </PageSection>
      </Page>

      {triggerScenario !== null && (
        <RunTrigger
          scenario={triggerScenario}
          onConfirm={handleRunStart}
          onCancel={() => setTriggerScenario(null)}
        />
      )}
    </>
  );
}

export default App;
