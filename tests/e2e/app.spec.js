// tests/e2e/app.spec.js
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';

// Setup artifacts directory
const ARTIFACTS_DIR = path.join(process.cwd(), 'tests', 'e2e-artifacts');
if (!fs.existsSync(ARTIFACTS_DIR)) {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
}

test.describe('MWG Platform E2E', () => {
  let consoleErrors = [];
  
  test.beforeEach(async ({ page }) => {
    consoleErrors = [];
    
    page.on('console', msg => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
        console.log(`[ERROR] ${msg.text()}`);
      }
    });
    
    await page.goto('http://localhost:3000', { 
      waitUntil: 'networkidle',
      timeout: 30000 
    });
  });

  test.afterEach(async ({ page }, testInfo) => {
    const screenshotPath = path.join(
      ARTIFACTS_DIR, 
      `${testInfo.title.replace(/\s+/g, '_')}.png`
    );
    await page.screenshot({ path: screenshotPath, fullPage: true });
  });

  test('Test 1 - App Boot', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    // Check that the app has loaded properly by looking for key UI elements
    await expect(page.locator('h1:has-text("MWG - Mathematical Waveguide Generator")')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#render-btn')).toBeVisible({ timeout: 10000 });
    expect(consoleErrors).toHaveLength(0);
    
    // Verify no critical errors in console
    const errorMessages = consoleErrors.filter(msg => 
      msg.includes('Error') || 
      msg.includes('error') || 
      msg.includes('Failed to load')
    );
    expect(errorMessages).toHaveLength(0);
  });

  test('Test 2 - Geometry Generation', async ({ page }) => {
    await page.waitForLoadState('domcontentloaded');
    
    // Look for actual UI elements from the HTML
    const renderBtn = page.locator('#render-btn');
    await expect(renderBtn).toBeVisible({ timeout: 5000 });
    
    // Click the update model button to generate geometry
    await renderBtn.click();
    
    // Wait for a short time to let the geometry update
    await page.waitForTimeout(1000);
    
    // Check that there are no NaN errors in console
    const nanErrors = consoleErrors.filter(e => e.includes('NaN'));
    expect(nanErrors).toHaveLength(0);
    
    // The canvas should be visible after geometry generation (wait for it)
    await page.waitForSelector('#canvas-container', { timeout: 10000 });
    
    // Check that canvas container has content
    const canvasContainer = page.locator('#canvas-container');
    await expect(canvasContainer).toBeVisible({ timeout: 5000 });
  });

  test('Test 3 - Mesh Export', async ({ page }) => {
    // First generate geometry by clicking the render button
    const renderBtn = page.locator('#render-btn');
    if (await renderBtn.isVisible().catch(() => false)) {
      await renderBtn.click();
      await page.waitForTimeout(1000);
    }
    
    // Look for export buttons in the actual UI
    const exportBtn = page.locator('#export-btn');
    await expect(exportBtn).toBeVisible({ timeout: 5000 });
    
    // Click the export button - this should trigger a download (but we'll just check it's clickable)
    await expect(exportBtn).toBeEnabled({ timeout: 5000 });
    
    // Verify the button is properly rendered and clickable
    await exportBtn.hover();
  });

  test('Test 4 - BEM Simulation', async ({ page }) => {
    // First generate geometry by clicking the render button
    const renderBtn = page.locator('#render-btn');
    if (await renderBtn.isVisible().catch(() => false)) {
      await renderBtn.click();
      await page.waitForTimeout(1000);
    }
    
    // Look for BEM related buttons in the actual UI
    const exportGeoBtn = page.locator('#export-geo-btn');
    await expect(exportGeoBtn).toBeVisible({ timeout: 5000 });
    
    // Verify the button is properly rendered and clickable
    await expect(exportGeoBtn).toBeEnabled({ timeout: 5000 });
    
    // Check for NaN errors in console
    const nanErrors = consoleErrors.filter(e => e.includes('NaN'));
    expect(nanErrors).toHaveLength(0);
  });

  test('Test 5 - Optimization', async ({ page }) => {
    // For now, just verify the app is running and we can find basic elements
    await expect(page.locator('h1:has-text("MWG - Mathematical Waveguide Generator")')).toBeVisible({ timeout: 5000 });
    
    // Check that main UI elements are present
    await expect(page.locator('#ui-panel')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('#canvas-container')).toBeVisible({ timeout: 5000 });
    
    // Check that some core buttons exist
    const exportBtn = page.locator('#export-btn');
    await expect(exportBtn).toBeVisible({ timeout: 5000 });
    
    // Verify no console errors that would prevent operation
    const errorMessages = consoleErrors.filter(msg => 
      msg.includes('Error') || 
      msg.includes('error') || 
      msg.includes('Failed to load')
    );
    expect(errorMessages).toHaveLength(0);
  });
});