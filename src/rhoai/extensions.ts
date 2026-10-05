// [SHARED] Common section for all community plugins — never changes across plugins.
// Do not change the id or name: all community plugins share this section
// so they appear grouped together in the dashboard sidebar.
export const communityPluginsSectionExtension = {
  type: 'app.navigation/section' as const,
  properties: {
    id: 'community-plugins', // [SHARED] common section for all community plugins
    title: 'Community plugins', // [SHARED]
    group: '9_plugins', // [SHARED]
    iconRef: () => import(/* webpackMode: "eager" */ './CommunityNavIcon'),
  },
};

// [PLUGIN-SPECIFIC] Everything below is specific to MaaS:PAL

export const maaspalAreaExtension = {
  type: 'app.area' as const,
  properties: {
    id: 'maaspal',
    featureFlags: [] as string[],
  },
};

export const maaspalSectionExtension = {
  type: 'app.navigation/section' as const,
  properties: {
    id: 'maaspal',
    title: 'MaaS:PAL',
    group: '1_maaspal',
    section: 'community-plugins', // [SHARED] must match communityPluginsSectionExtension.id
    iconRef: () => import(/* webpackMode: "eager" */ '~/app/components/MaaspalNavIcon'),
  },
};

export const scenariosNavExtension = {
  type: 'app.navigation/href' as const,
  properties: {
    id: 'maaspal-scenarios',
    title: 'Scenarios',
    href: '/maaspal/scenarios',
    section: 'maaspal',
    path: '/maaspal/scenarios/*',
  },
};

export const runsNavExtension = {
  type: 'app.navigation/href' as const,
  properties: {
    id: 'maaspal-runs',
    title: 'Runs',
    href: '/maaspal/runs',
    section: 'maaspal',
    path: '/maaspal/runs/*',
  },
};

export const maasSetupNavExtension = {
  type: 'app.navigation/href' as const,
  properties: {
    id: 'maaspal-setup',
    title: 'MaaS setup',
    href: '/maaspal/setup',
    section: 'maaspal',
    path: '/maaspal/setup/*',
  },
};

export const maaspalRouteExtension = {
  type: 'app.route' as const,
  properties: {
    path: '/maaspal/*', // top-level route prefix; App.tsx routes below it
    component: () => import(/* webpackMode: "eager" */ '~/app/App'),
  },
};

export const extensions = [
  communityPluginsSectionExtension,
  maaspalAreaExtension,
  maaspalSectionExtension,
  scenariosNavExtension,
  runsNavExtension,
  maasSetupNavExtension,
  maaspalRouteExtension,
];

export default extensions;
