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
        availableSlots: [],
        serviceDurationMinutes: 60,
        businessHoursClose: '18:00',
        calendarDate: new Date(),
        searchQuery: '',
        collapsedCategories: new Set()
    };

    // DOM Elements
    const elements = {
        progressFill: document.getElementById('progressFill'),
        steps: document.querySelectorAll('.step'),
        stepPanels: document.querySelectorAll('.step-panel'),
        nextBtn: document.getElementById('nextBtn'),
        backBtn: document.getElementById('backBtn'),
        servicesContainer: document.getElementById('servicesContainer'),
        serviceSearch: document.getElementById('serviceSearch'),
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
    function groupServicesByCategory(services) {
        const groups = {};
        const uncategorized = [];

        services.forEach(service => {
            const category = service.category || null;
            if (category) {
                if (!groups[category]) {
                    groups[category] = [];
                }
                groups[category].push(service);
            } else {
                uncategorized.push(service);
            }
        });

        // Sort categories alphabetically
        const sortedCategories = Object.keys(groups).sort();
        const result = sortedCategories.map(cat => ({
            name: cat,
            services: groups[cat]
        }));

        // Add uncategorized at the end if any
        if (uncategorized.length > 0) {
            result.push({
                name: 'Other Services',
                services: uncategorized
            });
        }

        return result;
    }

    function filterServices(services, query) {
        if (!query.trim()) return services;

        const lowerQuery = query.toLowerCase().trim();
        return services.filter(service =>
            service.name.toLowerCase().includes(lowerQuery) ||
            (service.category && service.category.toLowerCase().includes(lowerQuery))
        );
    }

    function renderServices(services) {
        if (!services.length) {
            elements.servicesContainer.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">&#128269;</div>
                    <p>No services available at this time.</p>
                </div>
            `;
            return;
        }

        // Filter based on search query
        const filteredServices = filterServices(services, state.searchQuery);

        if (!filteredServices.length) {
            elements.servicesContainer.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">&#128269;</div>
                    <p>No services match "${escapeHtml(state.searchQuery)}"</p>
                </div>
            `;
            return;
        }

        // Group by category
        const groups = groupServicesByCategory(filteredServices);

        // If searching and only one group with few items, or all in one category, show flat
        const showFlat = state.searchQuery.trim() && filteredServices.length <= 6;

        if (showFlat || groups.length === 1) {
            // Flat view for search results or single category
            elements.servicesContainer.innerHTML = `
                <div class="category-services" style="padding-left: 0;">
                    ${filteredServices.map(service => renderServiceCard(service)).join('')}
                </div>
            `;
        } else {
            // Grouped view
            elements.servicesContainer.innerHTML = groups.map(group => `
                <div class="category-group ${state.collapsedCategories.has(group.name) ? 'collapsed' : ''}"
                     data-category="${escapeHtml(group.name)}">
                    <div class="category-header">
                        <span class="category-toggle">&#9660;</span>
                        <span class="category-title">${escapeHtml(group.name)}</span>
                        <span class="category-count">${group.services.length}</span>
                    </div>
                    <div class="category-services">
                        ${group.services.map(service => renderServiceCard(service)).join('')}
                    </div>
                </div>
            `).join('');

            // Add category toggle handlers
            elements.servicesContainer.querySelectorAll('.category-header').forEach(header => {
                header.addEventListener('click', () => {
                    const group = header.parentElement;
                    const category = group.dataset.category;
                    group.classList.toggle('collapsed');
                    if (group.classList.contains('collapsed')) {
                        state.collapsedCategories.add(category);
                    } else {
                        state.collapsedCategories.delete(category);
                    }
                });
            });
        }

        // Add click handlers for service cards
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

    function renderServiceCard(service) {
        const isSelected = state.selectedService && state.selectedService.id === service.id;
        return `
            <div class="service-card ${isSelected ? 'selected' : ''}" data-service-id="${service.id}">
                <h3>${escapeHtml(service.name)}</h3>
                ${service.totalCents ? `<p class="price">${formatPrice(service.totalCents)}</p>` : ''}
            </div>
        `;
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
                    renderServices(state.services);
                }, 150);
            });

            // Clear search on escape
            elements.serviceSearch.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    e.target.value = '';
                    state.searchQuery = '';
                    renderServices(state.services);
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

    // Initialize
    async function init() {
        setupEventListeners();
        renderCalendar();
        goToStep(1);

        // Load services
        try {
            state.services = await fetchServices();
            renderServices(state.services);
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
