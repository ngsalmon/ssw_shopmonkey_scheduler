"""Find the actual bookable Window Tint - Full Sedan/Truck - Ceramic
canned service Anne booked (website showed $380.47).

Also probe parts attach format on /v3/order/<id>/service.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def loc(p=None):
    out = dict(p or {})
    if LOCATION_ID:
        out["locationId"] = LOCATION_ID
    return out


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # Use bookable=True filter (exact-match top-level scalar; works).
        params = loc({"where": json.dumps({"bookable": True}), "limit": "100"})
        bookable = c.get("/v3/canned_service", params=params).json()
        print(f"Bookable canned services: {bookable.get('meta')}")
        services = bookable.get("data", [])
        print(f"  Returned: {len(services)}")

        # Find Window Tint - Full Sedan/Truck - Ceramic (no Dealer/Fleet prefix)
        target_name = "Window Tint - Full Sedan/Truck - Ceramic"
        found = [s for s in services if s.get("name") == target_name]
        print(f"\nExact matches for {target_name!r}: {len(found)}")

        # Loose matches
        loose = [
            s
            for s in services
            if "ceramic" in (s.get("name") or "").lower()
            and "window tint" in (s.get("name") or "").lower()
            and "dealer" not in (s.get("name") or "").lower()
            and "fleet" not in (s.get("name") or "").lower()
        ]
        print(f"Loose matches (Window Tint ... Ceramic, not Dealer/Fleet): {len(loose)}")
        for s in loose:
            print(f"  - id={s['id'][:8]} name={s.get('name')!r}")

        if not found and loose:
            # Use the full-sedan variant
            target = next(
                (s for s in loose if "full sedan" in (s.get("name") or "").lower()),
                loose[0],
            )
        else:
            target = found[0] if found else None

        if not target:
            print("Could not find target canned service.")
            return 1

        # Fetch detail
        detail = c.get(f"/v3/canned_service/{target['id']}").json().get("data", {})
        print(f"\nTarget detail: name={detail.get('name')!r}")
        print(f"  totalCents: {detail.get('totalCents')}")
        print(f"  calculatedLaborCents: {detail.get('calculatedLaborCents')}")
        print(f"  calculatedPartsCents: {detail.get('calculatedPartsCents')}")
        print(f"  calculatedTaxCents: {detail.get('calculatedTaxCents')}")
        print(f"  calculatedDiscountCents: {detail.get('calculatedDiscountCents')}")
        print(f"  calculatedTotalCents: {detail.get('calculatedTotalCents')}")
        print(f"  taxPercent: {detail.get('taxPercent')}")
        print(f"  shopSuppliesPercent: {detail.get('shopSuppliesPercent')}")

        # Labors
        print("\n  Labors:")
        for labor in detail.get("labors") or []:
            print(
                f"    {labor.get('name')!r} hours={labor.get('hours')} "
                f"rateCents={labor.get('rateCents')} "
                f"discountCents={labor.get('discountCents')} "
                f"discountPercent={labor.get('discountPercent')} "
                f"taxable={labor.get('taxable')} "
                f"laborRate={(labor.get('laborRate') or {}).get('name')}"
            )

        # Parts
        print("\n  Parts:")
        for part in detail.get("parts") or []:
            print(
                f"    {part.get('name')!r} qty={part.get('quantity')} "
                f"retailCostCents={part.get('retailCostCents')} "
                f"wholesaleCostCents={part.get('wholesaleCostCents')} "
                f"discountCents={part.get('discountCents')} "
                f"taxable={part.get('taxable')} "
                f"partNumber={part.get('partNumber')!r}"
            )

        # Fees
        print("\n  Fees:")
        for fee in detail.get("fees") or []:
            print(f"    {json.dumps(fee, default=str)}")

        # Subcontracts
        print("\n  Subcontracts:")
        for sub in detail.get("subcontracts") or []:
            print(f"    {json.dumps(sub, default=str)}")

        # Compute expected ticket total
        labor_cents = sum(
            int((lb.get("hours") or 0) * (lb.get("rateCents") or 0))
            for lb in (detail.get("labors") or [])
        )
        parts_cents = sum(
            int((p.get("quantity") or 0) * (p.get("retailCostCents") or 0))
            for p in (detail.get("parts") or [])
        )
        print(f"\n  Computed labor + parts (no tax): {labor_cents + parts_cents} cents")
        print(f"  i.e. ${(labor_cents + parts_cents) / 100:.2f}")
        print(f"  Plus tax @ {detail.get('taxPercent')}%")
        tax_cents = round((labor_cents + parts_cents) * (detail.get("taxPercent") or 0) / 100)
        # Note: shop-level tax rules may differ. Compute approx.
        print(f"  Approx total with tax: ${(labor_cents + parts_cents + tax_cents) / 100:.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
