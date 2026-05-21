"""Probe the Sheets tech/department mapping to understand who is qualified
for what and at what priority.

We need this to design integration tests that:
- find a service-pair with disjoint qualified techs (cross-dept isolation test)
- find a service-pair with overlapping techs but a tech unique to one (shared-tech test)
- find a service with a small qualified-tech count (full-slot 409 test)

Read-only.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from sheets_client import SheetsClient
from shopmonkey_client import ShopmonkeyClient

load_dotenv()


async def main() -> int:
    sm = ShopmonkeyClient()
    sheets = SheetsClient()

    # 1. All bookable services and their inferred department
    services = await sm.get_bookable_canned_services()
    print(f"Bookable services: {len(services)}")
    by_dept: dict[str, list[dict]] = defaultdict(list)
    for s in services:
        labels = s.get("labels") or []
        dept = labels[0].get("name") if labels else None
        by_dept[dept or "<no label>"].append(s)
    print("\nBy department label:")
    for dept, lst in sorted(by_dept.items(), key=lambda kv: kv[0] or ""):
        print(f"  {dept}: {len(lst)} services")

    # 2. Tech-department matrix from the sheet
    print("\nTech / department matrix (sheet snapshot):")
    tech_depts = await sheets.get_tech_departments()
    # tech_depts: {tech_id: {tech_name, departments: {dept: priority}}}
    print(f"  Tech count: {len(tech_depts)}")
    all_depts: set[str] = set()
    for entry in tech_depts.values():
        all_depts.update(entry.get("departments", {}).keys())
    print(f"  Distinct depts in sheet: {sorted(all_depts)}")

    # Cross-reference: only show active techs in Shopmonkey
    active_ids = await sm.get_active_user_ids()
    print(f"  Active Shopmonkey users: {len(active_ids)}")

    print("\nPer-department qualified-tech sets (active only, with priority):")
    qualified_by_dept: dict[str, list[tuple[str, str, int]]] = {}
    for dept in sorted(all_depts):
        techs = await sheets.get_techs_for_department(dept, active_tech_ids=active_ids)
        qualified_by_dept[dept] = [(t["tech_id"], t["tech_name"], t["priority"]) for t in techs]
        print(f"\n  {dept}: {len(techs)} qualified")
        for t in techs:
            print(f"    pri={t['priority']:>2}  {t['tech_id'][:8]}  {t['tech_name']}")

    # 3. Find pair candidates for integration tests
    print("\n" + "=" * 70)
    print("Test-pair candidates")
    print("=" * 70)

    dept_techsets = {d: {tid for tid, _, _ in lst} for d, lst in qualified_by_dept.items() if lst}
    dept_names = sorted(dept_techsets.keys())

    # Disjoint pairs (Test 2)
    print("\nDisjoint dept pairs (∩ == ∅):")
    found_disjoint: list[tuple[str, str]] = []
    for i, d1 in enumerate(dept_names):
        for d2 in dept_names[i + 1 :]:
            if not (dept_techsets[d1] & dept_techsets[d2]):
                found_disjoint.append((d1, d2))
                print(f"  {d1!r:30}  ⨯  {d2!r}")
    if not found_disjoint:
        print("  none found")

    # Overlapping pairs (Test 4) — with at least one tech unique to one side
    print("\nOverlapping dept pairs with a tech unique to LEFT side:")
    for i, d1 in enumerate(dept_names):
        for d2 in dept_names[i + 1 :]:
            shared = dept_techsets[d1] & dept_techsets[d2]
            unique_to_left = dept_techsets[d1] - dept_techsets[d2]
            if shared and unique_to_left:
                print(
                    f"  {d1!r:30}  ∩  {d2!r}: "
                    f"shared={len(shared)}, unique-to-left={len(unique_to_left)}"
                )

    # Small-tech-count services (Test 3)
    print("\nDepartments with 1-3 qualified active techs (good for full-slot 409 test):")
    for d, techset in dept_techsets.items():
        if 1 <= len(techset) <= 3:
            print(f"  {d!r:30}  techs={len(techset)}")

    # Match service names to depts so we have ready-to-pick service IDs
    print("\nService-to-dept mapping samples (first 3 per dept):")
    for d, services_in_dept in by_dept.items():
        if d in dept_techsets:
            print(f"\n  {d}:  ({len(dept_techsets[d])} qualified techs)")
            for s in services_in_dept[:3]:
                labors = s.get("labors") or []
                hours = sum(float(la.get("hours", 0) or 0) for la in labors)
                print(f"    {s.get('id', '')[:8]}  {s.get('name', '')[:60]:60}  ~{hours}h")

    await sm.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
