const HtmlWebpackPlugin = require('html-webpack-plugin');
const { ModuleFederationPlugin } = require('webpack').container;
const path = require('path');
const { 'module-federation': moduleFederation } = require('../package.json');

const remoteEntry = path.posix.join(moduleFederation.remoteEntry);

module.exports = {
  entry: './src/index.ts',
  output: {
    publicPath: 'auto',
    filename: '[name].[contenthash].js',
  },
  module: {
    rules: [
      {
        test: /\.tsx?$/,
        use: {
          loader: 'ts-loader',
          options: {
            transpileOnly: true,
            compilerOptions: {
              noEmit: false,
            },
          },
        },
        include: /src/,
        exclude: /\.test\.(ts|tsx)$/,
      },
      {
        test: /\.css$/,
        use: [
          {
            loader: 'style-loader',
          },
          {
            loader: 'css-loader',
          },
        ],
      },
      {
        test: /\.(png|jpg|jpeg|gif|svg|woff2?|eot|ttf|otf)$/i,
        type: 'asset/resource',
      },
    ],
  },
  resolve: {
    extensions: ['.js', '.ts', '.tsx', '.jsx'],
    alias: {
      '~': path.resolve(__dirname, '../src'),
    },
  },
  plugins: [
    // index.html is only used for standalone development (the dashboard loads
    // remoteEntry.js instead); absolute script paths keep deep links like
    // /maaspal/runs/<id> loading from the server root.
    new HtmlWebpackPlugin({
      template: path.resolve(__dirname, '../src/index.html'),
      publicPath: '/',
    }),
    new ModuleFederationPlugin({
      name: moduleFederation.name, // must match plugin.yaml remote.spec.name
      filename: remoteEntry,
      exposes: {
        './extensions': './src/rhoai/extensions.ts',
        './Icon': './src/app/components/MaaspalNavIcon.tsx',
      },
      shared: {
        react: {
          singleton: true,
          requiredVersion: '^18',
        },
        'react-dom': {
          singleton: true,
          requiredVersion: '^18',
        },
        'react-router-dom': {
          singleton: true,
          requiredVersion: '^7',
        },
        '@patternfly/react-core': {
          singleton: true,
          requiredVersion: '^6',
        },
        '@openshift/dynamic-plugin-sdk': {
          singleton: true,
          requiredVersion: '^5',
        },
      },
    }),
  ],
};
