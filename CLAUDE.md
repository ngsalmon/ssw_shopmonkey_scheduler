# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shopmonkey Scheduling Widget - A Python FastAPI backend with HTML/JavaScript widget frontend for appointment scheduling. Integrates with Shopmonkey API (automotive service management) and Google Sheets API (technician/department mapping).

## Common Commands

```bash
# Run the development server
python main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_availability.py

# Run a specific test
pytest tests/test_availability.py::test_calculate_available_slots

# Run tests with verbose output
pytest -v

# Build Docker image
docker build -t shopmonkey-scheduler .

# Run Docker container
docker run -p 8080:8080 --env-file .env shopmonkey-scheduler
```

## Architecture

```
Client (widget.html/js) → FastAPI (main.py) → Shopmonkey Client + Sheets Client
                                ↓
                        availability.py (slot calculation)
```

**Core Components:**

- **main.py** - FastAPI application with REST endpoints (`/services`, `/availability`, `/book`, `/health`, `/schedule`)
- **shopmonkey_client.py** - Async HTTP client (httpx) for Shopmonkey API v3: services, appointments, customers, vehicles
- **sheets_client.py** - Google Sheets API client for technician/department skills matrix (uses 5-min TTL cache)
- **availability.py** - Business logic for calculating available time slots, handling multi-day services (>5 hours)
- **config.yaml** - Business hours and slot configuration
- **static/** - Frontend widget (HTML/JS/CSS) for customer-facing scheduling interface

**Data Flow - Availability Check:**
1. Get service from Shopmonkey (extracts department from labels)
2. Query Google Sheets for techs qualified in that department
3. Fetch existing appointments for those techs on the date
4. Calculate available slots respecting business hours, service duration, existing bookings

**Data Flow - Booking:**
1. Re-validate slot availability (prevents race conditions)
2. Find/create customer and vehicle in Shopmonkey
3. Create appointment assigned to first available tech
4. Return confirmation number (format: SM-YYYYMMDD-XXXXXX)

## Key Technical Details

- **Async patterns**: FastAPI endpoints and Shopmonkey client are async; Sheets client is sync
- **Department mapping**: Uses Shopmonkey service labels (not Google Sheets service mapping)
- **Multi-day services**: availability.py handles services that span multiple business days
- **Cache clearing**: Call `sheets_client.clear_cache()` after Google Sheets updates
- **Environment variables**: SHOPMONKEY_API_TOKEN, GOOGLE_SHEETS_ID, GOOGLE_APPLICATION_CREDENTIALS (see .env.example)

## Testing

Tests use pytest with pytest-asyncio. Mock fixtures are provided for Shopmonkey and Sheets clients to avoid external API calls during testing.
