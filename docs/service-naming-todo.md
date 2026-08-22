# Service Naming Consistency - TODO

**Status:** Awaiting approval before implementation
**Date:** 2026-01-24

---

## Summary

16 service renames identified for consistency. Can be automated via Shopmonkey API.

---

## Renames

### 1. Bedliner (1 change)
| Current | New |
|---------|-----|
| `Bedliner - Short Bed` | `Bedliner - Short Bed Spray-In` |

### 2. Consultations (2 changes + category change)
| Current | New | Category |
|---------|-----|----------|
| `Custom Exhaust Consultation` | `Consultation - Custom Exhaust` | Consultation |
| `Sales Consultation` | `Consultation - Sales` | Consultation |

### 3. Detail - Add "Level 1" to Exterior (4 changes)
| Current | New |
|---------|-----|
| `Detail - Exterior - Coupe/Two Door Truck` | `Detail - Exterior Level 1 - Coupe/Two Door Truck` |
| `Detail - Exterior - SUV` | `Detail - Exterior Level 1 - SUV` |
| `Detail - Exterior - Sedan/Four Door Truck` | `Detail - Exterior Level 1 - Sedan/Four Door Truck` |
| `Detail - Exterior - XL SUV/Van` | `Detail - Exterior Level 1 - XL SUV/Van` |

### 4. Detail - Remove "Only" from Interior Level 2 (4 changes)
| Current | New |
|---------|-----|
| `Detail - Interior Only Level 2 - Coupe/Two Door Truck` | `Detail - Interior Level 2 - Coupe/Two Door Truck` |
| `Detail - Interior Only Level 2 - SUV` | `Detail - Interior Level 2 - SUV` |
| `Detail - Interior Only Level 2 - Sedan/Four Door Truck` | `Detail - Interior Level 2 - Sedan/Four Door Truck` |
| `Detail - Interior Only Level 2 - XL SUV/Van` | `Detail - Interior Level 2 - XL SUV/Van` |

### 5. Detail - Express Vehicle Naming (3 changes)
| Current | New |
|---------|-----|
| `Detail - Express Exterior - Standard Vehicle` | `Detail - Express Exterior - 2-Row Vehicle` |
| `Detail - Express Interior & Exterior - Standard Vehicle` | `Detail - Express Interior & Exterior - 2-Row Vehicle` |
| `Detail - Express Interior & Exterior - Vehicle w/Third Row Seating` | `Detail - Express Interior & Exterior - 3-Row Vehicle` |

### 6. Window Tint - "Two Door" Clarification (2 changes)
| Current | New |
|---------|-----|
| `Window Tint - Two Door Tint - Carbon` | `Window Tint - Front Doors - Carbon` |
| `Window Tint - Two Door Tint - Ceramic` | `Window Tint - Front Doors - Ceramic` |

### 7. Window Tint - Window Count + Vehicle Type (4 changes)
| Current | New |
|---------|-----|
| `Window Tint - Full Sedan/Truck - Carbon` | `Window Tint - Full Sedan/Truck/SUV (5 Window) - Carbon` |
| `Window Tint - Full Sedan/Truck - Ceramic` | `Window Tint - Full Sedan/Truck/SUV (5 Window) - Ceramic` |
| `Window Tint - Full XL SUV/Van - Carbon` | `Window Tint - Full XL SUV/Van (7 Window) - Carbon` |
| `Window Tint - Full XL SUV/Van - Ceramic` | `Window Tint - Full XL SUV/Van (7 Window) - Ceramic` |

---

## Implementation

### API Endpoint
```
PUT https://api.shopmonkey.cloud/v3/canned_service/:id
Authorization: Bearer ${SHOPMONKEY_API_TOKEN}
Content-Type: application/json

{ "name": "New Service Name" }
```

### To Execute
Run a script that:
1. Fetches all canned services
2. Matches current names to new names
3. Calls PUT for each rename
4. Reports results

### Widget Impact
No code changes needed - the widget's parser handles these patterns.

---

## Notes
- Window tint pricing based on window count: 5 windows (Sedan/Truck/SUV) vs 7 windows (XL SUV/Van)
- Consultation services may need category field updated in addition to name
