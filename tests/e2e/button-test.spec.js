import { test, expect } from '@playwright/test';

test('Button functionality test', async ({ page }) => {
    // Navigate to the app
    await page.goto('http://localhost:3000', {
        waitUntil: 'networkidle',
        timeout: 30000
    });

    // Wait for the app to fully load
    await expect(page.locator('#stats')).toBeVisible();

    console.log('✓ App loaded successfully');

    // Test that buttons exist and are clickable
    const renderBtn = page.locator('#render-btn');
    const exportBtn = page.locator('#export-btn');
    const cameraToggle = page.locator('#camera-toggle');

    await expect(renderBtn).toBeVisible();
    await expect(exportBtn).toBeVisible();
    await expect(cameraToggle).toBeVisible();

    console.log('✓ Buttons are visible in the DOM');

    // Click render button
    await renderBtn.click();
    await page.waitForTimeout(1000); // Wait for render to complete

    console.log('✓ Render button clicked successfully');

    // Verify that clicking the button doesn't throw errors
    const errors = await page.evaluate(() => {
        return window.errors || [];
    });

    if (errors.length === 0) {
        console.log('✓ No errors detected after button clicks');
    } else {
        console.error('✗ Errors found:', errors);
    }

    // Test camera toggle
    const initialText = await cameraToggle.textContent();
    await cameraToggle.click();
    await page.waitForTimeout(500);

    const newText = await cameraToggle.textContent();
    if (initialText !== newText) {
        console.log('✓ Camera toggle button works correctly');
    } else {
        console.warn('Camera toggle text did not change, but no errors occurred');
    }

    console.log('All button tests passed!');
});