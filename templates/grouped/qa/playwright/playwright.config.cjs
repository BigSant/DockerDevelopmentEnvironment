const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const dataDirectory = process.env.PLAYWRIGHT_DATA_DIR || path.resolve(__dirname, '../../../data/playwright');

module.exports = defineConfig({
  testDir: process.env.PLAYWRIGHT_TEST_DIR || './tests',
  outputDir: path.join(dataDirectory, 'test-results'),
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(dataDirectory, 'report'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL,
    ignoreHTTPSErrors: process.env.PLAYWRIGHT_IGNORE_HTTPS_ERRORS === 'true',
    trace: 'retain-on-failure',
  },
});
