/**
 * hero-magic.js — Magic UI–style interactions for the dashboard hero:
 * particle field, cursor spotlight, scroll reveals. No React required.
 */
(function () {
    'use strict';

    function prefersReducedMotion() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function initSpotlight(root, spotlightEl) {
        if (!root || !spotlightEl || prefersReducedMotion()) return;
        root.addEventListener(
            'pointermove',
            function (e) {
                var r = root.getBoundingClientRect();
                spotlightEl.style.setProperty('--sx', e.clientX - r.left + 'px');
                spotlightEl.style.setProperty('--sy', e.clientY - r.top + 'px');
            },
            { passive: true }
        );
        root.addEventListener('pointerleave', function () {
            spotlightEl.style.setProperty('--sx', '50%');
            spotlightEl.style.setProperty('--sy', '35%');
        });
    }

    function initParticles(canvas) {
        if (!canvas || prefersReducedMotion()) return null;
        var ctx = canvas.getContext('2d');
        if (!ctx) return null;

        var dots = [];
        var n = 48;
        var w = 0;
        var h = 0;
        var raf = 0;
        var running = true;

        function resize() {
            var p = canvas.parentElement;
            if (!p) return;
            w = p.clientWidth;
            h = p.clientHeight;
            canvas.width = Math.max(1, Math.floor(w * (window.devicePixelRatio || 1)));
            canvas.height = Math.max(1, Math.floor(h * (window.devicePixelRatio || 1)));
            canvas.style.width = w + 'px';
            canvas.style.height = h + 'px';
            ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
        }

        function seed() {
            dots.length = 0;
            for (var i = 0; i < n; i++) {
                dots.push({
                    x: Math.random() * w,
                    y: Math.random() * h,
                    vx: (Math.random() - 0.5) * 0.35,
                    vy: (Math.random() - 0.5) * 0.35,
                    r: Math.random() * 1.8 + 0.4,
                });
            }
        }

        function step() {
            if (!running) return;
            ctx.clearRect(0, 0, w, h);
            var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            var fill = isDark ? 'rgba(165, 180, 252,0.35)' : 'rgba(99,102,241,0.25)';
            var line = isDark ? 'rgba(129,140,248,0.08)' : 'rgba(99,102,241,0.12)';
            var i, j, d, d2, dx, dy, dist;

            for (i = 0; i < dots.length; i++) {
                d = dots[i];
                d.x += d.vx;
                d.y += d.vy;
                if (d.x < 0 || d.x > w) d.vx *= -1;
                if (d.y < 0 || d.y > h) d.vy *= -1;
                d.x = Math.max(0, Math.min(w, d.x));
                d.y = Math.max(0, Math.min(h, d.y));
            }

            for (i = 0; i < dots.length; i++) {
                for (j = i + 1; j < dots.length; j++) {
                    d = dots[i];
                    d2 = dots[j];
                    dx = d.x - d2.x;
                    dy = d.y - d2.y;
                    dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 88) {
                        ctx.beginPath();
                        ctx.strokeStyle = line;
                        ctx.lineWidth = 1;
                        ctx.moveTo(d.x, d.y);
                        ctx.lineTo(d2.x, d2.y);
                        ctx.stroke();
                    }
                }
            }

            for (i = 0; i < dots.length; i++) {
                d = dots[i];
                ctx.beginPath();
                ctx.fillStyle = fill;
                ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
                ctx.fill();
            }

            raf = window.requestAnimationFrame(step);
        }

        resize();
        seed();

        window.addEventListener('resize', function () {
            resize();
            seed();
        });

        raf = window.requestAnimationFrame(step);

        return function () {
            running = false;
            if (raf) window.cancelAnimationFrame(raf);
        };
    }

    function initMagicReveals() {
        var els = document.querySelectorAll('.magic-reveal');
        if (!els.length) return;

        if (prefersReducedMotion()) {
            els.forEach(function (el) {
                el.classList.add('magic-reveal--visible');
            });
            return;
        }

        var io = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (en) {
                    if (en.isIntersecting) {
                        en.target.classList.add('magic-reveal--visible');
                    }
                });
            },
            { root: null, rootMargin: '0px 0px -6% 0px', threshold: 0.08 }
        );

        els.forEach(function (el) {
            io.observe(el);
        });

        requestAnimationFrame(function () {
            els.forEach(function (el) {
                var r = el.getBoundingClientRect();
                var vh = window.innerHeight || 800;
                if (r.top < vh * 0.95 && r.bottom > -40) {
                    el.classList.add('magic-reveal--visible');
                }
            });
        });
    }

    function boot() {
        var stack = document.querySelector('[data-hero-magic]');
        if (!stack) {
            initMagicReveals();
            return;
        }

        var canvas = document.getElementById('heroMagicCanvas');
        var spotlight = document.getElementById('heroMagicSpotlight');
        initSpotlight(stack, spotlight);
        initParticles(canvas);

        initMagicReveals();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
