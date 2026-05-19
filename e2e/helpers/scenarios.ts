/**
 * Helpers for posting scenario state to the test-only /test/state endpoint.
 */

import type { APIRequestContext } from '@playwright/test';

export interface TechFixture {
  tech_id: string;
  tech_name: string;
  departments: Record<string, number>;
  status?: string;
  active_in_shopmonkey?: boolean;
}

export interface ServiceFixture {
  id: string;
  name: string;
  totalCents?: number | null;
  bookable?: boolean;
  labels?: string[];
  laborHours?: number | null;
}

export interface AppointmentFixture {
  technician_id: string | null;
  start_date: string; // ISO with TZ offset (e.g. "2026-05-20T10:00:00.000-06:00")
  end_date: string;
  customer_id?: string;
  vehicle_id?: string;
  name?: string;
}

export interface ErrorInjection {
  status_code?: number;
  message?: string;
  network?: boolean;
  timeout?: boolean;
}

export interface ScenarioPayload {
  reset?: boolean;
  load_default?: boolean;
  services?: ServiceFixture[];
  techs?: TechFixture[];
  appointments?: AppointmentFixture[];
  errors?: Record<string, ErrorInjection>;
  config?: Record<string, unknown>;
}

/** Reset to defaults: full fixture (all categories) and original config.yaml. */
export async function resetToDefaults(request: APIRequestContext): Promise<void> {
  const response = await request.post('/test/state/reset');
  if (!response.ok()) {
    throw new Error(`reset failed: ${response.status()} ${await response.text()}`);
  }
}

/** Apply a partial scenario on top of current state. */
export async function setScenario(
  request: APIRequestContext,
  payload: ScenarioPayload,
): Promise<void> {
  const response = await request.post('/test/state', { data: payload });
  if (!response.ok()) {
    throw new Error(`setScenario failed: ${response.status()} ${await response.text()}`);
  }
}

/** Read current state (useful for booking-payload assertions). */
export async function getState(request: APIRequestContext): Promise<{
  services: ServiceFixture[];
  techs: TechFixture[];
  appointments: Array<Record<string, unknown>>;
  errors: Record<string, ErrorInjection>;
  recorded_create_appointment_payloads: Array<Record<string, unknown>>;
  config: Record<string, unknown>;
}> {
  const response = await request.get('/test/state');
  if (!response.ok()) {
    throw new Error(`getState failed: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}

/** Convenience: allow the Detail category to render by clearing disabled_departments. */
export async function enableAllDepartments(request: APIRequestContext): Promise<void> {
  await setScenario(request, { config: { disabled_departments: {} } });
}
