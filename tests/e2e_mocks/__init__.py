"""E2E mock clients and test-only endpoints.

Only imported when E2E_MODE=1 is set at server start. Provides in-process
fakes for ShopmonkeyClient and SheetsClient so the widget can be exercised
end-to-end without real external credentials.
"""
