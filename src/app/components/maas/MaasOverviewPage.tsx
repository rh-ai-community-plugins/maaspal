import { useState } from 'react';
import { PageSection, Tab, Tabs, TabTitleText } from '@patternfly/react-core';
import { PageIntro } from '../PageIntro';
import { AccessControlTab } from './AccessControlTab';
import { AccessSimulatorTab } from './AccessSimulatorTab';
import { AuthorizationPoliciesTab } from './AuthorizationPoliciesTab';
import { ModelsTab } from './ModelsTab';
import { NetworkingTab } from './NetworkingTab';
import { PlatformConfigTab } from './PlatformConfigTab';
import { RateLimitingTab } from './RateLimitingTab';
import { SubscriptionsTab } from './SubscriptionsTab';

type TabKey =
  | 'subscriptions'
  | 'models'
  | 'auth-policies'
  | 'access'
  | 'access-simulator'
  | 'rate-limiting'
  | 'networking'
  | 'platform';

export function MaasOverviewPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('subscriptions');

  return (
    <>
      <PageSection>
        <PageIntro title="MaaS overview">
          A read-only view of how MaaS is configured on this cluster: subscriptions, models, access and rate
          limiting. Nothing here changes the cluster.
        </PageIntro>
        <Tabs
          activeKey={activeTab}
          onSelect={(_evt, key) => setActiveTab(key as TabKey)}
          aria-label="MaaS overview sections"
        >
          <Tab eventKey="subscriptions" title={<TabTitleText>Subscriptions</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <SubscriptionsTab />
            </div>
          </Tab>
          <Tab eventKey="models" title={<TabTitleText>Models</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <ModelsTab />
            </div>
          </Tab>
          <Tab eventKey="auth-policies" title={<TabTitleText>Authorization Policies</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <AuthorizationPoliciesTab />
            </div>
          </Tab>
          <Tab eventKey="access" title={<TabTitleText>Access Control</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <AccessControlTab />
            </div>
          </Tab>
          <Tab eventKey="access-simulator" title={<TabTitleText>Access Simulator</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <AccessSimulatorTab />
            </div>
          </Tab>
          <Tab eventKey="rate-limiting" title={<TabTitleText>Rate Limiting</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <RateLimitingTab />
            </div>
          </Tab>
          <Tab eventKey="networking" title={<TabTitleText>Networking</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <NetworkingTab />
            </div>
          </Tab>
          <Tab eventKey="platform" title={<TabTitleText>Platform Config</TabTitleText>}>
            <div style={{ marginTop: '1rem' }}>
              <PlatformConfigTab />
            </div>
          </Tab>
        </Tabs>
      </PageSection>
    </>
  );
}
