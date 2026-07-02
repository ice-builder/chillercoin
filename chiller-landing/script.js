/* ═══════════════════════════════════════════════
   $CHILLER — Scripts v3 (Fixed & Clean)
   ═══════════════════════════════════════════════ */

// Polyfill for canvas roundRect (Safari < 16)
if (!CanvasRenderingContext2D.prototype.roundRect) {
    CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
        if (typeof r === 'number') r = [r];
        const rad = r[0] || 0;
        this.moveTo(x + rad, y);
        this.lineTo(x + w - rad, y);
        this.arcTo(x + w, y, x + w, y + rad, rad);
        this.lineTo(x + w, y + h - rad);
        this.arcTo(x + w, y + h, x + w - rad, y + h, rad);
        this.lineTo(x + rad, y + h);
        this.arcTo(x, y + h, x, y + h - rad, rad);
        this.lineTo(x, y + rad);
        this.arcTo(x, y, x + rad, y, rad);
        this.closePath();
    };
}


// ─── Floating Ice Cubes + Bubbles ───────────
(function initBackground() {
    const canvas = document.getElementById('particles');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let w, h;

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const cubes = [];
    const bubbles = [];

    function createCube() {
        return {
            x: Math.random() * w,
            y: Math.random() * h,
            size: Math.random() * 14 + 6,
            speedY: -(Math.random() * 0.3 + 0.1),
            speedX: (Math.random() - 0.5) * 0.2,
            rot: Math.random() * Math.PI * 2,
            rotSpd: (Math.random() - 0.5) * 0.006,
            opacity: Math.random() * 0.1 + 0.03,
            melt: 0.002 + Math.random() * 0.001
        };
    }

    function createBubble() {
        return {
            x: Math.random() * w,
            y: Math.random() * h,
            size: Math.random() * 3 + 1,
            speedY: -(Math.random() * 0.5 + 0.2),
            opacity: Math.random() * 0.12 + 0.02,
            wobble: Math.random() * 100
        };
    }

    for (let i = 0; i < 12; i++) cubes.push(createCube());
    for (let i = 0; i < 30; i++) bubbles.push(createBubble());

    function drawCube(c) {
        ctx.save();
        ctx.translate(c.x, c.y);
        ctx.rotate(c.rot);
        ctx.globalAlpha = c.opacity;
        const s = c.size;
        const r = s * 0.2;

        ctx.fillStyle = 'rgba(180, 230, 255, 0.4)';
        ctx.strokeStyle = 'rgba(150, 220, 255, 0.25)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(-s/2, -s/2, s, s, r);
        ctx.fill();
        ctx.stroke();

        // shine
        ctx.fillStyle = 'rgba(255,255,255,0.35)';
        ctx.beginPath();
        ctx.ellipse(-s*0.12, -s*0.12, s*0.13, s*0.06, -0.5, 0, Math.PI*2);
        ctx.fill();
        ctx.restore();
    }

    function drawBubble(b) {
        ctx.globalAlpha = b.opacity;
        ctx.fillStyle = 'rgba(100, 200, 255, 0.5)';
        ctx.beginPath();
        ctx.arc(b.x, b.y, b.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = b.opacity * 1.5;
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.beginPath();
        ctx.arc(b.x - b.size*0.25, b.y - b.size*0.25, b.size*0.2, 0, Math.PI*2);
        ctx.fill();
    }

    function animate() {
        ctx.clearRect(0, 0, w, h);
        cubes.forEach(c => {
            c.y += c.speedY;
            c.x += c.speedX + Math.sin(c.y * 0.008) * 0.15;
            c.rot += c.rotSpd;
            c.size -= c.melt;
            if (c.y < -30 || c.size < 3) {
                Object.assign(c, createCube());
                c.y = h + 20;
            }
            drawCube(c);
        });
        bubbles.forEach(b => {
            b.y += b.speedY;
            b.wobble += 0.02;
            b.x += Math.sin(b.wobble) * 0.25;
            if (b.y < -10) {
                Object.assign(b, createBubble());
                b.y = h + 10;
            }
            drawBubble(b);
        });
        ctx.globalAlpha = 1;
        requestAnimationFrame(animate);
    }
    animate();
})();


// ─── Navbar ─────────────────────────────────
window.addEventListener('scroll', () => {
    document.getElementById('navbar')?.classList.toggle('scrolled', window.scrollY > 50);
});


// ─── Hero cascade entrance ──────────────────
document.addEventListener('DOMContentLoaded', () => {
    const items = [
        { el: '.hero-badge', delay: 200 },
        { el: '.hero-line-1', delay: 500 },
        { el: '.hero-gradient', delay: 700 },
        { el: '.hero-sub', delay: 1000 },
        { el: '.hero-stats', delay: 1200 },
        { el: '.hero-actions', delay: 1500 },
        { el: '.hero-note', delay: 1700 },
    ];
    items.forEach(({ el, delay }) => {
        const node = document.querySelector(el);
        if (!node) return;
        node.style.opacity = '0';
        node.style.transform = 'translateY(25px)';
        setTimeout(() => {
            node.style.transition = 'opacity 0.8s ease, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1)';
            node.style.opacity = '1';
            node.style.transform = 'translateY(0)';
        }, delay);
    });
});


// ─── Animated Stat Counters ─────────────────
function animateCounters() {
    document.querySelectorAll('.stat-value').forEach((el, i) => {
        const target = parseInt(el.dataset.target);
        const suffix = el.dataset.suffix || '';
        const duration = 1800;

        setTimeout(() => {
            const start = performance.now();
            function tick(now) {
                const progress = Math.min((now - start) / duration, 1);
                const eased = 1 - Math.pow(1 - progress, 4); // ease-out quart
                el.textContent = Math.round(target * eased) + suffix;
                if (progress < 1) requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);

            // pop effect
            el.closest('.stat-card')?.animate([
                { transform: 'scale(1)' },
                { transform: 'scale(1.06)' },
                { transform: 'scale(1)' }
            ], { duration: 400, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)' });
        }, i * 150);
    });
}

const statsObs = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) { animateCounters(); statsObs.disconnect(); }
}, { threshold: 0.3 });
const heroStats = document.querySelector('.hero-stats');
if (heroStats) statsObs.observe(heroStats);


// ─── Scroll Reveal (universal) ──────────────
function setupReveal() {
    const selectors = '.step-card, .phil-card, .perf-stat-card, .perf-chart-card, .rm-card, .engine-features li, .origin-card, .engine-card, .flow-step, .rev-card, .not-scam-card, .comp-group, .mwm-col, .mwm-quote';
    const els = document.querySelectorAll(selectors);

    els.forEach((el, i) => {
        el.classList.add('reveal-item');
    });

    // Stagger by group
    document.querySelectorAll('.steps-grid .step-card').forEach((el, i) => el.style.setProperty('--delay', `${i * 0.12}s`));
    document.querySelectorAll('.phil-card').forEach((el, i) => el.style.setProperty('--delay', `${i * 0.1}s`));
    document.querySelectorAll('.trans-card').forEach((el, i) => el.style.setProperty('--delay', `${i * 0.1}s`));
    document.querySelectorAll('.engine-features li').forEach((el, i) => el.style.setProperty('--delay', `${i * 0.1}s`));
    document.querySelectorAll('.rm-card').forEach((el, i) => el.style.setProperty('--delay', `${i * 0.15}s`));

    const obs = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.08 });

    els.forEach(el => obs.observe(el));
}
setupReveal();

// Inject reveal CSS
const revealCSS = document.createElement('style');
revealCSS.textContent = `
    .reveal-item {
        opacity: 0;
        transform: translateY(35px);
        transition: opacity 0.7s ease, transform 0.7s cubic-bezier(0.16, 1, 0.3, 1);
        transition-delay: var(--delay, 0s);
    }
    .reveal-item.revealed {
        opacity: 1;
        transform: translateY(0);
    }
`;
document.head.appendChild(revealCSS);


// ─── Section Headers Reveal ─────────────────
document.querySelectorAll('.section-header').forEach(header => {
    const children = header.querySelectorAll('.section-tag, h2, p');
    children.forEach((el, i) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
    });

    const obs = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            children.forEach((el, i) => {
                setTimeout(() => {
                    el.style.transition = 'opacity 0.6s ease, transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                }, i * 120);
            });
            obs.disconnect();
        }
    }, { threshold: 0.3 });
    obs.observe(header);
});


// ─── Origin Story — paragraph reveal ────────
(function() {
    const paragraphs = document.querySelectorAll('.origin-story p');
    paragraphs.forEach(p => {
        p.style.opacity = '0';
        p.style.transform = 'translateY(18px)';
    });

    const obs = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            paragraphs.forEach((p, i) => {
                setTimeout(() => {
                    p.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
                    p.style.opacity = '1';
                    p.style.transform = 'translateY(0)';
                }, i * 180);
            });
            obs.disconnect();
        }
    }, { threshold: 0.2 });

    const origin = document.getElementById('origin');
    if (origin) obs.observe(origin);
})();


// ─── Scroll-triggered flow animations ───────
(function() {
    const scrollEls = document.querySelectorAll('.scroll-animate');
    if (!scrollEls.length) return;

    const obs = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                // Stagger children for grid layouts
                const children = entry.target.querySelectorAll('.vault-flow-card, .vm-item, .trans-card');
                if (children.length > 0) {
                    children.forEach((child, i) => {
                        child.style.transitionDelay = `${i * 0.15}s`;
                    });
                }
                entry.target.classList.add('visible');
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15 });

    scrollEls.forEach(el => obs.observe(el));
})();


// ─── 3D Card Tilt (only after revealed) ─────
document.addEventListener('mousemove', (e) => {
    const card = e.target.closest('.step-card, .phil-card, .perf-stat-card');
    if (!card || !card.classList.contains('revealed')) return;

    const rect = card.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;

    card.style.transition = 'transform 0.1s ease';
    card.style.transform = `translateY(-6px) perspective(600px) rotateX(${y * -8}deg) rotateY(${x * 8}deg)`;
});

document.addEventListener('mouseleave', (e) => {
    const card = e.target.closest('.step-card, .phil-card, .perf-stat-card');
    if (!card) return;
    card.style.transition = 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
    card.style.transform = 'translateY(0)';
}, true);

// Fix: reset on mouseout from cards
document.querySelectorAll('.step-card, .phil-card, .perf-stat-card').forEach(card => {
    card.addEventListener('mouseleave', () => {
        card.style.transition = 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
        card.style.transform = 'translateY(0)';
    });
});


// ─── NAV Chart — animated draw ──────────────
(function() {
    const container = document.getElementById('navChart');
    if (!container) return;

    const canvas = document.createElement('canvas');
    container.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    // Generate realistic equity curve
    const data = [];
    let v = 5000;
    for (let i = 0; i < 100; i++) {
        v += 42 + (Math.random() - 0.35) * 70 + Math.sin(i * 0.12) * 25;
        v = Math.max(v, 4900);
        data.push(v);
    }

    let progress = 0;
    let running = false;

    function resize() {
        canvas.width = container.clientWidth;
        canvas.height = container.clientHeight;
    }

    function draw() {
        const W = canvas.width, H = canvas.height;
        const pad = { t: 16, b: 24, l: 8, r: 8 };
        ctx.clearRect(0, 0, W, H);

        const min = Math.min(...data) * 0.98;
        const max = Math.max(...data) * 1.02;
        const n = Math.max(2, Math.floor(data.length * progress));
        const step = (W - pad.l - pad.r) / (data.length - 1);

        // Grid
        ctx.strokeStyle = 'rgba(0,100,200,0.06)';
        ctx.lineWidth = 1;
        for (let i = 0; i < 4; i++) {
            const y = pad.t + (H - pad.t - pad.b) * i / 3;
            ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
        }

        // Build path
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
            const x = pad.l + i * step;
            const y = pad.t + (1 - (data[i] - min) / (max - min)) * (H - pad.t - pad.b);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }

        // Glow
        ctx.save();
        ctx.strokeStyle = 'rgba(0,150,255,0.2)';
        ctx.lineWidth = 7;
        ctx.lineJoin = 'round';
        ctx.stroke();
        ctx.restore();

        // Line
        ctx.strokeStyle = '#0099ff';
        ctx.lineWidth = 2.5;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Fill under
        const lastI = n - 1;
        const lastX = pad.l + lastI * step;
        const lastY = pad.t + (1 - (data[lastI] - min) / (max - min)) * (H - pad.t - pad.b);
        ctx.lineTo(lastX, H - pad.b);
        ctx.lineTo(pad.l, H - pad.b);
        ctx.closePath();
        const grad = ctx.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, 'rgba(0,153,255,0.1)');
        grad.addColorStop(1, 'rgba(0,153,255,0)');
        ctx.fillStyle = grad;
        ctx.fill();

        // Pulsing dot
        const pulse = Math.sin(Date.now() * 0.004) * 0.5 + 0.5;
        ctx.beginPath(); ctx.arc(lastX, lastY, 4, 0, Math.PI*2);
        ctx.fillStyle = '#0099ff'; ctx.fill();
        ctx.beginPath(); ctx.arc(lastX, lastY, 8 + pulse * 6, 0, Math.PI*2);
        ctx.fillStyle = `rgba(0,153,255,${0.08 + pulse * 0.06})`; ctx.fill();

        // Label
        ctx.fillStyle = '#0077dd';
        ctx.font = 'bold 11px Inter, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText('$' + Math.round(data[lastI]).toLocaleString(), lastX - 8, lastY - 10);

        if (progress < 1) {
            progress += 0.015;
            requestAnimationFrame(draw);
        } else {
            requestAnimationFrame(draw); // keep pulse alive
        }
    }

    const obs = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && !running) {
            running = true;
            resize();
            draw();
        }
    }, { threshold: 0.2 });
    obs.observe(container);
    window.addEventListener('resize', () => { resize(); if (running) draw(); });
})();


// ─── Terminal Typewriter ────────────────────
(function() {
    const body = document.getElementById('terminalBody');
    if (!body) return;
    body.innerHTML = '';

    const lines = [
        ['🧠 Scanning 245 markets...', 't-info'],
        ['📊 BTC $59,328 | ETH $2,541 | SOL $178', 't-value'],
        ['⚡ Signal: ETHUSDT SHORT score=87 conf=92%', 't-success'],
        ['✅ OPENED ETHUSDT SHORT @ $2,541.20', 't-success'],
        ['🛡️ SL: $2,604.70 (2.5%) | TP: $2,465.00', 't-info'],
        ['📈 Trail Phase 1: ×3.0 (let it breathe)', 't-info'],
        ['🧠 Regime: CLEAN ✅ Full power mode', 't-warn'],
        ['⚡ Signal: SOLUSDT SHORT score=79', 't-success'],
        ['✅ OPENED SOLUSDT SHORT @ $178.42', 't-success'],
        ['📈 ETHUSDT +0.8% → Phase 2: ×1.5', 't-success'],
        ['💰 CLOSED ETHUSDT +1.24% ($31.20) 🧊', 't-success'],
        ['🧊 Just another day of chilling...', 't-value'],
        ['💰 CLOSED SOLUSDT +0.95% ($28.50) 🧊', 't-success'],
        ['📊 Daily: +$89.70 (+1.79%) WR:100% 🔥', 't-success'],
        ['🧊 Vault NAV: $10,565 (+115.05%)', 't-value'],
    ];

    let idx = 0;

    function typeOut(span, text, cb) {
        let i = 0;
        function next() {
            if (i < text.length) {
                span.textContent += text[i++];
                setTimeout(next, 10 + Math.random() * 8);
            } else if (cb) cb();
        }
        next();
    }

    function addLine() {
        const [text, cls] = lines[idx % lines.length];
        const time = new Date().toTimeString().slice(0, 8);

        const div = document.createElement('div');
        div.className = 'term-line';

        const ts = document.createElement('span');
        ts.className = 't-time';
        ts.textContent = time + ' ';

        const tx = document.createElement('span');
        tx.className = cls;

        div.appendChild(ts);
        div.appendChild(tx);
        body.appendChild(div);

        typeOut(tx, text, () => {
            while (body.children.length > 12) body.removeChild(body.firstChild);
            body.scrollTop = body.scrollHeight;
            idx++;
            setTimeout(addLine, 600 + Math.random() * 1200);
        });
    }

    // Start after terminal is visible
    const obs = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            addLine();
            obs.disconnect();
        }
    }, { threshold: 0.2 });
    const terminal = document.querySelector('.terminal');
    if (terminal) obs.observe(terminal); else addLine();
})();


// ─── CTA cursor glow ───────────────────────
(function() {
    const cta = document.querySelector('.cta-card');
    const glow = cta?.querySelector('.cta-glow');
    if (!cta || !glow) return;
    cta.addEventListener('mousemove', e => {
        const r = cta.getBoundingClientRect();
        glow.style.transition = 'transform 0.1s ease';
        glow.style.transform = `translate(${e.clientX - r.left - 300}px, ${e.clientY - r.top - 300}px)`;
    });
})();


// ─── Inject extra animation CSS ─────────────
const extraCSS = document.createElement('style');
extraCSS.textContent = `
    /* Logo float */
    .nav-logo-img { animation: logo-float 3s ease-in-out infinite; }
    @keyframes logo-float {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-3px); }
    }

    /* CTA button pulse */
    .hero-actions .btn-primary,
    .cta-actions .btn-primary {
        animation: btn-glow 2.5s ease-in-out infinite;
    }
    @keyframes btn-glow {
        0%, 100% { box-shadow: 0 4px 20px rgba(0,136,255,0.2); }
        50% { box-shadow: 0 6px 35px rgba(0,136,255,0.4); }
    }

    /* Origin emoji bounce */
    .origin-emoji { animation: emoji-bob 2.5s ease-in-out infinite; }
    @keyframes emoji-bob {
        0%, 100% { transform: translateY(0) rotate(0deg); }
        33% { transform: translateY(-8px) rotate(-4deg); }
        66% { transform: translateY(-4px) rotate(3deg); }
    }

    /* Roadmap active dot pulse */
    .rm-item.active .rm-dot { animation: dot-pulse 2s infinite; }
    @keyframes dot-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(0,153,255,0.35); }
        50% { box-shadow: 0 0 0 10px rgba(0,153,255,0); }
    }

    /* Badge shimmer effect */
    .hero-badge::after {
        content: '';
        position: absolute;
        top: 0; left: -100%; right: 0; bottom: 0;
        background: linear-gradient(90deg, transparent 0%, rgba(0,180,255,0.06) 50%, transparent 100%);
        animation: badge-shimmer 3s linear infinite;
        pointer-events: none;
    }
    .hero-badge { position: relative; overflow: hidden; }
    @keyframes badge-shimmer {
        0% { left: -100%; }
        100% { left: 100%; }
    }

    /* Ticker glow on hover */
    .ticker-item { transition: text-shadow 0.3s; }
    .ticker-item.profit:hover { text-shadow: 0 0 8px rgba(16,185,129,0.4); }

    /* Stat card hover lift */
    .stat-card { transition: transform 0.3s ease, box-shadow 0.3s ease; }
    .stat-card:hover { transform: translateY(-6px); box-shadow: 0 12px 35px rgba(0,60,120,0.1); }
`;
document.head.appendChild(extraCSS);


// ─── Smooth scroll for anchor links ─────────
document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
        e.preventDefault();
        document.querySelector(a.getAttribute('href'))?.scrollIntoView({ behavior: 'smooth' });
    });
});

// ─── Mobile menu ────────────────────────────
const menuBtn = document.getElementById('mobileMenuBtn');
const navLinks = document.querySelector('.nav-links');
if (menuBtn) {
    let open = false;
    menuBtn.addEventListener('click', () => {
        open = !open;
        if (open) {
            navLinks.style.cssText = 'display:flex; flex-direction:column; position:absolute; top:70px; right:24px; background:rgba(255,255,255,0.97); padding:24px; border-radius:16px; border:1px solid rgba(0,120,220,0.1); backdrop-filter:blur(20px); box-shadow:0 8px 30px rgba(0,60,120,0.1); gap:16px;';
        } else {
            navLinks.style.cssText = '';
        }
    });
}
