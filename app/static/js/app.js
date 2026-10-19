/**
 * Airutil Frontend Application Logic.
 * 
 * Demonstrates:
 * 1. Accessible Theme Switching (Light / Dark / System) with persistence.
 * 2. Live Round-Robin Ingress Testing against /healthz and / endpoints.
 */

document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    setupThemeToggle();
});

// ============================================================================
// Theme Management (Light, Dark, System)
// ============================================================================

function initTheme() {
    const savedTheme = localStorage.getItem("airutil-theme") || "system";
    applyTheme(savedTheme);
}

function applyTheme(theme) {
    const html = document.documentElement;
    html.setAttribute("data-theme", theme);
    localStorage.setItem("airutil-theme", theme);

    const themeIcon = document.getElementById("theme-icon");
    if (themeIcon) {
        if (theme === "dark") {
            themeIcon.textContent = "🌙";
        } else if (theme === "light") {
            themeIcon.textContent = "☀️";
        } else {
            themeIcon.textContent = "🌓";
        }
    }
}

function setupThemeToggle() {
    const toggleBtn = document.getElementById("theme-toggle");
    if (!toggleBtn) return;

    toggleBtn.addEventListener("click", () => {
        const currentTheme = document.documentElement.getAttribute("data-theme") || "system";
        let nextTheme = "dark";
        if (currentTheme === "system") {
            nextTheme = "light";
        } else if (currentTheme === "light") {
            nextTheme = "dark";
        } else {
            nextTheme = "system";
        }
        applyTheme(nextTheme);
    });
}

// ============================================================================
// Round-Robin Verification via Asynchronous Fetch
// ============================================================================

let pingCount = 0;

async function testRoundRobin() {
    const feedback = document.getElementById("rr-feedback");
    const liveReplica = document.getElementById("live-replica");
    const replicaLabel = document.getElementById("replica-label");
    const replicaPill = document.getElementById("replica-pill");

    if (feedback) {
        feedback.textContent = "Probing /healthz...";
    }

    try {
        const startTime = performance.now();
        // Add cache-busting timestamp to test round-robin proxy
        const res = await fetch(`/healthz?t=${Date.now()}`);
        const data = await res.json();
        const duration = Math.round(performance.now() - startTime);

        pingCount++;
        const replica = data.replica || "unknown";

        if (liveReplica) {
            liveReplica.textContent = replica;
        }
        if (replicaLabel) {
            replicaLabel.textContent = replica;
        }

        if (replicaPill) {
            replicaPill.classList.remove("pill-app1", "pill-app2");
            if (replica === "app1") {
                replicaPill.classList.add("pill-app1");
            } else if (replica === "app2") {
                replicaPill.classList.add("pill-app2");
            }
        }

        if (feedback) {
            feedback.innerHTML = `Ping #${pingCount}: Answered by <strong>${replica}</strong> (${duration}ms)`;
        }
    } catch (err) {
        if (feedback) {
            feedback.textContent = `Probe failed: ${err.message}`;
        }
    }
}
