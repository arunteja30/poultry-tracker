// Enhanced PWA Features - Install Prompt, Offline Detection, Touch Gestures
let deferredPrompt;
let isOnline = navigator.onLine;

// Install Prompt Management
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  showInstallButton();
});

function showInstallButton() {
  // Create install button if it doesn't exist
  if (!document.getElementById('installBtn')) {
    const installBtn = document.createElement('button');
    installBtn.id = 'installBtn';
    installBtn.innerHTML = '📱 Install App';
    installBtn.className = 'btn btn-primary btn-sm position-fixed';
    installBtn.style.cssText = 'bottom: 20px; right: 20px; z-index: 1000; border-radius: 25px;';
    installBtn.onclick = installApp;
    document.body.appendChild(installBtn);
  }
}

async function installApp() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      console.log('User accepted the install prompt');
      hideInstallButton();
    }
    deferredPrompt = null;
  }
}

function hideInstallButton() {
  const installBtn = document.getElementById('installBtn');
  if (installBtn) {
    installBtn.remove();
  }
}

window.addEventListener('appinstalled', () => {
  hideInstallButton();
  showNotification('App installed successfully! 🎉');
  deferredPrompt = null;
});

// Service Worker Registration
window.addEventListener('load', () => {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
      .then(registration => {
        console.log('SW registered: ', registration);
      })
      .catch(registrationError => {
        console.log('SW registration failed: ', registrationError);
      });
  }
});

// Offline/Online Detection
function updateOnlineStatus() {
  const wasOnline = isOnline;
  isOnline = navigator.onLine;
  
  if (!isOnline && wasOnline) {
    showNotification('You are offline. Some features may be unavailable.', 'warning');
  } else if (isOnline && !wasOnline) {
    showNotification('You are back online! 📶', 'success');
  }
  
  // Update UI based on connection status
  document.body.classList.toggle('offline', !isOnline);
}

window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);

// Notification System
function showNotification(message, type = 'info') {
  // Remove existing notifications
  const existingNotifications = document.querySelectorAll('.pwa-notification');
  existingNotifications.forEach(notification => notification.remove());
  
  const notification = document.createElement('div');
  notification.className = `alert alert-${type} pwa-notification position-fixed`;
  notification.style.cssText = 'top: 20px; left: 50%; transform: translateX(-50%); z-index: 1050; min-width: 300px; text-align: center;';
  notification.innerHTML = `
    <div>${message}</div>
    <button type="button" class="btn-close btn-close-white" onclick="this.parentElement.remove()"></button>
  `;
  
  document.body.appendChild(notification);
  
  // Auto-remove after 5 seconds
  setTimeout(() => {
    if (notification.parentElement) {
      notification.remove();
    }
  }, 5000);
}

// Touch Gesture Support
let touchStartX, touchStartY, touchEndX, touchEndY;

document.addEventListener('touchstart', (e) => {
  touchStartX = e.changedTouches[0].screenX;
  touchStartY = e.changedTouches[0].screenY;
});

document.addEventListener('touchend', (e) => {
  touchEndX = e.changedTouches[0].screenX;
  touchEndY = e.changedTouches[0].screenY;
  handleSwipe();
});

function handleSwipe() {
  const deltaX = touchEndX - touchStartX;
  const deltaY = touchEndY - touchStartY;
  const minSwipeDistance = 50;
  
  if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > minSwipeDistance) {
    if (deltaX > 0) {
      // Swipe right
      console.log('Swiped right');
    } else {
      // Swipe left
      console.log('Swiped left');
    }
  }
}

// Performance Monitoring
if ('performance' in window) {
  window.addEventListener('load', () => {
    setTimeout(() => {
      const perfData = performance.getEntriesByType('navigation')[0];
      if (perfData) {
        console.log(`Page load time: ${perfData.loadEventEnd - perfData.loadEventStart}ms`);
      }
    }, 0);
  });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  updateOnlineStatus();
  
  // Add offline styles
  const style = document.createElement('style');
  style.textContent = `
    .offline {
      filter: grayscale(0.5);
    }
    .offline::before {
      content: "⚠️ Offline Mode";
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      background: #dc3545;
      color: white;
      text-align: center;
      padding: 5px;
      z-index: 9999;
      font-size: 14px;
    }
  `;
  document.head.appendChild(style);
});
