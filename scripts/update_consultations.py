#!/usr/bin/env python3
"""
One-time update script to configure consultation services.

This script:
1. Renames existing "Consultation" to "Sales Consultation" in Shopmonkey
2. Creates new "Custom Exhaust Consultation" service in Shopmonkey
3. Adds corresponding columns to Google Sheets Tech/Dept tab
4. Sets tech assignments (Nikki/Chad for Sales, Zack for Custom Exhaust)

Run once and delete after verification.
"""

import json
import os
import sys

import httpx
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


def get_shopmonkey_client():
    """Create httpx client for Shopmonkey API."""
    api_token = os.getenv("SHOPMONKEY_API_TOKEN")
    base_url = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud")

    if not api_token:
        raise ValueError("SHOPMONKEY_API_TOKEN is required")

    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )


def get_sheets_service():
    """Create Google Sheets service."""
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if credentials_path:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
    else:
        from google.auth import default
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])

    return build("sheets", "v4", credentials=credentials)


def find_canned_service_by_name(client, name, partial_match=False):
    """Find a canned service by name."""
    if partial_match:
        # Search all services and find matching ones
        response = client.get("/v3/canned_service")
        response.raise_for_status()
        data = response.json().get("data", [])
        for svc in data:
            if name.lower() in svc.get("name", "").lower():
                return svc
        return None
    else:
        where_clause = json.dumps({"name": name})
        response = client.get("/v3/canned_service", params={"where": where_clause})
        response.raise_for_status()
        data = response.json().get("data", [])
        return data[0] if data else None


def get_all_labels(client):
    """Get all labels from Shopmonkey."""
    response = client.get("/v3/label")
    response.raise_for_status()
    return response.json().get("data", [])


def find_or_create_label(client, label_name, color="blue"):
    """Find existing label or create new one."""
    labels = get_all_labels(client)

    for label in labels:
        if label.get("name", "").lower() == label_name.lower():
            print(f"  Found existing label: {label_name} (ID: {label['id']})")
            return label

    # Create new label (color and saved are required for reusable labels)
    response = client.post("/v3/label", json={
        "name": label_name,
        "color": color,
        "saved": True,
    })
    response.raise_for_status()
    new_label = response.json().get("data", response.json())
    print(f"  Created new label: {label_name} (ID: {new_label.get('id')})")
    return new_label


def update_canned_service(client, service_id, updates):
    """Update a canned service."""
    response = client.put(f"/v3/canned_service/{service_id}", json=updates)
    response.raise_for_status()
    return response.json().get("data", response.json())


def create_canned_service(client, service_data):
    """Create a new canned service."""
    response = client.post("/v3/canned_service", json=service_data)
    response.raise_for_status()
    return response.json().get("data", response.json())


def add_label_to_service(client, service_id, label_id):
    """Add a label to a canned service by updating the labels array."""
    # First get the current service to see existing labels
    response = client.get(f"/v3/canned_service/{service_id}")
    response.raise_for_status()
    service = response.json().get("data", {})

    current_labels = service.get("labels", [])

    # Check if label already attached
    if any(l.get("id") == label_id for l in current_labels):
        return {"already_attached": True}

    # Add the new label
    current_labels.append({"id": label_id})

    # Update the service
    response = client.put(f"/v3/canned_service/{service_id}", json={"labels": current_labels})
    response.raise_for_status()
    return response.json()


def update_shopmonkey(client):
    """Update Shopmonkey canned services."""
    print("\n=== SHOPMONKEY UPDATES ===\n")

    # Default location ID from the existing services
    location_id = os.getenv("SHOPMONKEY_LOCATION_ID") or "55826aba-3443-416e-b37b-edadb114696e"

    # Step 1: Find or create "Sales Consultation" service
    print("1. Looking for 'Sales Consultation' service...")

    # Check if Sales Consultation already exists
    sales_consultation = find_canned_service_by_name(client, "Sales Consultation")

    if sales_consultation:
        print(f"  Found existing: {sales_consultation['name']} (ID: {sales_consultation['id']})")

        # Ensure it's bookable
        if not sales_consultation.get("bookable"):
            updates = {"bookable": True}
            updated = update_canned_service(client, sales_consultation["id"], updates)
            print(f"  Updated to bookable: {updated.get('bookable', True)}")
        else:
            print(f"  Already bookable: True")

        sales_consultation_id = sales_consultation["id"]
    else:
        # Look for "Customer Consultation" to rename
        consultation = find_canned_service_by_name(client, "Customer Consultation")
        if not consultation:
            consultation = find_canned_service_by_name(client, "Consultation", partial_match=True)

        if consultation:
            print(f"  Found: {consultation['name']} (ID: {consultation['id']})")
            print("  Renaming to 'Sales Consultation'...")

            updates = {
                "name": "Sales Consultation",
                "bookable": True,
            }
            updated = update_canned_service(client, consultation["id"], updates)
            print(f"  Updated name to: {updated.get('name', 'Sales Consultation')}")
            print(f"  Bookable: {updated.get('bookable', True)}")
            sales_consultation_id = consultation["id"]
        else:
            print("  Not found! Creating new 'Sales Consultation' service...")
            service_data = {
                "name": "Sales Consultation",
                "bookable": True,
                "locationId": location_id,
            }
            new_service = create_canned_service(client, service_data)
            print(f"  Created: {new_service.get('name')} (ID: {new_service.get('id')})")
            sales_consultation_id = new_service["id"]

    # Add "Sales Consultation" label
    print("\n  Adding 'Sales Consultation' label...")
    label = find_or_create_label(client, "Sales Consultation")
    result = add_label_to_service(client, sales_consultation_id, label["id"])
    if result.get("already_attached"):
        print(f"  Label already attached to service")
    else:
        print(f"  Label added to service")

    # Step 2: Create "Custom Exhaust Consultation" service
    print("\n2. Creating 'Custom Exhaust Consultation' service...")

    # Check if it already exists
    existing = find_canned_service_by_name(client, "Custom Exhaust Consultation")

    if existing:
        print(f"  Already exists: {existing['name']} (ID: {existing['id']})")

        # Update to ensure it's bookable
        updates = {
            "bookable": True,
        }
        updated = update_canned_service(client, existing["id"], updates)
        print(f"  Bookable: {updated.get('bookable', True)}")

        custom_exhaust_id = existing["id"]
    else:
        service_data = {
            "name": "Custom Exhaust Consultation",
            "bookable": True,
            "locationId": location_id,
        }
        new_service = create_canned_service(client, service_data)
        print(f"  Created: {new_service.get('name')} (ID: {new_service.get('id')})")
        custom_exhaust_id = new_service["id"]

    # Add "Custom Exhaust" label
    print("\n  Adding 'Custom Exhaust' label...")
    label = find_or_create_label(client, "Custom Exhaust")
    result = add_label_to_service(client, custom_exhaust_id, label["id"])
    if result.get("already_attached"):
        print(f"  Label already attached to service")
    else:
        print(f"  Label added to service")

    print("\n✓ Shopmonkey updates complete!")
    return sales_consultation_id, custom_exhaust_id


def update_google_sheets(service, spreadsheet_id):
    """Update Google Sheets Tech/Dept tab with new columns."""
    print("\n=== GOOGLE SHEETS UPDATES ===\n")

    tab_name = "Tech/Dept"

    # Step 1: Read current header row to find column positions
    print("1. Reading current Tech/Dept header row...")
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!1:1"
    ).execute()

    header_row = result.get("values", [[]])[0]
    print(f"  Current columns: {header_row}")

    # Find Status column index (columns to add should be before Status)
    status_index = None
    for i, col in enumerate(header_row):
        if col.lower() == "status":
            status_index = i
            break

    if status_index is None:
        # Status not found, add columns at the end
        status_index = len(header_row)
        print("  Status column not found, adding columns at end")
    else:
        print(f"  Status column at index {status_index}")

    # Check if columns already exist
    sales_col_index = None
    exhaust_col_index = None

    for i, col in enumerate(header_row):
        if col.lower() == "sales consultation":
            sales_col_index = i
        elif col.lower() == "custom exhaust":
            exhaust_col_index = i

    # Step 2: Read all data to get tech names and IDs
    print("\n2. Reading tech data...")
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A:Z"
    ).execute()

    all_data = result.get("values", [])
    if len(all_data) < 2:
        print("  ERROR: No tech data found!")
        return

    # Find tech rows (Name is column A, ID is column B)
    tech_rows = {}  # row_index -> {name, id}
    for row_idx, row in enumerate(all_data[1:], start=2):  # Start from row 2 (1-indexed)
        if len(row) >= 2:
            name = row[0].strip().lower()
            tech_id = row[1].strip()
            tech_rows[row_idx] = {"name": name, "id": tech_id}
            print(f"    Row {row_idx}: {row[0]} (ID: {tech_id})")

    # Step 3: Insert columns if needed
    if sales_col_index is None or exhaust_col_index is None:
        print("\n3. Adding new columns...")

        # Get spreadsheet to find sheet ID
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_id = None
        for sheet in spreadsheet.get("sheets", []):
            if sheet["properties"]["title"] == tab_name:
                sheet_id = sheet["properties"]["sheetId"]
                break

        if sheet_id is None:
            print(f"  ERROR: Sheet '{tab_name}' not found!")
            return

        requests = []

        # We'll insert columns before Status if it exists
        # Insert two columns at status_index position
        if sales_col_index is None:
            requests.append({
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": status_index,
                        "endIndex": status_index + 1,
                    },
                    "inheritFromBefore": True,
                }
            })
            print(f"  Inserting 'Sales Consultation' column at index {status_index}")
            sales_col_index = status_index
            status_index += 1  # Status moved right

        if exhaust_col_index is None:
            requests.append({
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": status_index,
                        "endIndex": status_index + 1,
                    },
                    "inheritFromBefore": True,
                }
            })
            print(f"  Inserting 'Custom Exhaust' column at index {status_index}")
            exhaust_col_index = status_index

        if requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests}
            ).execute()
            print("  Columns inserted!")
    else:
        print("\n3. Columns already exist!")
        print(f"  'Sales Consultation' at column index {sales_col_index}")
        print(f"  'Custom Exhaust' at column index {exhaust_col_index}")

    # Step 4: Set column headers and tech values
    print("\n4. Setting column headers and tech assignments...")

    # Convert column index to letter (0=A, 1=B, etc.)
    def col_letter(index):
        result = ""
        while index >= 0:
            result = chr(index % 26 + ord('A')) + result
            index = index // 26 - 1
        return result

    sales_col = col_letter(sales_col_index)
    exhaust_col = col_letter(exhaust_col_index)

    print(f"  Sales Consultation column: {sales_col}")
    print(f"  Custom Exhaust column: {exhaust_col}")

    # Prepare batch update values
    updates = []

    # Headers
    updates.append({
        "range": f"'{tab_name}'!{sales_col}1",
        "values": [["Sales Consultation"]]
    })
    updates.append({
        "range": f"'{tab_name}'!{exhaust_col}1",
        "values": [["Custom Exhaust"]]
    })

    # Tech assignments
    # Nikki and Chad -> Sales Consultation = TRUE
    # Zack -> Custom Exhaust = TRUE
    # Everyone else -> FALSE for both

    for row_idx, tech_info in tech_rows.items():
        name = tech_info["name"]

        # Sales Consultation: Nikki and Chad
        if "nikki" in name or "chad" in name:
            sales_value = "TRUE"
            print(f"  {tech_info['name']}: Sales Consultation = TRUE")
        else:
            sales_value = "FALSE"

        # Custom Exhaust: Zack only
        if "zack" in name:
            exhaust_value = "TRUE"
            print(f"  {tech_info['name']}: Custom Exhaust = TRUE")
        else:
            exhaust_value = "FALSE"

        updates.append({
            "range": f"'{tab_name}'!{sales_col}{row_idx}",
            "values": [[sales_value]]
        })
        updates.append({
            "range": f"'{tab_name}'!{exhaust_col}{row_idx}",
            "values": [[exhaust_value]]
        })

    # Execute batch update
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": updates
        }
    ).execute()

    print("\n✓ Google Sheets updates complete!")


def main():
    """Main entry point."""
    # Load environment variables from .env file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    env_path = os.path.join(project_dir, ".env")

    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded environment from: {env_path}")
    else:
        print(f"Warning: .env file not found at {env_path}")

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not spreadsheet_id:
        print("ERROR: GOOGLE_SHEETS_ID is required")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("CONSULTATION SERVICES UPDATE SCRIPT")
    print("=" * 50)
    print("\nThis script will:")
    print("1. Rename 'Consultation' to 'Sales Consultation' in Shopmonkey")
    print("2. Create 'Custom Exhaust Consultation' in Shopmonkey")
    print("3. Add Tech/Dept columns in Google Sheets")
    print("4. Assign Nikki & Chad to Sales Consultation")
    print("5. Assign Zack to Custom Exhaust")
    print("\n" + "=" * 50)

    # Shopmonkey updates
    sm_client = get_shopmonkey_client()
    try:
        update_shopmonkey(sm_client)
    finally:
        sm_client.close()

    # Google Sheets updates
    sheets_service = get_sheets_service()
    update_google_sheets(sheets_service, spreadsheet_id)

    print("\n" + "=" * 50)
    print("ALL UPDATES COMPLETE!")
    print("=" * 50)
    print("\nNext steps:")
    print("1. Verify changes in Shopmonkey UI")
    print("2. Check Google Sheets Tech/Dept tab")
    print("3. Test booking at /schedule")
    print("4. Delete this script when verified")


if __name__ == "__main__":
    main()
