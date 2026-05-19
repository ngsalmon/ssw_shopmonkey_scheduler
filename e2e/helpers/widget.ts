/**
 * Page-object wrapper over the scheduling widget. Centralizes selectors so a
 * UI rename in one place doesn't ripple through every spec.
 */

import { expect, type Locator, type Page } from '@playwright/test';

export class WidgetPage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(query: string = ''): Promise<void> {
    const url = query ? `/${query}` : '/';
    // /services fires from init(). Wait for the response before continuing.
    const servicesPromise = this.page.waitForResponse(
      (resp) => resp.url().includes('/services') && resp.status() === 200,
      { timeout: 10_000 },
    );
    await this.page.goto(url);
    await servicesPromise;
    // Categories render after the response resolves.
    await this.page.locator('#categoryTabs .category-tab').first().waitFor({
      state: 'visible',
      timeout: 5_000,
    });
  }

  categoryTab(label: string): Locator {
    return this.page.locator('#categoryTabs .category-tab').filter({ hasText: label });
  }

  async selectCategory(label: string): Promise<void> {
    await this.categoryTab(label).click();
  }

  filterChip(filterName: string, value: string): Locator {
    return this.page.locator(
      `.filter-chip[data-filter="${filterName}"][data-value="${value}"]`,
    );
  }

  async applyFilter(filterName: string, value: string): Promise<void> {
    await this.filterChip(filterName, value).click();
  }

  serviceCards(): Locator {
    return this.page.locator('#servicesContainer .service-card');
  }

  async serviceCardTitles(): Promise<string[]> {
    const cards = this.serviceCards();
    const count = await cards.count();
    const titles: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await cards.nth(i).locator('.card-title').textContent();
      if (text) titles.push(text.trim());
    }
    return titles;
  }

  async serviceCardVehicleSizes(): Promise<string[]> {
    const cards = this.serviceCards();
    const count = await cards.count();
    const sizes: string[] = [];
    for (let i = 0; i < count; i++) {
      const vehicleEl = cards.nth(i).locator('.vehicle-size');
      if ((await vehicleEl.count()) > 0) {
        const text = await vehicleEl.textContent();
        if (text) sizes.push(text.trim());
      }
    }
    return sizes;
  }

  async serviceIds(): Promise<string[]> {
    const cards = this.serviceCards();
    const count = await cards.count();
    const ids: string[] = [];
    for (let i = 0; i < count; i++) {
      const id = await cards.nth(i).getAttribute('data-service-id');
      if (id) ids.push(id);
    }
    return ids;
  }

  serviceCard(serviceId: string): Locator {
    return this.page.locator(`.service-card[data-service-id="${serviceId}"]`);
  }

  /**
   * Click a service card by ID, switching category tabs as needed to find it.
   * The widget only renders cards for the active category.
   */
  async selectServiceById(serviceId: string): Promise<void> {
    const tabs = this.page.locator('#categoryTabs .category-tab');
    const tabCount = await tabs.count();
    for (let i = 0; i < tabCount; i++) {
      await tabs.nth(i).click();
      const card = this.serviceCard(serviceId);
      if (await card.count() > 0 && await card.isVisible()) {
        await card.click();
        return;
      }
    }
    throw new Error(
      `Service card not found in any category tab: data-service-id="${serviceId}"`,
    );
  }

  nextBtn(): Locator {
    return this.page.locator('#nextBtn');
  }

  async clickNext(): Promise<void> {
    await this.nextBtn().click();
  }

  searchInput(): Locator {
    return this.page.locator('#serviceSearch');
  }

  async search(text: string): Promise<void> {
    await this.searchInput().fill(text);
    // The widget debounces search by 150ms. Wait past it, then for the next
    // tick of rendering to settle.
    await this.page.waitForTimeout(200);
  }

  // Calendar step
  calendarGrid(): Locator {
    return this.page.locator('#calendarGrid');
  }

  async pickCalendarDate(date: Date): Promise<void> {
    const day = date.getDate();
    const monthYear = date.toLocaleString('en-US', { month: 'long', year: 'numeric' });
    await expect(this.page.locator('#calendarTitle')).toContainText(monthYear);
    // Match only enabled, non-empty day cells - the calendar pads leading/trailing
    // weeks with disabled buttons that share day numbers.
    await this.calendarGrid()
      .locator('.calendar-day:not(.disabled):not(.empty)')
      .filter({ hasText: new RegExp(`^${day}$`) })
      .first()
      .click();
  }

  // Time slot step
  timeSlotsContainer(): Locator {
    return this.page.locator('#timeSlotsContainer');
  }

  timeSlots(): Locator {
    return this.timeSlotsContainer().locator('.time-slot');
  }

  async selectFirstAvailableSlot(): Promise<string> {
    const slot = this.timeSlots().first();
    const label = (await slot.textContent()) ?? '';
    await slot.click();
    return label.trim();
  }

  // Customer form
  async fillCustomerForm(opts: {
    firstName: string;
    lastName: string;
    email?: string;
    phone?: string;
  }): Promise<void> {
    await this.page.locator('#firstName').fill(opts.firstName);
    await this.page.locator('#lastName').fill(opts.lastName);
    if (opts.email) await this.page.locator('#email').fill(opts.email);
    if (opts.phone) await this.page.locator('#phone').fill(opts.phone);
  }

  // Vehicle form
  async fillVehicleForm(opts: {
    year: number;
    make: string;
    model: string;
    vin?: string;
  }): Promise<void> {
    await this.page.locator('#vehicleYear').fill(String(opts.year));
    await this.page.locator('#vehicleMake').fill(opts.make);
    await this.page.locator('#vehicleModel').fill(opts.model);
    if (opts.vin) await this.page.locator('#vehicleVin').fill(opts.vin);
  }

  successPanel(): Locator {
    return this.page.locator('#successPanel');
  }

  confirmationNumber(): Locator {
    return this.page.locator('#confirmationNumber');
  }
}
