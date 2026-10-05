import type { ReactNode } from 'react';
import { Title } from '@patternfly/react-core';

/** A page's title and one-line explanation, with an optional action on the right. */
export function PageIntro({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="maaspal-page-intro">
      <div>
        <Title headingLevel="h2" size="xl">{title}</Title>
        <p className="maaspal-page-intro__text">{children}</p>
      </div>
      {actions}
    </div>
  );
}
