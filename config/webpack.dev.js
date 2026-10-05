const { merge } = require('webpack-merge');
const common = require('./webpack.common.js');

module.exports = merge(common, {
  mode: 'development',
  devtool: 'eval-source-map',
  devServer: {
    port: parseInt(process.env.PORT, 10) || 9500,
    historyApiFallback: true,
    hot: true,
    proxy: [
      {
        context: ['/maaspal/api'], // BFF (FastAPI, `make dev-bff`) — must come before the general proxy
        target: 'http://localhost:3000',
        pathRewrite: { '^/maaspal/api': '/api' },
      },
      // The dashboard, for everything else under the route prefix. STANDALONE=true
      // skips it so the plugin's pages can be opened on :9500 with no dashboard.
      ...(process.env.STANDALONE === 'true'
        ? []
        : [
            {
              context: ['/maaspal'],
              target: 'http://localhost:8443',
              pathRewrite: { '^/maaspal': '/maaspal' },
            },
          ]),
    ],
  },
  optimization: {
    runtimeChunk: false,
    splitChunks: false,
  },
});
