// Standalone development entry only (`npm run start:dev`). Inside the RHOAI
// dashboard the host loads ./extensions and renders App for /maaspal/* under
// its own router; this mounts it the same way, so App's absolute links
// (/maaspal/runs/...) work identically in both.
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Page } from '@patternfly/react-core';
import '@patternfly/react-core/dist/styles/base.css';
import App from './app/App';

const root = ReactDOM.createRoot(document.getElementById('root')!);

root.render(
  <React.StrictMode>
    <BrowserRouter>
      {/* The dashboard renders plugins inside its own <Page>; PageSection's
          padding comes from there. */}
      <Page>
        <Routes>
          {/* must match the route prefix in src/rhoai/extensions.ts */}
          <Route path="/maaspal/*" element={<App />} />
          <Route path="*" element={<Navigate to="/maaspal" replace />} />
        </Routes>
      </Page>
    </BrowserRouter>
  </React.StrictMode>,
);
