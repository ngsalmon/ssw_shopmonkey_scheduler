/**
 * Scheduling Widget Embed Script
 *
 * Usage:
 * 1. Add this script to your page:
 *    <script src="https://your-api-domain.com/static/embed.js"
 *            data-api-url="https://your-api-domain.com"
 *            data-button-text="Book Now"
 *            data-primary-color="#FF6B00">
 *    </script>
 *
 * 2. Or initialize manually:
 *    SchedulingWidget.init({
 *      apiUrl: 'https://your-api-domain.com',
 *      buttonText: 'Book Appointment',
 *      primaryColor: '#FF6B00',
 *      mode: 'button' // 'button', 'inline', or 'modal'
 *    });
 */

(function() {
    'use strict';

    const SchedulingWidget = {
        isInitialized: false,
        config: {
            apiUrl: '',
            buttonText: 'Book Appointment',
            primaryColor: '#FF6B00',
            mode: 'button', // 'button', 'inline', or 'modal'
            containerId: null,
            buttonPosition: 'bottom-right' // 'bottom-right', 'bottom-left'
        },

        init: function(options = {}) {
            if (this.isInitialized) return;

            // Merge options
            Object.assign(this.config, options);

            // Auto-detect API URL from script src if not provided
            if (!this.config.apiUrl) {
                const script = document.currentScript ||
                    document.querySelector('script[src*="embed.js"]');
                if (script) {
                    const scriptUrl = new URL(script.src);
                    this.config.apiUrl = scriptUrl.origin;

                    // Read data attributes
                    if (script.dataset.apiUrl) this.config.apiUrl = script.dataset.apiUrl;
                    if (script.dataset.buttonText) this.config.buttonText = script.dataset.buttonText;
                    if (script.dataset.primaryColor) this.config.primaryColor = script.dataset.primaryColor;
                    if (script.dataset.mode) this.config.mode = script.dataset.mode;
                    if (script.dataset.containerId) this.config.containerId = script.dataset.containerId;
                    if (script.dataset.buttonPosition) this.config.buttonPosition = script.dataset.buttonPosition;
                }
            }

            this.injectStyles();

            switch (this.config.mode) {
                case 'inline':
                    this.renderInline();
                    break;
                case 'modal':
                    this.renderModal();
                    break;
                case 'button':
                default:
                    this.renderFloatingButton();
                    break;
            }

            this.isInitialized = true;
        },

        injectStyles: function() {
            const style = document.createElement('style');
            style.textContent = `
                .sw-floating-btn {
                    position: fixed;
                    ${this.config.buttonPosition === 'bottom-left' ? 'left: 20px;' : 'right: 20px;'}
                    bottom: 20px;
                    background-color: ${this.config.primaryColor};
                    color: white;
                    border: none;
                    border-radius: 50px;
                    padding: 16px 24px;
                    font-size: 16px;
                    font-weight: 500;
                    cursor: pointer;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                    z-index: 9998;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    transition: transform 0.2s, box-shadow 0.2s;
                    font-family: Arial, Helvetica, sans-serif;
                }
                .sw-floating-btn:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
                }
                .sw-floating-btn svg {
                    width: 20px;
                    height: 20px;
                }
                .sw-modal-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background-color: rgba(0, 0, 0, 0.7);
                    z-index: 9999;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                    opacity: 0;
                    visibility: hidden;
                    transition: opacity 0.3s, visibility 0.3s;
                }
                .sw-modal-overlay.active {
                    opacity: 1;
                    visibility: visible;
                }
                .sw-modal-container {
                    background-color: #1a1a1a;
                    border-radius: 12px;
                    width: 100%;
                    max-width: 850px;
                    max-height: 90vh;
                    overflow: hidden;
                    position: relative;
                    transform: scale(0.9);
                    transition: transform 0.3s;
                }
                .sw-modal-overlay.active .sw-modal-container {
                    transform: scale(1);
                }
                .sw-modal-close {
                    position: absolute;
                    top: 12px;
                    right: 12px;
                    background: rgba(255, 255, 255, 0.1);
                    border: none;
                    color: white;
                    width: 36px;
                    height: 36px;
                    border-radius: 50%;
                    cursor: pointer;
                    font-size: 24px;
                    line-height: 1;
                    z-index: 10;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .sw-modal-close:hover {
                    background: rgba(255, 255, 255, 0.2);
                }
                .sw-modal-iframe {
                    width: 100%;
                    height: 80vh;
                    max-height: 700px;
                    border: none;
                }
                .sw-inline-container {
                    width: 100%;
                    min-height: 600px;
                }
                .sw-inline-iframe {
                    width: 100%;
                    min-height: 600px;
                    border: none;
                }
                @media (max-width: 600px) {
                    .sw-floating-btn {
                        padding: 14px 20px;
                        font-size: 14px;
                    }
                    .sw-floating-btn span {
                        display: none;
                    }
                    .sw-modal-container {
                        max-height: 95vh;
                    }
                    .sw-modal-iframe {
                        height: 90vh;
                    }
                }
            `;
            document.head.appendChild(style);
        },

        renderFloatingButton: function() {
            const button = document.createElement('button');
            button.className = 'sw-floating-btn';
            button.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                          d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                <span>${this.escapeHtml(this.config.buttonText)}</span>
            `;
            button.addEventListener('click', () => this.openModal());
            document.body.appendChild(button);

            // Create modal
            this.createModal();
        },

        renderModal: function() {
            this.createModal();
            // Optionally auto-open or provide trigger method
        },

        createModal: function() {
            const overlay = document.createElement('div');
            overlay.className = 'sw-modal-overlay';
            overlay.id = 'sw-modal-overlay';
            overlay.innerHTML = `
                <div class="sw-modal-container">
                    <button class="sw-modal-close" aria-label="Close">&times;</button>
                    <iframe class="sw-modal-iframe" src="${this.config.apiUrl}/schedule" title="Schedule Appointment"></iframe>
                </div>
            `;

            // Close on overlay click
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.closeModal();
            });

            // Close button
            overlay.querySelector('.sw-modal-close').addEventListener('click', () => this.closeModal());

            // Close on escape key
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') this.closeModal();
            });

            document.body.appendChild(overlay);
        },

        renderInline: function() {
            const container = this.config.containerId ?
                document.getElementById(this.config.containerId) :
                document.querySelector('[data-scheduling-widget]');

            if (!container) {
                console.error('Scheduling Widget: No container found for inline mode');
                return;
            }

            container.className = 'sw-inline-container';
            container.innerHTML = `
                <iframe class="sw-inline-iframe" src="${this.config.apiUrl}/schedule" title="Schedule Appointment"></iframe>
            `;
        },

        openModal: function() {
            const overlay = document.getElementById('sw-modal-overlay');
            if (overlay) {
                overlay.classList.add('active');
                document.body.style.overflow = 'hidden';
            }
        },

        closeModal: function() {
            const overlay = document.getElementById('sw-modal-overlay');
            if (overlay) {
                overlay.classList.remove('active');
                document.body.style.overflow = '';
            }
        },

        escapeHtml: function(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    };

    // Expose globally
    window.SchedulingWidget = SchedulingWidget;

    // Auto-initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => SchedulingWidget.init());
    } else {
        SchedulingWidget.init();
    }
})();
