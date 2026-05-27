/* ==========================================================================
   ZeroChainAI — Interactive Scripts
   ========================================================================== */

// --- Matrix Rain Background ---
(function initMatrix() {
    const canvas = document.getElementById('matrix-bg');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const chars = '0123456789ABCDEFabcdef{}[]<>/\\|;:.,~!@#$%^&*';
    const fontSize = 14;
    let columns = Math.floor(canvas.width / fontSize);
    let drops = Array(columns).fill(1);

    function draw() {
        ctx.fillStyle = 'rgba(10, 11, 15, 0.12)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#00f0ff';
        ctx.font = fontSize + 'px JetBrains Mono, monospace';

        for (let i = 0; i < drops.length; i++) {
            const char = chars[Math.floor(Math.random() * chars.length)];
            ctx.fillText(char, i * fontSize, drops[i] * fontSize);
            if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) {
                drops[i] = 0;
            }
            drops[i]++;
        }
        requestAnimationFrame(draw);
    }

    // Restart columns on resize
    window.addEventListener('resize', () => {
        columns = Math.floor(canvas.width / fontSize);
        drops = Array(columns).fill(1);
    });

    draw();
})();

// --- Navbar Scroll Effect ---
(function initNavbar() {
    const navbar = document.getElementById('navbar');
    if (!navbar) return;
    window.addEventListener('scroll', () => {
        navbar.classList.toggle('scrolled', window.scrollY > 50);
    });
})();

// --- Mobile Nav Toggle ---
(function initMobileNav() {
    const toggle = document.getElementById('nav-toggle');
    const links = document.querySelector('.nav-links');
    if (!toggle || !links) return;

    toggle.addEventListener('click', () => {
        const isOpen = links.style.display === 'flex';
        links.style.display = isOpen ? 'none' : 'flex';
        links.style.flexDirection = 'column';
        links.style.position = 'absolute';
        links.style.top = '100%';
        links.style.left = '0';
        links.style.right = '0';
        links.style.background = 'rgba(10, 11, 15, 0.98)';
        links.style.padding = '24px';
        links.style.gap = '16px';
        links.style.borderBottom = '1px solid rgba(255,255,255,0.06)';
    });
})();

// --- Stat Counter Animation ---
(function initCounters() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const el = entry.target;
                const target = parseInt(el.dataset.target);
                if (!target) return;
                animateCounter(el, target);
                observer.unobserve(el);
            }
        });
    }, { threshold: 0.5 });

    document.querySelectorAll('[data-target]').forEach(el => observer.observe(el));

    function animateCounter(el, target) {
        let current = 0;
        const duration = 1500;
        const step = target / (duration / 16);
        const timer = setInterval(() => {
            current += step;
            if (current >= target) {
                current = target;
                clearInterval(timer);
            }
            el.textContent = Math.round(current);
        }, 16);
    }
})();

// --- Scroll Reveal ---
(function initReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

    document.querySelectorAll(
        '.service-card, .pipeline-step, .threat-layer, .cta-card'
    ).forEach((el, i) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
        el.style.transition = `all 0.6s ease ${i * 0.1}s`;
        observer.observe(el);
    });

    // CSS class for revealed
    const style = document.createElement('style');
    style.textContent = `.revealed { opacity: 1 !important; transform: translateY(0) !important; }`;
    document.head.appendChild(style);
})();

// --- Shield Attack Deflection Animation ---
(function initShieldDefense() {
    const container = document.getElementById('particles');
    if (!container) return;

    // Create canvas overlay for the attack animation
    const canvas = document.createElement('canvas');
    canvas.width = 400;
    canvas.height = 400;
    canvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;';
    container.parentElement.style.position = 'relative';
    container.parentElement.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    const W = canvas.width, H = canvas.height;
    const CX = W / 2, CY = H / 2;         // shield center
    const SHIELD_R = 52;                     // shield radius
    const SPAWN_MARGIN = 20;                 // spawn outside edges

    // Attack colors (red/orange = threats)
    const ATTACK_COLORS = ['#ff3b5c', '#ff6b35', '#f59e0b', '#ef4444', '#dc2626'];
    // Deflect color (cyan = shield energy)
    const DEFLECT_COLOR = '#00f0ff';

    class Projectile {
        constructor() { this.reset(); }

        reset() {
            // Spawn from a random edge (all 4 sides)
            const side = Math.floor(Math.random() * 4);
            switch(side) {
                case 0: this.x = -SPAWN_MARGIN; this.y = Math.random() * H; break;        // left
                case 1: this.x = W + SPAWN_MARGIN; this.y = Math.random() * H; break;     // right
                case 2: this.x = Math.random() * W; this.y = -SPAWN_MARGIN; break;        // top
                case 3: this.x = Math.random() * W; this.y = H + SPAWN_MARGIN; break;     // bottom
            }

            // Aim at shield center with slight randomness
            const aimX = CX + (Math.random() - 0.5) * 20;
            const aimY = CY + (Math.random() - 0.5) * 20;
            const dx = aimX - this.x;
            const dy = aimY - this.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            this.speed = 1.2 + Math.random() * 1.5;
            this.vx = (dx / dist) * this.speed;
            this.vy = (dy / dist) * this.speed;
            this.size = 2 + Math.random() * 2.5;
            this.color = ATTACK_COLORS[Math.floor(Math.random() * ATTACK_COLORS.length)];
            this.alpha = 0.7 + Math.random() * 0.3;
            this.trail = [];
            this.maxTrail = 8 + Math.floor(Math.random() * 6);
            this.deflected = false;
            this.deflectTimer = 0;
            this.flashAlpha = 0;
        }

        update() {
            // Save trail
            this.trail.push({ x: this.x, y: this.y, a: this.alpha });
            if (this.trail.length > this.maxTrail) this.trail.shift();

            this.x += this.vx;
            this.y += this.vy;

            if (!this.deflected) {
                // Check collision with shield
                const dx = this.x - CX;
                const dy = this.y - CY;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist <= SHIELD_R) {
                    // DEFLECT! Bounce away
                    this.deflected = true;
                    this.flashAlpha = 1.0;
                    this.color = DEFLECT_COLOR;

                    // Reflect velocity off shield surface (normal = outward from center)
                    const nx = dx / dist;
                    const ny = dy / dist;
                    const dot = this.vx * nx + this.vy * ny;
                    this.vx = this.vx - 2 * dot * nx;
                    this.vy = this.vy - 2 * dot * ny;

                    // Speed boost on deflection
                    const boost = 1.6 + Math.random() * 0.8;
                    this.vx *= boost;
                    this.vy *= boost;

                    // Push outside shield
                    this.x = CX + nx * (SHIELD_R + 3);
                    this.y = CY + ny * (SHIELD_R + 3);
                }
            } else {
                // After deflection: fade out
                this.alpha -= 0.012;
                this.flashAlpha *= 0.92;
                this.deflectTimer++;
            }

            // Reset if off-screen or fully faded
            if (this.x < -60 || this.x > W + 60 || this.y < -60 || this.y > H + 60 || this.alpha <= 0) {
                this.reset();
            }
        }

        draw(ctx) {
            // Draw trail
            for (let i = 0; i < this.trail.length; i++) {
                const t = this.trail[i];
                const progress = i / this.trail.length;
                ctx.beginPath();
                ctx.arc(t.x, t.y, this.size * progress * 0.6, 0, Math.PI * 2);
                ctx.fillStyle = this.deflected
                    ? `rgba(0, 240, 255, ${progress * 0.3 * this.alpha})`
                    : `rgba(255, 59, 92, ${progress * 0.25 * this.alpha})`;
                ctx.fill();
            }

            // Draw main projectile
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
            const r = this.deflected ? 0 : 255;
            const g = this.deflected ? 240 : 59;
            const b = this.deflected ? 255 : 92;
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${this.alpha})`;
            ctx.fill();

            // Glow
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size * 2.5, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${this.alpha * 0.15})`;
            ctx.fill();

            // Flash on deflection
            if (this.flashAlpha > 0.05) {
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size * 6, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(0, 240, 255, ${this.flashAlpha * 0.3})`;
                ctx.fill();
            }
        }
    }

    // Shield pulse ring on deflections
    const pulses = [];

    function addPulse() {
        pulses.push({ r: SHIELD_R, alpha: 0.6, speed: 1.5 });
    }

    // Spawn projectiles — stagger them for continuous attacks
    const projectiles = [];
    const NUM_PROJECTILES = 25;
    for (let i = 0; i < NUM_PROJECTILES; i++) {
        const p = new Projectile();
        // Stagger spawn positions along their path
        const randomProgress = Math.random() * 60;
        for (let s = 0; s < randomProgress; s++) p.update();
        projectiles.push(p);
    }

    let frameCount = 0;
    let lastPulse = 0;

    function animate() {
        ctx.clearRect(0, 0, W, H);
        frameCount++;

        // Draw shield glow (always visible)
        const shieldGlow = ctx.createRadialGradient(CX, CY, SHIELD_R * 0.5, CX, CY, SHIELD_R * 1.5);
        shieldGlow.addColorStop(0, 'rgba(0, 240, 255, 0.04)');
        shieldGlow.addColorStop(0.7, 'rgba(0, 240, 255, 0.02)');
        shieldGlow.addColorStop(1, 'transparent');
        ctx.beginPath();
        ctx.arc(CX, CY, SHIELD_R * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = shieldGlow;
        ctx.fill();

        // Thin shield boundary
        ctx.beginPath();
        ctx.arc(CX, CY, SHIELD_R, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(0, 240, 255, 0.12)';
        ctx.lineWidth = 1;
        ctx.stroke();

        // Update & draw projectiles
        let deflectedThisFrame = false;
        projectiles.forEach(p => {
            const wasDef = p.deflected;
            p.update();
            if (!wasDef && p.deflected) deflectedThisFrame = true;
            p.draw(ctx);
        });

        if (deflectedThisFrame && frameCount - lastPulse > 10) {
            addPulse();
            lastPulse = frameCount;
        }

        // Draw pulse rings
        for (let i = pulses.length - 1; i >= 0; i--) {
            const pulse = pulses[i];
            ctx.beginPath();
            ctx.arc(CX, CY, pulse.r, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(0, 240, 255, ${pulse.alpha})`;
            ctx.lineWidth = 1.5;
            ctx.stroke();
            pulse.r += pulse.speed;
            pulse.alpha -= 0.015;
            if (pulse.alpha <= 0) pulses.splice(i, 1);
        }

        requestAnimationFrame(animate);
    }

    animate();
})();

// --- Form Handler → Cloudflare Worker → Telegram ---
(function initForm() {
    const form = document.getElementById('contact-form');
    if (!form) return;

    // Worker endpoint — update after deploying worker
    const WORKER_URL = 'https://zerochainai-contact.workers.dev';

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('form-submit-btn');
        const originalHTML = btn.innerHTML;

        // Collect form data
        const data = {
            projectName: document.getElementById('project-name').value.trim(),
            email: document.getElementById('contact-email').value.trim(),
            projectUrl: document.getElementById('project-url').value.trim(),
            serviceType: document.getElementById('service-type').value,
            message: document.getElementById('project-message').value.trim(),
        };

        // Validate
        if (!data.projectName || !data.email) {
            shakeButton(btn);
            return;
        }

        // Loading state
        btn.classList.add('btn-loading');
        btn.innerHTML = '<span>Submitting...</span>';

        try {
            const response = await fetch(WORKER_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });

            const result = await response.json();

            if (response.ok && result.success) {
                // Success
                btn.classList.remove('btn-loading');
                btn.classList.add('btn-success');
                btn.innerHTML = '<span>✓ Request Sent Successfully!</span>';
                form.reset();

                setTimeout(() => {
                    btn.classList.remove('btn-success');
                    btn.innerHTML = originalHTML;
                }, 4000);
            } else {
                throw new Error(result.error || 'Submission failed');
            }
        } catch (err) {
            console.error('Form submission error:', err);

            // Error — fallback to Telegram direct link
            btn.classList.remove('btn-loading');
            btn.classList.add('btn-error');
            btn.innerHTML = '<span>⚠ Error — Try Telegram instead</span>';

            setTimeout(() => {
                btn.classList.remove('btn-error');
                btn.innerHTML = originalHTML;
            }, 4000);

            // Also offer Telegram as fallback
            const tgMsg = encodeURIComponent(
                `Audit Request:\nProject: ${data.projectName}\nEmail: ${data.email}\nURL: ${data.projectUrl || '—'}\nService: ${data.serviceType || '—'}\n\n${data.message || ''}`
            );
            const tgFallback = document.createElement('a');
            tgFallback.href = `https://t.me/ZeroChainAIbot?start=audit`;
            tgFallback.target = '_blank';
            tgFallback.style.cssText = 'display:block;text-align:center;margin-top:8px;color:#29b6f6;font-size:0.85rem;';
            tgFallback.textContent = '→ Open @ZeroChainAIbot on Telegram';
            form.appendChild(tgFallback);
            setTimeout(() => tgFallback.remove(), 6000);
        }
    });

    function shakeButton(btn) {
        btn.style.animation = 'shake 0.4s ease';
        setTimeout(() => btn.style.animation = '', 400);
    }

    // Add shake keyframes
    const shakeStyle = document.createElement('style');
    shakeStyle.textContent = `@keyframes shake { 0%,100%{transform:translateX(0)} 25%{transform:translateX(-6px)} 75%{transform:translateX(6px)} }`;
    document.head.appendChild(shakeStyle);
})();

// --- Smooth Scroll for anchor links ---
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            // Close mobile menu if open
            const links = document.querySelector('.nav-links');
            if (window.innerWidth <= 968 && links) {
                links.style.display = 'none';
            }
        }
    });
});
