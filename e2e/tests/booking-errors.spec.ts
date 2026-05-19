/**
 * Booking error paths: race conditions, backend errors, form validation.
 */

import { expect, test } from '@playwright/test';
import { nextWeekday, toISODate } from '../helpers/dates';
import { resetToDefaults, setScenario } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

const TINT_FULL_COUPE_CARBON = 'svc_tint_full_coupe_carbon';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('booking rejected when slot is taken by another customer between availability check and submit', async ({
  page,
  request,
}) => {
  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById(TINT_FULL_COUPE_CARBON);
  await widget.clickNext();
  const target = nextWeekday(1);
  await widget.pickCalendarDate(target);
  await widget.clickNext();
  await widget.selectFirstAvailableSlot();
  await widget.clickNext();

  // Race: another booking grabs both qualified techs for the whole day
  // before we submit. main.py:/book re-validates inside the lock and 409s.
  const dateStr = toISODate(target);
  await setScenario(request, {
    appointments: [
      {
        technician_id: 'tech_alex',
        start_date: `${dateStr}T09:00:00.000-06:00`,
        end_date: `${dateStr}T17:30:00.000-06:00`,
        name: 'Race winner',
      },
      {
        technician_id: 'tech_cam',
        start_date: `${dateStr}T09:00:00.000-06:00`,
        end_date: `${dateStr}T17:30:00.000-06:00`,
        name: 'Race winner',
      },
    ],
  });

  await widget.fillCustomerForm({ firstName: 'Late', lastName: 'Arriver' });
  await widget.clickNext();
  await widget.fillVehicleForm({ year: 2020, make: 'Honda', model: 'Civic' });
  await widget.clickNext();
  // Step 6 review → final Book Appointment click triggers /book, which
  // re-validates the slot inside main.py's booking_lock and 409s.
  await widget.clickNext();

  // Expect an error toast; success panel should not appear.
  await expect(widget.successPanel()).toBeHidden({ timeout: 3_000 });
  await expect(page.locator('#toastContainer .toast')).toBeVisible({ timeout: 5_000 });
});

test('backend 502 from Shopmonkey during /services is surfaced to the user', async ({
  page,
  request,
}) => {
  await setScenario(request, {
    errors: { get_bookable_canned_services: { status_code: 502, message: 'upstream down' } },
  });
  const servicesResponse = page.waitForResponse((resp) => resp.url().includes('/services'));
  await page.goto('/');
  await servicesResponse;
  // The widget surfaces /services failures via a toast notification.
  await expect(page.locator('#toastContainer .toast')).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('#toastContainer .toast')).toContainText(/(failed|unable|error|try again)/i);
});

test('form validation keeps Next disabled until required fields are filled', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById(TINT_FULL_COUPE_CARBON);
  await widget.clickNext();
  await widget.pickCalendarDate(nextWeekday(1));
  await widget.clickNext();
  await widget.selectFirstAvailableSlot();
  await widget.clickNext();

  // Step 4: customer form. Next is disabled until first + last name present.
  await expect(page.locator('#step4')).toHaveClass(/active/);
  await expect(widget.nextBtn()).toBeDisabled();

  // Type only first name; still missing last name -> still disabled.
  await page.locator('#firstName').fill('Bo');
  await expect(widget.nextBtn()).toBeDisabled();

  // Fill last name -> Next enables.
  await page.locator('#lastName').fill('Booker');
  await expect(widget.nextBtn()).toBeEnabled();
});
