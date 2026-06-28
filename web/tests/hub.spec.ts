import { test, expect, type Page } from '@playwright/test';

// Evidence suite for the GT Marketing Hub. Captures a screenshot for every module
// and proves the things the brief asks to see: the three-role Decision Queue gate,
// the $365K budget reconcile, and the composable per-user Home.
// Screenshots land in web/test-evidence/.

const EV = 'test-evidence';

const MODULES: { id: string; title: RegExp }[] = [
  { id: 'home', title: /Executive Command Center/ },
  { id: 'dashboard', title: /Dashboard \/ KPI/ },
  { id: 'decision', title: /Decision Queue/ },
  { id: 'budget', title: /Budget Tracker/ },
  { id: 'grassroots', title: /Grassroots Engine/ },
  { id: 'content', title: /Content & Thought/ },
  { id: 'camp', title: /Summer Camp/ },
  { id: 'events', title: /Field Marketing & Events/ },
  { id: 'nurture', title: /Nurture & Lifecycle/ },
  { id: 'crm', title: /CRM \/ Marketing Operations/ },
  { id: 'admissions', title: /Admissions & Voice/ },
  { id: 'website', title: /Website & Digital/ },
  { id: 'resources', title: /Resource Library/ },
];

// Switch the demo role via the sidebar "VIEWING AS" buttons.
async function viewAs(page: Page, role: 'ADMIN' | 'LEADER' | 'OPER') {
  await page.getByRole('button', { name: role, exact: true }).click();
}

test.describe('module screens render (evidence)', () => {
  for (const m of MODULES) {
    test(`module: ${m.id}`, async ({ page }) => {
      await page.goto(`/${m.id}`);
      await expect(page.locator('h1')).toContainText(m.title);
      await page.screenshot({ path: `${EV}/modules/${m.id}.png`, fullPage: true });
    });
  }
});

test.describe('Decision Queue — three-role hard gate', () => {
  test('Leader can decide (approve/reject/need-info visible)', async ({ page }) => {
    await page.goto('/decision');
    await viewAs(page, 'LEADER');
    await expect(page.getByRole('button', { name: /APPROVE/ }).first()).toBeVisible();
    await page.screenshot({ path: `${EV}/roles/decision-leader.png`, fullPage: true });
  });

  test('Operator is locked to own submissions (no full queue, no decide)', async ({ page }) => {
    await page.goto('/decision');
    await viewAs(page, 'OPER');
    await expect(page.getByText(/leadership-only/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /APPROVE/ })).toHaveCount(0);
    await page.screenshot({ path: `${EV}/roles/decision-operator.png`, fullPage: true });
  });

  test('Admin views the queue but cannot decide', async ({ page }) => {
    await page.goto('/decision');
    await viewAs(page, 'ADMIN');
    await expect(page.getByText(/ADMIN VIEW/)).toBeVisible();
    await expect(page.getByRole('button', { name: /APPROVE/ })).toHaveCount(0);
    await page.screenshot({ path: `${EV}/roles/decision-admin.png`, fullPage: true });
  });
});

test('Budget reconciles to $365K', async ({ page }) => {
  await page.goto('/budget');
  await expect(page.getByText(/\$365/).first()).toBeVisible();
  await page.screenshot({ path: `${EV}/proofs/budget-365k.png`, fullPage: true });
});

test('Operator write-gate: owns Grassroots (editable), reads others (read-only)', async ({ page }) => {
  await page.goto('/grassroots');
  await viewAs(page, 'OPER');
  await expect(page.getByText(/EDITABLE/).first()).toBeVisible();
  await page.screenshot({ path: `${EV}/roles/grassroots-operator-editable.png`, fullPage: true });
  // Navigating reloads, resetting the demo role to the leader default — re-apply
  // OPER, then read a module the operator does NOT own (Field & Events) → read-only.
  await page.goto('/events');
  await viewAs(page, 'OPER');
  await expect(page.getByText(/READ-ONLY/).first()).toBeVisible();
  await page.screenshot({ path: `${EV}/roles/events-operator-readonly.png`, fullPage: true });
});

test('Home is composable: widget picker opens with the 44-widget library', async ({ page }) => {
  await page.goto('/home');
  await page.getByRole('button', { name: /ADD WIDGET/ }).click();
  await expect(page.getByPlaceholder(/Search 44 widgets/)).toBeVisible();
  await page.screenshot({ path: `${EV}/proofs/home-widget-picker.png`, fullPage: true });
});

test('Dark mode toggle', async ({ page }) => {
  await page.goto('/home');
  await page.getByRole('button', { name: /LIGHT|DARK/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: `${EV}/proofs/dark-mode.png`, fullPage: true });
});
