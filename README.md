# Shopmonkey Scheduling Widget

A Python FastAPI backend with embeddable JavaScript widget for online appointment scheduling. Integrates with [Shopmonkey](https://www.shopmonkey.io/) (automotive service management) and Google Sheets (technician/department mapping).

## Features

- Real-time availability based on technician schedules and existing appointments
- Multi-day service support (vehicles staying overnight)
- Embeddable widget with multiple display modes
- Service pre-selection via URL parameters or embed attributes
- Email notifications on booking
- Mobile-responsive design

## Production URLs

- **Scheduler Widget**: https://scheduler.salmonspeedworx.com
- **API Base**: https://api.salmonspeedworx.com/scheduler

## Quick Start

### Prerequisites

- Python 3.10+
- Shopmonkey API token
- Google Cloud service account with Sheets API access
- Google Sheet with technician/department mapping

### Installation

```bash
# Clone the repository
git clone https://github.com/ngsalmon/ssw_shopmonkey_scheduler.git
cd ssw_shopmonkey_scheduler

# Install dependencies
pip install -r requirements.txt

# Copy environment file and configure
cp .env.example .env
# Edit .env with your credentials

# Run the development server
python main.py
```

The server starts at http://localhost:8080

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SHOPMONKEY_API_TOKEN` | Yes | Shopmonkey API token |
| `SHOPMONKEY_API_BASE_URL` | No | API base URL (default: https://api.shopmonkey.cloud) |
| `SHOPMONKEY_LOCATION_ID` | No | Location ID for multi-location shops |
| `GOOGLE_SHEETS_ID` | Yes | Google Sheet ID for technician mapping |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes | Path to Google service account JSON |
| `ALLOWED_ORIGINS` | No | CORS allowed origins (comma-separated) |
| `SMTP_HOST` | No | SMTP server for email notifications |
| `SMTP_PORT` | No | SMTP port (default: 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASSWORD` | No | SMTP password (use app password for Gmail) |
| `SMTP_USE_TLS` | No | Enable TLS (default: true) |
| `EMAIL_FROM` | No | From address for notifications |
| `NOTIFICATION_EMAIL` | No | Recipient for booking notifications |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Scheduling widget (HTML) |
| `/health` | GET | Health check |
| `/services` | GET | List available services |
| `/availability` | GET | Get available time slots |
| `/book` | POST | Book an appointment |

### Example: Check Availability

```bash
curl "https://api.salmonspeedworx.com/scheduler/availability?service_id=SERVICE_ID&date=2025-01-15"
```

### Example: Book Appointment

```bash
curl -X POST "https://api.salmonspeedworx.com/scheduler/book" \
  -H "Content-Type: application/json" \
  -d '{
    "service_id": "SERVICE_ID",
    "slot_start": "2025-01-15T09:00:00",
    "slot_end": "2025-01-15T10:00:00",
    "customer": {
      "firstName": "John",
      "lastName": "Doe",
      "email": "john@example.com",
      "phone": "555-1234"
    },
    "vehicle": {
      "year": 2020,
      "make": "Toyota",
      "model": "Camry"
    }
  }'
```

## Widget Embedding

### Full Page Link

Direct link to the scheduling widget:
```
https://scheduler.salmonspeedworx.com
```

### Floating Button (Recommended)

Adds a "Book Appointment" button fixed to the corner that opens the scheduler in a modal:

```html
<script src="https://scheduler.salmonspeedworx.com/static/embed.js"
        data-button-text="Book Appointment"
        data-primary-color="#FF6B00"
        data-button-position="bottom-right">
</script>
```

### Inline Embed

Embeds the scheduler directly into a page section:

```html
<div id="scheduling-widget"></div>
<script src="https://scheduler.salmonspeedworx.com/static/embed.js"
        data-mode="inline"
        data-container-id="scheduling-widget">
</script>
```

### Pre-Select a Service

Skip the service selection step by specifying a service:

**By Service ID:**
```html
<script src="https://scheduler.salmonspeedworx.com/static/embed.js"
        data-service-id="SERVICE_ID_HERE"
        data-button-text="Book Oil Change">
</script>
```

**By Service Name:**
```html
<script src="https://scheduler.salmonspeedworx.com/static/embed.js"
        data-service-name="Oil Change"
        data-button-text="Book Oil Change">
</script>
```

**Direct URL with Service:**
```
https://scheduler.salmonspeedworx.com?service_id=SERVICE_ID_HERE
https://scheduler.salmonspeedworx.com?service_name=Oil%20Change
```

### Embed Options

| Attribute | Values | Description |
|-----------|--------|-------------|
| `data-button-text` | Any text | Button label (default: "Book Appointment") |
| `data-primary-color` | Hex color | Button/accent color (default: #FF6B00) |
| `data-mode` | `button`, `inline`, `modal` | Display mode (default: button) |
| `data-button-position` | `bottom-right`, `bottom-left` | Floating button position |
| `data-container-id` | Element ID | Container for inline mode |
| `data-service-id` | Service ID | Pre-select service by Shopmonkey ID |
| `data-service-name` | Service name | Pre-select service by name (case-insensitive) |

### JavaScript API

For programmatic control:

```javascript
// Initialize with options
SchedulingWidget.init({
  apiUrl: 'https://scheduler.salmonspeedworx.com',
  buttonText: 'Schedule Service',
  primaryColor: '#FF6B00',
  mode: 'button',
  serviceId: 'SERVICE_ID'
});

// Open/close modal programmatically
SchedulingWidget.openModal();
SchedulingWidget.closeModal();
```

## Development

### Run Development Server

```bash
python main.py
```

### Run Tests

```bash
# All tests
pytest

# Exclude integration tests
pytest -m "not integration and not booking"

# Verbose output
pytest -v

# Specific test file
pytest tests/test_availability.py
```

### Docker

```bash
# Build
docker build -t shopmonkey-scheduler .

# Run
docker run -p 8080:8080 --env-file .env shopmonkey-scheduler
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Browser                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │ widget.html │    │  widget.js  │    │     embed.js        │ │
│  │ (full page) │    │  (booking   │    │ (external sites)    │ │
│  │             │    │   logic)    │    │                     │ │
│  └─────────────┘    └─────────────┘    └─────────────────────┘ │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/REST
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend (main.py)                   │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │  /services  │    │/availability│    │       /book         │ │
│  └──────┬──────┘    └──────┬──────┘    └──────────┬──────────┘ │
└─────────┼──────────────────┼─────────────────────┼──────────────┘
          │                  │                     │
          ▼                  ▼                     ▼
┌─────────────────┐  ┌───────────────┐  ┌─────────────────────────┐
│ shopmonkey_     │  │availability.py│  │    Email Service        │
│ client.py       │  │ (slot calc)   │  │    (SMTP)               │
└────────┬────────┘  └───────┬───────┘  └─────────────────────────┘
         │                   │
         ▼                   ▼
┌─────────────────┐  ┌───────────────┐
│  Shopmonkey API │  │sheets_client  │
│  (services,     │  │ (technician   │
│   appointments) │  │  mapping)     │
└─────────────────┘  └───────┬───────┘
                             │
                             ▼
                     ┌───────────────┐
                     │ Google Sheets │
                     │ (tech skills) │
                     └───────────────┘
```

### Key Files

| File | Description |
|------|-------------|
| `main.py` | FastAPI application and REST endpoints |
| `shopmonkey_client.py` | Async client for Shopmonkey API v3 |
| `sheets_client.py` | Google Sheets client for technician mapping |
| `availability.py` | Business logic for slot calculation |
| `config.yaml` | Business hours and slot configuration |
| `static/widget.html` | Scheduling widget HTML |
| `static/widget.js` | Widget JavaScript (booking flow) |
| `static/embed.js` | Embeddable script for external sites |

## Deployment

### CI/CD

Pushes to `master` automatically:
1. Run tests
2. Build Docker image
3. Push to Google Artifact Registry
4. Deploy to Cloud Run

### Infrastructure

See [terraform/README.md](terraform/README.md) for infrastructure management.

### Manual Deployment

```bash
# Build and push image
docker build -t us-docker.pkg.dev/shopmonkey-scheduler/shopmonkey-scheduler/shopmonkey-scheduler:latest .
docker push us-docker.pkg.dev/shopmonkey-scheduler/shopmonkey-scheduler/shopmonkey-scheduler:latest

# Deploy to Cloud Run
gcloud run deploy shopmonkey-scheduler \
  --image us-docker.pkg.dev/shopmonkey-scheduler/shopmonkey-scheduler/shopmonkey-scheduler:latest \
  --region us-central1
```

## License

Private - All rights reserved.
