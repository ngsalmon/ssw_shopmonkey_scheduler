#!/usr/bin/env python3
"""
Script to rename Shopmonkey canned services for naming consistency.

Usage:
    python scripts/rename_services.py          # Dry run (preview changes)
    python scripts/rename_services.py --apply  # Apply changes
"""

import argparse
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Service name mappings: current_name -> new_name
RENAMES = {
    # 1. Bedliner - Add "Spray-In" suffix
    "Bedliner - Short Bed": "Bedliner - Short Bed Spray-In",

    # 2. Consultations - Unify category and add separator
    "Custom Exhaust Consultation": "Consultation - Custom Exhaust",
    "Sales Consultation": "Consultation - Sales",

    # 3. Detail - Add "Level 1" to basic Exterior services
    "Detail - Exterior - Coupe/Two Door Truck": "Detail - Exterior Level 1 - Coupe/Two Door Truck",
    "Detail - Exterior - SUV": "Detail - Exterior Level 1 - SUV",
    "Detail - Exterior - Sedan/Four Door Truck": "Detail - Exterior Level 1 - Sedan/Four Door Truck",
    "Detail - Exterior - XL SUV/Van": "Detail - Exterior Level 1 - XL SUV/Van",

    # 4. Detail - Remove "Only" from Interior Level 2
    "Detail - Interior Only Level 2 - Coupe/Two Door Truck": "Detail - Interior Level 2 - Coupe/Two Door Truck",
    "Detail - Interior Only Level 2 - SUV": "Detail - Interior Level 2 - SUV",
    "Detail - Interior Only Level 2 - Sedan/Four Door Truck": "Detail - Interior Level 2 - Sedan/Four Door Truck",
    "Detail - Interior Only Level 2 - XL SUV/Van": "Detail - Interior Level 2 - XL SUV/Van",

    # 5. Detail - Standardize Express vehicle naming
    "Detail - Express Exterior - Standard Vehicle": "Detail - Express Exterior - 2-Row Vehicle",
    "Detail - Express Interior & Exterior - Standard Vehicle": "Detail - Express Interior & Exterior - 2-Row Vehicle",
    "Detail - Express Interior & Exterior - Vehicle w/Third Row Seating": "Detail - Express Interior & Exterior - 3-Row Vehicle",

    # 6. Window Tint - Clarify "Two Door Tint"
    "Window Tint - Two Door Tint - Carbon": "Window Tint - Front Doors - Carbon",
    "Window Tint - Two Door Tint - Ceramic": "Window Tint - Front Doors - Ceramic",

    # 7. Window Tint - Clarify window-count-based pricing
    "Window Tint - Full Sedan/Truck - Carbon": "Window Tint - Full Sedan/Truck/SUV (5 Window) - Carbon",
    "Window Tint - Full Sedan/Truck - Ceramic": "Window Tint - Full Sedan/Truck/SUV (5 Window) - Ceramic",
    "Window Tint - Full XL SUV/Van - Carbon": "Window Tint - Full XL SUV/Van (7 Window) - Carbon",
    "Window Tint - Full XL SUV/Van - Ceramic": "Window Tint - Full XL SUV/Van (7 Window) - Ceramic",
}


async def fetch_all_canned_services(client: httpx.AsyncClient, location_id: str | None) -> list[dict]:
    """Fetch all canned services from Shopmonkey."""
    params = {}
    if location_id:
        params["locationId"] = location_id

    response = await client.get("/v3/canned_service", params=params if params else None)
    response.raise_for_status()
    return response.json().get("data", [])


async def update_service_name(client: httpx.AsyncClient, service_id: str, new_name: str) -> bool:
    """Update a canned service's name."""
    response = await client.put(
        f"/v3/canned_service/{service_id}",
        json={"name": new_name}
    )
    return response.status_code == 200


async def main(apply: bool = False) -> int:
    """Main function to rename services."""
    api_token = os.getenv("SHOPMONKEY_API_TOKEN")
    if not api_token:
        print("Error: SHOPMONKEY_API_TOKEN environment variable is required")
        return 1

    base_url = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud")
    location_id = os.getenv("SHOPMONKEY_LOCATION_ID")

    print(f"Shopmonkey Service Renamer")
    print(f"{'='*50}")
    print(f"Mode: {'APPLY CHANGES' if apply else 'DRY RUN (preview only)'}")
    print(f"API Base URL: {base_url}")
    print(f"Location ID: {location_id or '(not set)'}")
    print()

    async with httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    ) as client:
        # Fetch all canned services
        print("Fetching canned services...")
        services = await fetch_all_canned_services(client, location_id)
        print(f"Found {len(services)} canned services\n")

        # Find services to rename
        to_rename = []
        for service in services:
            current_name = service.get("name", "")
            if current_name in RENAMES:
                to_rename.append({
                    "id": service["id"],
                    "current_name": current_name,
                    "new_name": RENAMES[current_name],
                })

        if not to_rename:
            print("No services found matching the rename list.")
            print("\nPossible reasons:")
            print("  - Services have already been renamed")
            print("  - Service names in Shopmonkey don't match expected names exactly")
            print("\nExpected services to find:")
            for name in sorted(RENAMES.keys()):
                print(f"  - {name}")
            return 0

        # Display planned renames
        print(f"Services to rename: {len(to_rename)}")
        print("-" * 80)
        for item in to_rename:
            print(f"  {item['current_name']}")
            print(f"    -> {item['new_name']}")
            print()

        # Check for any expected services not found
        found_names = {item["current_name"] for item in to_rename}
        missing = set(RENAMES.keys()) - found_names
        if missing:
            print(f"\nNote: {len(missing)} expected services not found:")
            for name in sorted(missing):
                print(f"  - {name}")
            print()

        if not apply:
            print("-" * 80)
            print("DRY RUN - No changes made.")
            print("Run with --apply to apply changes.")
            return 0

        # Apply renames
        print("-" * 80)
        print("Applying changes...")
        print()

        success_count = 0
        failure_count = 0

        for item in to_rename:
            try:
                success = await update_service_name(client, item["id"], item["new_name"])
                if success:
                    print(f"✓ Renamed: {item['current_name']}")
                    success_count += 1
                else:
                    print(f"✗ Failed: {item['current_name']} (unexpected response)")
                    failure_count += 1
            except httpx.HTTPStatusError as e:
                print(f"✗ Failed: {item['current_name']} ({e.response.status_code}: {e.response.text[:100]})")
                failure_count += 1
            except Exception as e:
                print(f"✗ Failed: {item['current_name']} ({type(e).__name__}: {e})")
                failure_count += 1

        print()
        print("-" * 80)
        print(f"Results: {success_count} succeeded, {failure_count} failed")

        if failure_count > 0:
            return 1

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rename Shopmonkey canned services for naming consistency"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry run)",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(main(apply=args.apply))
    sys.exit(exit_code)
