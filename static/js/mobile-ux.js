// Mobile UX Enhancement JavaScript
// ===============================

class MobileUXEnhancer {
    constructor() {
        this.init();
    }

    init() {
        this.setupPullToRefresh();
        this.setupSwipeGestures();
        this.setupAutoSave();
        this.setupLazyLoading();
        this.setupFormEnhancements();
        this.setupTouchFeedback();
        this.setupProgressIndicators();
    }

    // Pull-to-Refresh Implementation
    setupPullToRefresh() {
        let startY = 0;
        let currentY = 0;
        let isPulling = false;
        const threshold = 80;
        
        const pullRefreshElement = document.createElement('div');
        pullRefreshElement.className = 'mobile-pull-refresh';
        pullRefreshElement.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Pull to refresh';
        pullRefreshElement.style.cssText = `
            position: fixed;
            top: -80px;
            left: 50%;
            transform: translateX(-50%);
            background: #1877f2;
            color: white;
            padding: 12px 20px;
            border-radius: 0 0 20px 20px;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s ease;
            z-index: 1000;
            display: none;
            min-width: 150px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        `;
        document.body.appendChild(pullRefreshElement);

        document.addEventListener('touchstart', (e) => {
            if (window.scrollY === 0 && !document.querySelector('.offcanvas.show')) {
                startY = e.touches[0].clientY;
                isPulling = true;
                pullRefreshElement.style.display = 'block';
            }
        });

        document.addEventListener('touchmove', (e) => {
            if (!isPulling) return;
            
            currentY = e.touches[0].clientY;
            const pullDistance = currentY - startY;
            
            if (pullDistance > 0 && pullDistance < threshold * 2) {
                e.preventDefault();
                const progress = Math.min(pullDistance / threshold, 1);
                pullRefreshElement.style.top = `${-80 + (80 * progress)}px`;
                
                if (pullDistance > threshold) {
                    pullRefreshElement.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Release to refresh';
                    pullRefreshElement.style.backgroundColor = '#28a745';
                }
            }
        });

        document.addEventListener('touchend', () => {
            if (!isPulling) return;
            
            const pullDistance = currentY - startY;
            if (pullDistance > threshold) {
                this.triggerRefresh();
            } else {
                pullRefreshElement.style.display = 'none';
            }
            
            pullRefreshElement.style.top = '-80px';
            pullRefreshElement.style.backgroundColor = '#1877f2';
            pullRefreshElement.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Pull to refresh';
            isPulling = false;
        });
    }

    triggerRefresh() {
        // Show loading state
        const pullRefresh = document.querySelector('.mobile-pull-refresh');
        if (pullRefresh) {
            pullRefresh.style.display = 'block';
            pullRefresh.style.top = '0px';
            pullRefresh.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Refreshing...';
            
            // Simulate refresh (replace with actual refresh logic)
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        }
    }

    // Swipe Gestures for List Items
    setupSwipeGestures() {
        const swipeableItems = document.querySelectorAll('.mobile-swipeable');
        
        swipeableItems.forEach(item => {
            let startX = 0;
            let currentX = 0;
            let isScrolling = false;
            
            item.addEventListener('touchstart', (e) => {
                startX = e.touches[0].clientX;
            });
            
            item.addEventListener('touchmove', (e) => {
                currentX = e.touches[0].clientX;
                const diffX = startX - currentX;
                const diffY = Math.abs(e.touches[0].clientY - startY);
                
                if (diffY < diffX && diffX > 50) {
                    item.style.transform = `translateX(-${Math.min(diffX, 200)}px)`;
                    if (diffX > 100) {
                        item.classList.add('swiped');
                    }
                }
            });
            
            item.addEventListener('touchend', () => {
                const diffX = startX - currentX;
                if (diffX < 100) {
                    item.style.transform = 'translateX(0)';
                    item.classList.remove('swiped');
                }
            });
        });
    }

    // Auto-save Functionality
    setupAutoSave() {
        const forms = document.querySelectorAll('form[data-autosave="true"]');
        
        // Only create indicator if there are auto-save forms
        if (forms.length === 0) {
            return;
        }
        
        const indicator = this.createAutoSaveIndicator();
        
        forms.forEach(form => {
            const inputs = form.querySelectorAll('input, textarea, select');
            let saveTimeout;
            
            inputs.forEach(input => {
                input.addEventListener('input', () => {
                    clearTimeout(saveTimeout);
                    this.showAutoSaveIndicator('saving');
                    
                    saveTimeout = setTimeout(() => {
                        this.autoSaveForm(form);
                    }, 2000);
                });
            });
        });
    }

    createAutoSaveIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'mobile-autosave-indicator';
        indicator.innerHTML = 'Saved';
        document.body.appendChild(indicator);
        return indicator;
    }

    showAutoSaveIndicator(state) {
        const indicator = document.querySelector('.mobile-autosave-indicator');
        if (!indicator) {
            return; // No indicator exists, don't show anything
        }
        
        indicator.className = `mobile-autosave-indicator ${state} show`;
        
        if (state === 'saving') {
            indicator.innerHTML = '<i class="bi bi-cloud-upload"></i> Saving...';
        } else if (state === 'saved') {
            indicator.innerHTML = '<i class="bi bi-check-circle"></i> Saved';
        } else if (state === 'error') {
            indicator.innerHTML = '<i class="bi bi-exclamation-triangle"></i> Error';
        }
        
        setTimeout(() => {
            indicator.classList.remove('show');
        }, 2000);
    }

    autoSaveForm(form) {
        const formData = new FormData(form);
        const data = Object.fromEntries(formData.entries());
        
        // Save to localStorage as fallback
        localStorage.setItem(`autosave_${form.id || 'form'}`, JSON.stringify(data));
        
        // Here you would typically send to server
        // For now, just show success
        this.showAutoSaveIndicator('saved');
    }

    // Lazy Loading Implementation
    setupLazyLoading() {
        const lazyElements = document.querySelectorAll('.mobile-lazy-load');
        
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('loaded');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        
        lazyElements.forEach(el => observer.observe(el));
    }

    // Form Enhancement Features
    setupFormEnhancements() {
        // Auto-focus first input
        const firstInput = document.querySelector('.mobile-input-enhanced');
        if (firstInput && window.innerWidth >= 768) {
            setTimeout(() => firstInput.focus(), 300);
        }
        
        // Real-time validation
        const inputs = document.querySelectorAll('.mobile-input-enhanced');
        inputs.forEach(input => {
            input.addEventListener('blur', () => this.validateInput(input));
            input.addEventListener('input', () => this.clearValidation(input));
        });
        
        // Form submission with progress
        const forms = document.querySelectorAll('form');
        forms.forEach(form => {
            form.addEventListener('submit', (e) => {
                if (!this.validateForm(form)) {
                    e.preventDefault();
                    return false;
                }
                this.showProgress(0);
                this.simulateProgress();
            });
        });
    }

    validateInput(input) {
        const value = input.value.trim();
        const type = input.type;
        const required = input.required;
        
        let isValid = true;
        let message = '';
        
        if (required && !value) {
            isValid = false;
            message = 'This field is required';
        } else if (type === 'email' && value && !this.isValidEmail(value)) {
            isValid = false;
            message = 'Please enter a valid email';
        } else if (type === 'number' && value && isNaN(value)) {
            isValid = false;
            message = 'Please enter a valid number';
        }
        
        if (!isValid) {
            input.classList.add('mobile-input-error');
            this.showValidationMessage(input, message, 'error');
        } else if (value) {
            input.classList.add('mobile-input-success');
            this.showValidationMessage(input, 'Looks good!', 'success');
        }
        
        return isValid;
    }

    clearValidation(input) {
        input.classList.remove('mobile-input-error', 'mobile-input-success');
        this.removeValidationMessage(input);
    }

    showValidationMessage(input, message, type) {
        this.removeValidationMessage(input);
        
        const messageEl = document.createElement('div');
        messageEl.className = `mobile-${type}-message`;
        messageEl.innerHTML = `<i class="bi bi-${type === 'error' ? 'exclamation-circle' : 'check-circle'}"></i> ${message}`;
        
        input.parentNode.appendChild(messageEl);
    }

    removeValidationMessage(input) {
        const existingMessage = input.parentNode.querySelector('.mobile-error-message, .mobile-success-message');
        if (existingMessage) {
            existingMessage.remove();
        }
    }

    validateForm(form) {
        const inputs = form.querySelectorAll('.mobile-input-enhanced');
        let isValid = true;
        
        inputs.forEach(input => {
            if (!this.validateInput(input)) {
                isValid = false;
            }
        });
        
        return isValid;
    }

    isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    // Touch Feedback
    setupTouchFeedback() {
        const touchElements = document.querySelectorAll('.mobile-touch-enhanced');
        
        touchElements.forEach(element => {
            element.addEventListener('touchstart', () => {
                element.style.transform = 'scale(0.95)';
            });
            
            element.addEventListener('touchend', () => {
                element.style.transform = 'scale(1)';
            });
        });
    }

    // Progress Indicators
    setupProgressIndicators() {
        const progressBar = document.createElement('div');
        progressBar.className = 'mobile-progress-indicator';
        progressBar.innerHTML = '<div class="mobile-progress-bar"></div>';
        document.body.appendChild(progressBar);
    }

    showProgress(percentage) {
        const progressBar = document.querySelector('.mobile-progress-bar');
        if (progressBar) {
            progressBar.style.width = `${percentage}%`;
            if (percentage > 0) {
                progressBar.parentElement.style.display = 'block';
            } else {
                progressBar.parentElement.style.display = 'none';
            }
        }
    }

    simulateProgress() {
        let progress = 0;
        const interval = setInterval(() => {
            progress += Math.random() * 30;
            if (progress >= 100) {
                progress = 100;
                clearInterval(interval);
                setTimeout(() => this.showProgress(0), 500);
            }
            this.showProgress(progress);
        }, 200);
    }
}

// Performance Optimizations
class PerformanceOptimizer {
    constructor() {
        this.setupImageOptimization();
        this.setupInfiniteScroll();
        this.minimizeReflows();
    }

    setupImageOptimization() {
        const images = document.querySelectorAll('.mobile-image-optimized');
        
        images.forEach(img => {
            img.setAttribute('data-loaded', 'false');
            
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const image = entry.target;
                        image.onload = () => {
                            image.setAttribute('data-loaded', 'true');
                        };
                        observer.unobserve(image);
                    }
                });
            });
            
            observer.observe(img);
        });
    }

    setupInfiniteScroll() {
        const containers = document.querySelectorAll('.mobile-list-container');
        
        containers.forEach(container => {
            container.addEventListener('scroll', this.throttle(() => {
                if (container.scrollTop + container.clientHeight >= container.scrollHeight - 100) {
                    this.loadMoreItems(container);
                }
            }, 250));
        });
    }

    loadMoreItems(container) {
        // Add loading indicator
        const loading = document.createElement('div');
        loading.className = 'mobile-skeleton';
        loading.style.height = '60px';
        container.appendChild(loading);
        
        // Simulate loading (replace with actual data loading)
        setTimeout(() => {
            loading.remove();
            // Add new items here
        }, 1000);
    }

    throttle(func, limit) {
        let inThrottle;
        return function() {
            const args = arguments;
            const context = this;
            if (!inThrottle) {
                func.apply(context, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        }
    }

    minimizeReflows() {
        // Use requestAnimationFrame for DOM updates
        this.pendingUpdates = [];
        this.updateScheduled = false;
        
        this.batchUpdate = (updateFunction) => {
            this.pendingUpdates.push(updateFunction);
            if (!this.updateScheduled) {
                this.updateScheduled = true;
                requestAnimationFrame(() => {
                    this.pendingUpdates.forEach(update => update());
                    this.pendingUpdates = [];
                    this.updateScheduled = false;
                });
            }
        };
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new MobileUXEnhancer();
    new PerformanceOptimizer();
});

// Export for use in other scripts
window.MobileUX = {
    enhancer: null,
    optimizer: null,
    init() {
        this.enhancer = new MobileUXEnhancer();
        this.optimizer = new PerformanceOptimizer();
    }
};
