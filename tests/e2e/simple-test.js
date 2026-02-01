// Simple test to verify Playwright setup works
import { test, expect } from '@playwright/test';

test('Basic test', async ({ page }) => {
  // Just verify that Playwright can run a simple test
  console.log('Playwright is working');
  expect(true).toBe(true);
});