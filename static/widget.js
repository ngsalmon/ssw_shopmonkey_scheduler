/**
 * Scheduling Widget JavaScript
 * Handles multi-step booking flow, API calls, and form validation
 */

(function() {
    'use strict';

    // Configuration
    const API_BASE_URL = window.WIDGET_API_URL || '';
    const TOTAL_STEPS = 6;

    // State
    const state = {
        currentStep: 1,
        selectedService: null,
        selectedDate: null,
        selectedSlot: null,
        customer: {
            firstName: '',
            lastName: '',
            email: '',
            phone: ''
        },
        vehicle: {
            year: '',
            make: '',
            model: '',
            vin: ''
        },
        services: [],
        parsedServices: [],
        availableSlots: [],
        serviceDurationMinutes: 60,
        businessHoursClose: '18:00',
        calendarDate: new Date(),
        searchQuery: '',
        collapsedCategories: new Set(),
        selectedCategory: null,
        filters: {
            detail: { vehicleSize: null, serviceType: null },
            windowTint: { tintType: null, tintArea: null }
        }
    };

    // Category definitions - ordered by margin (Detail last)
    const CATEGORIES = {
        bedliner: { label: 'Bedliner', priority: 1 },
        consultation: { label: 'Consultation', priority: 2 },
        windowTint: { label: 'Window Tint', priority: 3 },
        alignment: { label: 'Alignment', priority: 4 },
        detail: { label: 'Detail', priority: 5 },
        other: { label: 'Other', priority: 99 }
    };

    // Service name parser - extracts metadata from service names
    function parseServiceName(serviceName, category) {
        const result = {
            originalName: serviceName,
            displayName: serviceName,
            vehicleSize: null,
            vehicleSizeRaw: null,
            serviceType: null,
            tintType: null,
            tintArea: null,
            level: null,
            isCombo: false,
            categoryKey: 'other'
        };

        const lowerName = serviceName.toLowerCase();
        const lowerCategory = (category || '').toLowerCase();

        // Determine category key
        if (lowerCategory.includes('bedliner') || lowerName.includes('bedliner') || lowerName.includes('bed liner')) {
            result.categoryKey = 'bedliner';
        } else if (lowerCategory.includes('consultation') || lowerName.includes('consultation')) {
            result.categoryKey = 'consultation';
        } else if (lowerCategory.includes('window tint') || lowerCategory.includes('tint') || lowerName.includes('window tint')) {
            result.categoryKey = 'windowTint';
        } else if (lowerCategory.includes('alignment') || lowerName.includes('alignment')) {
            result.categoryKey = 'alignment';
        } else if (lowerCategory.includes('detail') || lowerName.startsWith('detail')) {
            result.categoryKey = 'detail';
        }

        // Parse Window Tint services: "Window Tint - [Area] - [Type]"
        if (result.categoryKey === 'windowTint' || lowerName.includes('window tint')) {
            const tintMatch = serviceName.match(/Window Tint\s*-\s*(.+?)\s*-\s*(Carbon|Ceramic)/i);
            if (tintMatch) {
                result.tintArea = tintMatch[1].trim();
                result.tintType = tintMatch[2].toLowerCase();
                result.displayName = tintMatch[1].trim();
            } else {
                // Try simpler pattern: "Window Tint - [Area]"
                const simpleMatch = serviceName.match(/Window Tint\s*-\s*(.+)/i);
                if (simpleMatch) {
                    result.tintArea = simpleMatch[1].trim();
                    result.displayName = simpleMatch[1].trim();
                    // Check if area contains tint type
                    if (result.tintArea.toLowerCase().includes('ceramic')) {
                        result.tintType = 'ceramic';
                    } else if (result.tintArea.toLowerCase().includes('carbon')) {
                        result.tintType = 'carbon';
                    }
                }
            }
            result.categoryKey = 'windowTint';
        }

        // Parse Detail services: "Detail - [Type] [Level] - [VehicleSize]"
        if (result.categoryKey === 'detail' || lowerName.startsWith('detail')) {
            result.categoryKey = 'detail';

            // Check for combo services
            if (lowerName.includes('combo') || (lowerName.includes('interior') && lowerName.includes('exterior') && lowerName.includes('&'))) {
                result.isCombo = true;
                result.serviceType = 'combo';
            } else if (lowerName.includes('interior') && !lowerName.includes('exterior')) {
                result.serviceType = 'interior';
            } else if (lowerName.includes('exterior') && !lowerName.includes('interior')) {
                result.serviceType = 'exterior';
            } else if (lowerName.includes('express')) {
                result.serviceType = 'express';
            }

            // Extract level
            const levelMatch = lowerName.match(/level\s*(\d+)/i);
            if (levelMatch) {
                result.level = parseInt(levelMatch[1], 10);
            }

            // Extract vehicle size - look for it at the end after the last dash
            const parts = serviceName.split('-').map(p => p.trim());
            if (parts.length >= 2) {
                const lastPart = parts[parts.length - 1];
                result.vehicleSizeRaw = lastPart;
                result.vehicleSize = normalizeVehicleSize(lastPart);

                // Build display name from middle parts
                if (parts.length >= 3) {
                    result.displayName = parts.slice(1, -1).join(' ').trim();
                } else {
                    result.displayName = parts[1] || serviceName;
                }
            }
        }

        return result;
    }

    function normalizeVehicleSize(raw) {
        if (!raw) return null;
        const lower = raw.toLowerCase().trim();

        if (lower.includes('coupe') || lower.includes('two door') || lower === '2 door truck') {
            return 'coupe';
        }
        if (lower.includes('sedan') || lower.includes('four door') || lower === '4 door truck') {
            return 'sedan';
        }
        if (lower.includes('xl suv') || lower.includes('van') || lower.includes('xlsuv')) {
            return 'xlsuv';
        }
        if (lower.includes('suv') || lower.includes('truck')) {
            return 'suv';
        }
        return null;
    }

    function getVehicleSizeLabel(size) {
        const labels = {
            coupe: 'Coupe',
            sedan: 'Sedan',
            suv: 'SUV',
            xlsuv: 'XL SUV/Van'
        };
        return labels[size] || size;
    }

    function getServiceTypeLabel(type) {
        const labels = {
            interior: 'Interior',
            exterior: 'Exterior',
            combo: 'Combo',
            express: 'Express'
        };
        return labels[type] || type;
    }

    function getTintTypeLabel(type) {
        const labels = {
            carbon: 'Carbon',
            ceramic: 'Ceramic'
        };
        return labels[type] || type;
    }

    // Sort services consistently within a category
    function sortServices(services, categoryKey) {
        return [...services].sort((a, b) => {
            const pA = a.parsed;
            const pB = b.parsed;

            if (categoryKey === 'windowTint') {
                // Sort by: area (alphabetically), then tint type (carbon before ceramic)
                const areaCompare = (pA.tintArea || '').localeCompare(pB.tintArea || '');
                if (areaCompare !== 0) return areaCompare;

                // Carbon before Ceramic
                const tintOrder = { carbon: 1, ceramic: 2 };
                const tintA = tintOrder[pA.tintType] || 99;
                const tintB = tintOrder[pB.tintType] || 99;
                return tintA - tintB;
            }

            if (categoryKey === 'detail') {
                // Sort by: vehicle size, then service type, then level
                const sizeOrder = { coupe: 1, sedan: 2, suv: 3, xlsuv: 4 };
                const sizeA = sizeOrder[pA.vehicleSize] || 99;
                const sizeB = sizeOrder[pB.vehicleSize] || 99;
                if (sizeA !== sizeB) return sizeA - sizeB;

                const typeOrder = { express: 1, interior: 2, exterior: 3, combo: 4 };
                const typeA = typeOrder[pA.serviceType] || 99;
                const typeB = typeOrder[pB.serviceType] || 99;
                if (typeA !== typeB) return typeA - typeB;

                // Then by level
                const levelA = pA.level || 99;
                const levelB = pB.level || 99;
                return levelA - levelB;
            }

            // Default: alphabetical by name
            return a.name.localeCompare(b.name);
        });
    }

    // DOM Elements
    const elements = {
        progressFill: document.getElementById('progressFill'),
        steps: document.querySelectorAll('.step'),
        stepPanels: document.querySelectorAll('.step-panel'),
        nextBtn: document.getElementById('nextBtn'),
        backBtn: document.getElementById('backBtn'),
        servicesContainer: document.getElementById('servicesContainer'),
        serviceSearch: document.getElementById('serviceSearch'),
        categoryTabs: document.getElementById('categoryTabs'),
        subFilters: document.getElementById('subFilters'),
        calendarGrid: document.getElementById('calendarGrid'),
        calendarTitle: document.getElementById('calendarTitle'),
        prevMonth: document.getElementById('prevMonth'),
        nextMonth: document.getElementById('nextMonth'),
        timeSlotsContainer: document.getElementById('timeSlotsContainer'),
        selectedDateText: document.getElementById('selectedDateText'),
        toastContainer: document.getElementById('toastContainer'),
        widgetFooter: document.getElementById('widgetFooter'),
        successPanel: document.getElementById('successPanel'),
        confirmationNumber: document.getElementById('confirmationNumber')
    };

    // API Functions
    async function fetchServices() {
        try {
            const response = await fetch(`${API_BASE_URL}/services`);
            if (!response.ok) throw new Error('Failed to load services');
            const data = await response.json();
            return data.services;
        } catch (error) {
            showToast('Failed to load services. Please try again.', 'error');
            throw error;
        }
    }

    async function fetchAvailability(serviceId, date) {
        try {
            const dateStr = formatDateForAPI(date);
            const response = await fetch(`${API_BASE_URL}/availability?service_id=${serviceId}&date=${dateStr}`);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || 'Failed to load availability');
            }
            const data = await response.json();
            // Store duration and close time for dynamic overnight calculation
            state.serviceDurationMinutes = data.duration_minutes || 60;
            state.businessHoursClose = data.business_hours_close || '18:00';
            return data.slots;
        } catch (error) {
            showToast(error.message || 'Failed to load available times.', 'error');
            throw error;
        }
    }

    // Calculate overnight status dynamically based on slot start time and service duration
    function calculateOvernightInfo(slotStart) {
        const [startHour, startMin] = slotStart.split(':').map(Number);
        const [closeHour, closeMin] = state.businessHoursClose.split(':').map(Number);

        const startMinutes = startHour * 60 + startMin;
        const closeMinutes = closeHour * 60 + closeMin;
        const minutesUntilClose = closeMinutes - startMinutes;

        const isOvernight = state.serviceDurationMinutes > minutesUntilClose;

        if (!isOvernight) {
            return { overnight: false, estimatedDays: 1 };
        }

        // Calculate how many days needed (assuming ~10 hour work days)
        const workDayMinutes = 600; // 10 hours
        const remainingAfterDay1 = state.serviceDurationMinutes - minutesUntilClose;
        const additionalDays = Math.ceil(remainingAfterDay1 / workDayMinutes);

        return {
            overnight: true,
            estimatedDays: 1 + additionalDays
        };
    }

    async function submitBooking() {
        const slotDate = formatDateForAPI(state.selectedDate);
        const slotStart = `${slotDate}T${state.selectedSlot.start}:00`;
        const slotEnd = `${slotDate}T${state.selectedSlot.end}:00`;

        const payload = {
            service_id: state.selectedService.id,
            slot_start: slotStart,
            slot_end: slotEnd,
            customer: {
                firstName: state.customer.firstName,
                lastName: state.customer.lastName,
                email: state.customer.email || null,
                phone: state.customer.phone || null
            },
            vehicle: {
                year: parseInt(state.vehicle.year, 10),
                make: state.vehicle.make,
                model: state.vehicle.model,
                vin: state.vehicle.vin || null
            }
        };

        try {
            const response = await fetch(`${API_BASE_URL}/book`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || 'Booking failed');
            }

            return await response.json();
        } catch (error) {
            showToast(error.message || 'Failed to complete booking.', 'error');
            throw error;
        }
    }

    // Rendering Functions

    // Parse all services and group by category
    function parseAndGroupServices(services) {
        const parsed = services.map(service => ({
            ...service,
            parsed: parseServiceName(service.name, service.category)
        }));

        // Group by category key
        const groups = {
            bedliner: [],
            consultation: [],
            windowTint: [],
            alignment: [],
            detail: [],
            other: []
        };

        parsed.forEach(service => {
            const key = service.parsed.categoryKey;
            if (groups[key]) {
                groups[key].push(service);
            } else {
                groups.other.push(service);
            }
        });

        return { parsed, groups };
    }

    // Get unique filter values for a category
    function getFilterOptions(services, categoryKey) {
        const options = {
            vehicleSizes: new Set(),
            serviceTypes: new Set(),
            tintTypes: new Set(),
            tintAreas: new Set()
        };

        services.forEach(service => {
            const p = service.parsed;
            if (p.vehicleSize) options.vehicleSizes.add(p.vehicleSize);
            if (p.serviceType) options.serviceTypes.add(p.serviceType);
            if (p.tintType) options.tintTypes.add(p.tintType);
            if (p.tintArea) options.tintAreas.add(p.tintArea);
        });

        return {
            vehicleSizes: Array.from(options.vehicleSizes),
            serviceTypes: Array.from(options.serviceTypes),
            tintTypes: Array.from(options.tintTypes),
            tintAreas: Array.from(options.tintAreas)
        };
    }

    // Apply filters to services
    function applyFilters(services, categoryKey) {
        if (!categoryKey) return services;

        return services.filter(service => {
            const p = service.parsed;

            if (categoryKey === 'detail') {
                const filters = state.filters.detail;
                if (filters.vehicleSize && p.vehicleSize !== filters.vehicleSize) return false;
                if (filters.serviceType && p.serviceType !== filters.serviceType) return false;
            }

            if (categoryKey === 'windowTint') {
                const filters = state.filters.windowTint;
                if (filters.tintType && p.tintType !== filters.tintType) return false;
                if (filters.tintArea && p.tintArea !== filters.tintArea) return false;
            }

            return true;
        });
    }

    // Search filter
    function filterBySearch(services, query) {
        if (!query.trim()) return services;
        const lowerQuery = query.toLowerCase().trim();
        return services.filter(service =>
            service.name.toLowerCase().includes(lowerQuery) ||
            (service.category && service.category.toLowerCase().includes(lowerQuery))
        );
    }

    // Render category tabs
    function renderCategoryTabs(groups) {
        if (!elements.categoryTabs) return;

        const tabs = [];

        // Get all category keys sorted by priority
        const sortedCategories = Object.keys(CATEGORIES).sort((a, b) =>
            CATEGORIES[a].priority - CATEGORIES[b].priority
        );

        // Build tabs for categories that have services
        sortedCategories.forEach(key => {
            if (groups[key] && groups[key].length > 0) {
                tabs.push({
                    key,
                    label: CATEGORIES[key].label,
                    count: groups[key].length
                });
            }
        });

        // Auto-select first tab if none selected
        if (!state.selectedCategory && tabs.length > 0) {
            state.selectedCategory = tabs[0].key;
        }

        elements.categoryTabs.innerHTML = tabs.map(tab => `
            <button type="button"
                    class="category-tab ${state.selectedCategory === tab.key ? 'active' : ''}"
                    data-category="${tab.key}">
                ${escapeHtml(tab.label)}
                <span class="tab-count">${tab.count}</span>
            </button>
        `).join('');

        // Add click handlers
        elements.categoryTabs.querySelectorAll('.category-tab').forEach(tabBtn => {
            tabBtn.addEventListener('click', () => {
                state.selectedCategory = tabBtn.dataset.category;
                // Reset filters when switching categories
                state.filters.detail = { vehicleSize: null, serviceType: null };
                state.filters.windowTint = { tintType: null, tintArea: null };
                renderServiceSelection();
            });
        });
    }

    // Render sub-filters for current category
    function renderSubFilters(services, categoryKey) {
        if (!elements.subFilters) return;

        if (!categoryKey || categoryKey === 'alignment' || categoryKey === 'other') {
            elements.subFilters.innerHTML = '';
            elements.subFilters.style.display = 'none';
            return;
        }

        const options = getFilterOptions(services, categoryKey);
        let filtersHtml = '';

        if (categoryKey === 'detail') {
            // Vehicle size filter
            if (options.vehicleSizes.length > 1) {
                const sizeOrder = ['coupe', 'sedan', 'suv', 'xlsuv'];
                const sortedSizes = options.vehicleSizes.sort((a, b) =>
                    sizeOrder.indexOf(a) - sizeOrder.indexOf(b)
                );

                filtersHtml += `
                    <div class="filter-group">
                        <span class="filter-label">Vehicle:</span>
                        <div class="filter-chips">
                            <button type="button" class="filter-chip ${!state.filters.detail.vehicleSize ? 'active' : ''}"
                                    data-filter="vehicleSize" data-value="">Any</button>
                            ${sortedSizes.map(size => `
                                <button type="button"
                                        class="filter-chip ${state.filters.detail.vehicleSize === size ? 'active' : ''}"
                                        data-filter="vehicleSize"
                                        data-value="${size}">
                                    ${getVehicleSizeLabel(size)}
                                </button>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            // Service type filter
            if (options.serviceTypes.length > 1) {
                const typeOrder = ['express', 'interior', 'exterior', 'combo'];
                const sortedTypes = options.serviceTypes.sort((a, b) =>
                    typeOrder.indexOf(a) - typeOrder.indexOf(b)
                );

                filtersHtml += `
                    <div class="filter-group">
                        <span class="filter-label">Type:</span>
                        <div class="filter-chips">
                            <button type="button" class="filter-chip ${!state.filters.detail.serviceType ? 'active' : ''}"
                                    data-filter="serviceType" data-value="">Any</button>
                            ${sortedTypes.map(type => `
                                <button type="button"
                                        class="filter-chip ${state.filters.detail.serviceType === type ? 'active' : ''}"
                                        data-filter="serviceType"
                                        data-value="${type}">
                                    ${getServiceTypeLabel(type)}
                                </button>
                            `).join('')}
                        </div>
                    </div>
                `;
            }
        }

        if (categoryKey === 'windowTint') {
            // Tint type filter
            if (options.tintTypes.length > 1) {
                filtersHtml += `
                    <div class="filter-group">
                        <span class="filter-label">Type:</span>
                        <div class="filter-chips">
                            <button type="button" class="filter-chip ${!state.filters.windowTint.tintType ? 'active' : ''}"
                                    data-filter="tintType" data-value="">Any</button>
                            ${options.tintTypes.map(type => `
                                <button type="button"
                                        class="filter-chip ${state.filters.windowTint.tintType === type ? 'active' : ''}"
                                        data-filter="tintType"
                                        data-value="${type}">
                                    ${getTintTypeLabel(type)}${type === 'ceramic' ? ' ★' : ''}
                                </button>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            // Tint area filter - group similar areas
            if (options.tintAreas.length > 1) {
                // Simplify areas into groups
                const areaGroups = [];
                const seenGroups = new Set();

                options.tintAreas.forEach(area => {
                    const lower = area.toLowerCase();
                    let groupKey = area;

                    if (lower.includes('full')) groupKey = 'Full Vehicle';
                    else if (lower.includes('windshield') && !lower.includes('strip')) groupKey = 'Windshield';
                    else if (lower.includes('sunstrip') || lower.includes('sun strip')) groupKey = 'Sunstrip';
                    else if (lower.includes('door') || lower.includes('front')) groupKey = 'Front Doors';

                    if (!seenGroups.has(groupKey)) {
                        seenGroups.add(groupKey);
                        areaGroups.push({ key: area, label: groupKey });
                    }
                });

                if (areaGroups.length > 1) {
                    filtersHtml += `
                        <div class="filter-group">
                            <span class="filter-label">Area:</span>
                            <div class="filter-chips">
                                <button type="button" class="filter-chip ${!state.filters.windowTint.tintArea ? 'active' : ''}"
                                        data-filter="tintArea" data-value="">Any</button>
                                ${areaGroups.slice(0, 4).map(area => `
                                    <button type="button"
                                            class="filter-chip ${state.filters.windowTint.tintArea === area.key ? 'active' : ''}"
                                            data-filter="tintArea"
                                            data-value="${escapeHtml(area.key)}">
                                        ${escapeHtml(area.label)}
                                    </button>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }
            }
        }

        elements.subFilters.innerHTML = filtersHtml;
        elements.subFilters.style.display = filtersHtml ? 'block' : 'none';

        // Add click handlers for filter chips
        elements.subFilters.querySelectorAll('.filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const filterName = chip.dataset.filter;
                const value = chip.dataset.value || null;

                if (categoryKey === 'detail') {
                    state.filters.detail[filterName] = value;
                } else if (categoryKey === 'windowTint') {
                    state.filters.windowTint[filterName] = value;
                }

                renderServiceSelection();
            });
        });
    }

    // Main render function for service selection
    function renderServiceSelection() {
        if (!state.services.length) {
            elements.servicesContainer.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">&#128269;</div>
                    <p>No services available at this time.</p>
                </div>
            `;
            return;
        }

        // Parse and group services
        const { parsed, groups } = parseAndGroupServices(state.services);
        state.parsedServices = parsed;

        // If search is active, show flat search results
        if (state.searchQuery.trim()) {
            let searchResults = filterBySearch(parsed, state.searchQuery);
            if (elements.categoryTabs) elements.categoryTabs.style.display = 'none';
            if (elements.subFilters) elements.subFilters.style.display = 'none';

            if (!searchResults.length) {
                elements.servicesContainer.innerHTML = `
                    <div class="no-results">
                        <div class="no-results-icon">&#128269;</div>
                        <p>No services match "${escapeHtml(state.searchQuery)}"</p>
                        <button type="button" class="btn-clear-search" onclick="document.getElementById('serviceSearch').value=''; this.closest('.widget-container').querySelector('#serviceSearch').dispatchEvent(new Event('input'));">Clear search</button>
                    </div>
                `;
                return;
            }

            // Sort search results alphabetically
            searchResults = searchResults.sort((a, b) => a.name.localeCompare(b.name));
            renderServiceCards(searchResults);
            return;
        }

        // Show tabs and filters
        if (elements.categoryTabs) elements.categoryTabs.style.display = 'flex';

        // Render tabs
        renderCategoryTabs(groups);

        // Get services for selected category and sort them
        const categoryServices = sortServices(groups[state.selectedCategory] || [], state.selectedCategory);

        // Render sub-filters
        renderSubFilters(categoryServices, state.selectedCategory);

        // Apply filters (sorting is preserved)
        const filteredServices = applyFilters(categoryServices, state.selectedCategory);

        if (!filteredServices.length) {
            elements.servicesContainer.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">&#128269;</div>
                    <p>No services match your filters</p>
                    <button type="button" class="btn-clear-filters">Clear filters</button>
                </div>
            `;

            // Add clear filters handler
            const clearBtn = elements.servicesContainer.querySelector('.btn-clear-filters');
            if (clearBtn) {
                clearBtn.addEventListener('click', () => {
                    state.filters.detail = { vehicleSize: null, serviceType: null };
                    state.filters.windowTint = { tintType: null, tintArea: null };
                    renderServiceSelection();
                });
            }
            return;
        }

        renderServiceCards(filteredServices);
    }

    // Render service cards
    function renderServiceCards(services) {
        elements.servicesContainer.innerHTML = `
            <div class="services-grid">
                ${services.map(service => renderServiceCard(service)).join('')}
            </div>
        `;

        // Add click handlers
        elements.servicesContainer.querySelectorAll('.service-card').forEach(card => {
            card.addEventListener('click', () => selectService(card.dataset.serviceId));
        });

        // Highlight selected service if any
        if (state.selectedService) {
            const selected = elements.servicesContainer.querySelector(
                `.service-card[data-service-id="${state.selectedService.id}"]`
            );
            if (selected) selected.classList.add('selected');
        }
    }

    // Render individual service card with enhanced display
    function renderServiceCard(service) {
        const isSelected = state.selectedService && state.selectedService.id === service.id;
        const p = service.parsed;

        // Build badges
        const badges = [];
        if (p.tintType) {
            badges.push(`<span class="badge badge-${p.tintType}">${getTintTypeLabel(p.tintType)}</span>`);
        }
        if (p.level) {
            badges.push(`<span class="badge badge-level">Lvl ${p.level}</span>`);
        }
        if (p.isCombo) {
            badges.push(`<span class="badge badge-combo">Combo</span>`);
        }

        // Display name - use parsed if available, otherwise original
        let displayName = p.displayName || service.name;

        // For Detail services, show a cleaner name
        if (p.categoryKey === 'detail' && p.serviceType) {
            const typePart = getServiceTypeLabel(p.serviceType);
            const levelPart = p.level ? ` Level ${p.level}` : '';
            displayName = `${typePart}${levelPart}`;
        }

        // Vehicle size indicator
        const vehicleSizeHtml = p.vehicleSizeRaw
            ? `<span class="vehicle-size">${escapeHtml(p.vehicleSizeRaw)}</span>`
            : '';

        const laborHoursHtml = service.laborHours
            ? `<span class="labor-hours">${formatLaborHours(service.laborHours)}</span>`
            : '';

        const priceHtml = service.totalCents
            ? `<span class="price">${formatPrice(service.totalCents)}</span>`
            : '';

        return `
            <div class="service-card ${isSelected ? 'selected' : ''}" data-service-id="${service.id}">
                ${badges.length ? `<div class="card-badges">${badges.join('')}</div>` : ''}
                <h3 class="card-title">${escapeHtml(displayName)}</h3>
                ${vehicleSizeHtml ? `<div class="card-vehicle">${vehicleSizeHtml}</div>` : ''}
                <div class="card-meta">
                    ${priceHtml}
                    ${laborHoursHtml}
                </div>
            </div>
        `;
    }

    // Legacy function for compatibility
    function renderServices(services) {
        renderServiceSelection();
    }

    function formatLaborHours(hours) {
        if (hours < 1) {
            const minutes = Math.round(hours * 60);
            return `~${minutes} min`;
        } else if (hours === 1) {
            return '~1 hour';
        } else {
            return `~${hours} hours`;
        }
    }

    function renderCalendar() {
        const year = state.calendarDate.getFullYear();
        const month = state.calendarDate.getMonth();
        const today = new Date();
        today.setHours(0, 0, 0, 0);

        // Update title
        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                           'July', 'August', 'September', 'October', 'November', 'December'];
        elements.calendarTitle.textContent = `${monthNames[month]} ${year}`;

        // Get first day of month and total days
        const firstDay = new Date(year, month, 1).getDay();
        const daysInMonth = new Date(year, month + 1, 0).getDate();

        // Build calendar grid
        let html = '';

        // Empty cells before first day
        for (let i = 0; i < firstDay; i++) {
            html += '<button type="button" class="calendar-day empty" disabled></button>';
        }

        // Days of month
        for (let day = 1; day <= daysInMonth; day++) {
            const date = new Date(year, month, day);
            const isPast = date < today;
            const isToday = date.getTime() === today.getTime();
            const isSelected = state.selectedDate &&
                              date.getTime() === state.selectedDate.getTime();

            const classes = ['calendar-day'];
            if (isPast) classes.push('disabled');
            if (isToday) classes.push('today');
            if (isSelected) classes.push('selected');

            html += `<button type="button" class="${classes.join(' ')}"
                            data-date="${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}"
                            ${isPast ? 'disabled' : ''}>${day}</button>`;
        }

        elements.calendarGrid.innerHTML = html;

        // Add click handlers
        elements.calendarGrid.querySelectorAll('.calendar-day:not(.disabled):not(.empty)').forEach(dayBtn => {
            dayBtn.addEventListener('click', () => selectDate(dayBtn.dataset.date));
        });
    }

    function renderTimeSlots(slots) {
        if (!slots.length) {
            elements.timeSlotsContainer.innerHTML = `
                <p class="no-slots-message">No available times for this date. Please select another date.</p>
            `;
            return;
        }

        elements.timeSlotsContainer.innerHTML = slots.map(slot => {
            // Dynamically calculate overnight status based on start time and duration
            const { overnight, estimatedDays } = calculateOvernightInfo(slot.start);
            const overnightClass = overnight ? 'overnight' : '';
            const overnightBadge = overnight
                ? `<div class="overnight-badge">${estimatedDays} day${estimatedDays > 1 ? 's' : ''}</div>`
                : '';
            const overnightNote = overnight
                ? '<div class="overnight-note">Vehicle stays overnight</div>'
                : '';

            return `
                <button type="button" class="time-slot ${overnightClass}"
                        data-start="${slot.start}"
                        data-end="${slot.end}">
                    ${overnightBadge}
                    <span class="slot-time">${formatTime(slot.start)}</span>
                    <div class="tech-count">${slot.available_techs} tech${slot.available_techs > 1 ? 's' : ''} available</div>
                    ${overnightNote}
                </button>
            `;
        }).join('');

        // Add click handlers
        elements.timeSlotsContainer.querySelectorAll('.time-slot').forEach(slotBtn => {
            slotBtn.addEventListener('click', () => {
                selectTimeSlot(slotBtn.dataset.start, slotBtn.dataset.end);
            });
        });
    }

    function renderConfirmation() {
        document.getElementById('summaryService').textContent = state.selectedService.name;

        const dateStr = formatDateDisplay(state.selectedDate);
        const timeStr = `${formatTime(state.selectedSlot.start)} - ${formatTime(state.selectedSlot.end)}`;
        let dateTimeText = `${dateStr} at ${timeStr}`;

        // Add overnight notice if applicable
        if (state.selectedSlot.overnight) {
            dateTimeText += `\n(${state.selectedSlot.estimatedDays}-day service - vehicle stays overnight)`;
        }
        document.getElementById('summaryDateTime').innerHTML = escapeHtml(dateTimeText).replace(/\n/g, '<br>');

        let customerInfo = `${state.customer.firstName} ${state.customer.lastName}`;
        if (state.customer.email) customerInfo += `\n${state.customer.email}`;
        if (state.customer.phone) customerInfo += `\n${state.customer.phone}`;
        document.getElementById('summaryCustomer').innerHTML = escapeHtml(customerInfo).replace(/\n/g, '<br>');

        const vehicleInfo = `${state.vehicle.year} ${state.vehicle.make} ${state.vehicle.model}`;
        document.getElementById('summaryVehicle').textContent = vehicleInfo;
    }

    // Booking Summary Strip
    function renderBookingSummary() {
        const summaryEl = document.getElementById('bookingSummary');
        if (!summaryEl) return;

        if (state.currentStep <= 1 || state.currentStep >= 6) {
            summaryEl.classList.remove('visible');
            while (summaryEl.firstChild) summaryEl.removeChild(summaryEl.firstChild);
            return;
        }

        const items = [];

        // Service (steps 2+)
        if (state.selectedService) {
            const parts = [state.selectedService.name];
            if (state.selectedService.totalCents) {
                parts.push(formatPrice(state.selectedService.totalCents));
            }
            if (state.selectedService.laborHours) {
                parts.push(formatLaborHours(state.selectedService.laborHours));
            }
            items.push({ step: 1, label: 'Service', value: parts.join('  \u00B7  ') });
        }

        // Date (steps 3+)
        if (state.currentStep > 2 && state.selectedDate) {
            items.push({ step: 2, label: 'Date', value: formatDateDisplay(state.selectedDate) });
        }

        // Time (steps 4+)
        if (state.currentStep > 3 && state.selectedSlot) {
            const timeValue = formatTime(state.selectedSlot.start) + ' - ' + formatTime(state.selectedSlot.end);
            items.push({ step: 3, label: 'Time', value: timeValue });
        }

        // Contact (steps 5+)
        if (state.currentStep > 4 && state.customer.firstName) {
            items.push({ step: 4, label: 'Contact', value: state.customer.firstName + ' ' + state.customer.lastName });
        }

        // Vehicle (step 6)
        if (state.currentStep > 5 && state.vehicle.year) {
            items.push({ step: 5, label: 'Vehicle', value: state.vehicle.year + ' ' + state.vehicle.make + ' ' + state.vehicle.model });
        }

        if (!items.length) {
            summaryEl.classList.remove('visible');
            while (summaryEl.firstChild) summaryEl.removeChild(summaryEl.firstChild);
            return;
        }

        // Build DOM nodes safely
        while (summaryEl.firstChild) summaryEl.removeChild(summaryEl.firstChild);

        items.forEach(function(item) {
            var row = document.createElement('div');
            row.className = 'summary-item';

            var label = document.createElement('span');
            label.className = 'summary-item-label';
            label.textContent = item.label + ':';

            var value = document.createElement('span');
            value.className = 'summary-item-value';
            value.textContent = item.value;

            var editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'summary-item-edit';
            editBtn.textContent = 'edit';
            editBtn.dataset.step = item.step;
            editBtn.addEventListener('click', function() {
                goToStep(parseInt(editBtn.dataset.step, 10));
            });

            row.appendChild(label);
            row.appendChild(value);
            row.appendChild(editBtn);
            summaryEl.appendChild(row);
        });

        summaryEl.classList.add('visible');
    }

    // Selection Handlers
    function selectService(serviceId) {
        state.selectedService = state.services.find(s => s.id === serviceId);

        // Update UI
        elements.servicesContainer.querySelectorAll('.service-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.serviceId === serviceId);
        });

        updateNextButton();
    }

    function selectDate(dateStr) {
        const [year, month, day] = dateStr.split('-').map(Number);
        state.selectedDate = new Date(year, month - 1, day);

        // Reset time slot selection
        state.selectedSlot = null;

        renderCalendar();
        updateNextButton();
    }

    async function loadTimeSlots() {
        if (!state.selectedService || !state.selectedDate) return;

        // Show loading
        elements.timeSlotsContainer.innerHTML = `
            <div class="loading-spinner">
                <div class="spinner"></div>
                <span>Loading available times...</span>
            </div>
        `;

        // Update date text
        elements.selectedDateText.textContent = formatDateDisplay(state.selectedDate);

        try {
            state.availableSlots = await fetchAvailability(
                state.selectedService.id,
                state.selectedDate
            );
            renderTimeSlots(state.availableSlots);
        } catch (error) {
            elements.timeSlotsContainer.innerHTML = `
                <p class="no-slots-message">Failed to load available times. Please try again.</p>
            `;
        }
    }

    function selectTimeSlot(start, end) {
        // Calculate overnight info dynamically based on current duration and start time
        const { overnight, estimatedDays } = calculateOvernightInfo(start);
        state.selectedSlot = { start, end, overnight, estimatedDays };

        // Update UI
        elements.timeSlotsContainer.querySelectorAll('.time-slot').forEach(slot => {
            slot.classList.toggle('selected',
                slot.dataset.start === start && slot.dataset.end === end);
        });

        updateNextButton();
    }

    // Step Navigation
    function goToStep(step) {
        if (step < 1 || step > TOTAL_STEPS) return;

        state.currentStep = step;

        // Update progress bar
        elements.progressFill.style.width = `${(step / TOTAL_STEPS) * 100}%`;

        // Update step indicators
        elements.steps.forEach((stepEl, index) => {
            stepEl.classList.remove('active', 'completed');
            if (index + 1 < step) {
                stepEl.classList.add('completed');
            } else if (index + 1 === step) {
                stepEl.classList.add('active');
            }
        });

        // Show current panel
        elements.stepPanels.forEach(panel => panel.classList.remove('active'));
        const currentPanel = document.getElementById(`step${step}`);
        if (currentPanel) {
            currentPanel.classList.add('active');
        }

        // Update booking summary strip
        renderBookingSummary();

        // Update buttons
        elements.backBtn.style.display = step > 1 ? 'inline-block' : 'none';
        elements.nextBtn.textContent = step === TOTAL_STEPS ? 'Book Appointment' : 'Continue';

        // Load step-specific data
        if (step === 3) {
            loadTimeSlots();
        } else if (step === 6) {
            renderConfirmation();
        }

        updateNextButton();
    }

    function nextStep() {
        if (state.currentStep === TOTAL_STEPS) {
            handleBooking();
        } else {
            goToStep(state.currentStep + 1);
        }
    }

    function prevStep() {
        goToStep(state.currentStep - 1);
    }

    // Form Handling
    function collectCustomerInfo() {
        state.customer.firstName = document.getElementById('firstName').value.trim();
        state.customer.lastName = document.getElementById('lastName').value.trim();
        state.customer.email = document.getElementById('email').value.trim();
        state.customer.phone = document.getElementById('phone').value.trim();
    }

    function collectVehicleInfo() {
        state.vehicle.year = document.getElementById('vehicleYear').value.trim();
        state.vehicle.make = document.getElementById('vehicleMake').value.trim();
        state.vehicle.model = document.getElementById('vehicleModel').value.trim();
        state.vehicle.vin = document.getElementById('vehicleVin').value.trim();
    }

    function validateCustomerForm() {
        let isValid = true;

        // First name
        const firstName = document.getElementById('firstName').value.trim();
        if (!firstName) {
            showFieldError('firstName', 'First name is required');
            isValid = false;
        } else {
            clearFieldError('firstName');
        }

        // Last name
        const lastName = document.getElementById('lastName').value.trim();
        if (!lastName) {
            showFieldError('lastName', 'Last name is required');
            isValid = false;
        } else {
            clearFieldError('lastName');
        }

        // Email (optional but must be valid if provided)
        const email = document.getElementById('email').value.trim();
        if (email && !isValidEmail(email)) {
            showFieldError('email', 'Please enter a valid email');
            isValid = false;
        } else {
            clearFieldError('email');
        }

        return isValid;
    }

    function validateVehicleForm() {
        let isValid = true;

        // Year
        const year = document.getElementById('vehicleYear').value.trim();
        const yearNum = parseInt(year, 10);
        if (!year || yearNum < 1900 || yearNum > 2100) {
            showFieldError('year', 'Please enter a valid year (1900-2100)');
            isValid = false;
        } else {
            clearFieldError('year');
        }

        // Make
        const make = document.getElementById('vehicleMake').value.trim();
        if (!make) {
            showFieldError('make', 'Vehicle make is required');
            isValid = false;
        } else {
            clearFieldError('make');
        }

        // Model
        const model = document.getElementById('vehicleModel').value.trim();
        if (!model) {
            showFieldError('model', 'Vehicle model is required');
            isValid = false;
        } else {
            clearFieldError('model');
        }

        return isValid;
    }

    function showFieldError(field, message) {
        const input = document.getElementById(field === 'year' ? 'vehicleYear' :
                      field === 'make' ? 'vehicleMake' :
                      field === 'model' ? 'vehicleModel' :
                      field === 'vin' ? 'vehicleVin' : field);
        const errorEl = document.getElementById(`${field}Error`);

        if (input) input.classList.add('error');
        if (errorEl) errorEl.textContent = message;
    }

    function clearFieldError(field) {
        const input = document.getElementById(field === 'year' ? 'vehicleYear' :
                      field === 'make' ? 'vehicleMake' :
                      field === 'model' ? 'vehicleModel' :
                      field === 'vin' ? 'vehicleVin' : field);
        const errorEl = document.getElementById(`${field}Error`);

        if (input) input.classList.remove('error');
        if (errorEl) errorEl.textContent = '';
    }

    // Booking Handler
    async function handleBooking() {
        elements.nextBtn.disabled = true;
        elements.nextBtn.textContent = 'Booking...';

        try {
            const result = await submitBooking();

            // Show success
            elements.stepPanels.forEach(panel => panel.classList.remove('active'));
            elements.successPanel.classList.add('active');
            elements.widgetFooter.style.display = 'none';
            elements.confirmationNumber.textContent = result.confirmation_number;

            showToast('Your appointment has been booked!', 'success');
        } catch (error) {
            elements.nextBtn.disabled = false;
            elements.nextBtn.textContent = 'Book Appointment';
        }
    }

    // UI Helpers
    function updateNextButton() {
        let canProceed = false;

        switch (state.currentStep) {
            case 1:
                canProceed = !!state.selectedService;
                break;
            case 2:
                canProceed = !!state.selectedDate;
                break;
            case 3:
                canProceed = !!state.selectedSlot;
                break;
            case 4:
                collectCustomerInfo();
                canProceed = state.customer.firstName && state.customer.lastName;
                break;
            case 5:
                collectVehicleInfo();
                canProceed = state.vehicle.year && state.vehicle.make && state.vehicle.model;
                break;
            case 6:
                canProceed = true;
                break;
        }

        elements.nextBtn.disabled = !canProceed;
    }

    function showToast(message, type = 'error') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        elements.toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 5000);
    }

    // Utility Functions
    function formatPrice(cents) {
        return '$' + (cents / 100).toFixed(2);
    }

    function formatDateForAPI(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function formatDateDisplay(date) {
        const options = { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' };
        return date.toLocaleDateString('en-US', options);
    }

    function formatTime(timeStr) {
        const [hours, minutes] = timeStr.split(':').map(Number);
        const period = hours >= 12 ? 'PM' : 'AM';
        const displayHours = hours % 12 || 12;
        return `${displayHours}:${String(minutes).padStart(2, '0')} ${period}`;
    }

    function isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Event Listeners
    function setupEventListeners() {
        // Navigation buttons
        elements.nextBtn.addEventListener('click', () => {
            // Validate current step before proceeding
            if (state.currentStep === 4 && !validateCustomerForm()) {
                return;
            }
            if (state.currentStep === 5 && !validateVehicleForm()) {
                return;
            }
            nextStep();
        });

        elements.backBtn.addEventListener('click', prevStep);

        // Calendar navigation
        elements.prevMonth.addEventListener('click', () => {
            state.calendarDate.setMonth(state.calendarDate.getMonth() - 1);
            renderCalendar();
        });

        elements.nextMonth.addEventListener('click', () => {
            state.calendarDate.setMonth(state.calendarDate.getMonth() + 1);
            renderCalendar();
        });

        // Service search
        if (elements.serviceSearch) {
            let debounceTimer;
            elements.serviceSearch.addEventListener('input', (e) => {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(() => {
                    state.searchQuery = e.target.value;
                    renderServiceSelection();
                }, 150);
            });

            // Clear search on escape
            elements.serviceSearch.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    e.target.value = '';
                    state.searchQuery = '';
                    renderServiceSelection();
                }
            });
        }

        // Form input listeners for real-time validation
        ['firstName', 'lastName', 'email', 'phone'].forEach(field => {
            const input = document.getElementById(field);
            if (input) {
                input.addEventListener('input', () => {
                    clearFieldError(field);
                    updateNextButton();
                });
            }
        });

        ['vehicleYear', 'vehicleMake', 'vehicleModel', 'vehicleVin'].forEach(inputId => {
            const input = document.getElementById(inputId);
            if (input) {
                input.addEventListener('input', () => {
                    const field = inputId.replace('vehicle', '').toLowerCase();
                    clearFieldError(field);
                    updateNextButton();
                });
            }
        });
    }

    // Parse URL parameters
    function getUrlParams() {
        const params = new URLSearchParams(window.location.search);
        return {
            serviceId: params.get('service') || params.get('service_id'),
            serviceName: params.get('service_name')
        };
    }

    // Pre-select service by ID or name
    async function preselectService(services, serviceId, serviceName) {
        let service = null;

        if (serviceId) {
            service = services.find(s => s.id === serviceId);
        }

        if (!service && serviceName) {
            // Try exact match first, then partial match
            const lowerName = serviceName.toLowerCase();
            service = services.find(s => s.name.toLowerCase() === lowerName) ||
                      services.find(s => s.name.toLowerCase().includes(lowerName));
        }

        if (service) {
            state.selectedService = service;

            // Parse the service to get its category
            const parsed = parseServiceName(service.name, service.category);

            // Auto-select the appropriate category tab
            state.selectedCategory = parsed.categoryKey;

            renderServiceSelection();
            // Skip to date selection (step 2)
            goToStep(2);
            return true;
        }

        return false;
    }

    // Initialize
    async function init() {
        setupEventListeners();
        renderCalendar();
        goToStep(1);

        // Load services
        try {
            state.services = await fetchServices();
            renderServiceSelection();

            // Check for pre-selected service from URL params
            const urlParams = getUrlParams();
            if (urlParams.serviceId || urlParams.serviceName) {
                await preselectService(state.services, urlParams.serviceId, urlParams.serviceName);
            }
        } catch (error) {
            // Error already handled in fetchServices
        }
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
