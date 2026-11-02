(() => {
    const root = document.documentElement;
    const sidebar = document.querySelector('#main-sidebar');
    const toggle = document.querySelector('[data-sidebar-toggle]');
    const close = document.querySelector('[data-sidebar-close]');
    const backdrop = document.querySelector('[data-sidebar-backdrop]');
    const desktop = window.matchMedia('(min-width: 1100px)');
    let sidebarOpen = desktop.matches;

    const setSidebar = (open, returnFocus = false) => {
        sidebarOpen = open;
        root.classList.toggle('sidebar-open', !desktop.matches && open);
        root.classList.toggle('sidebar-collapsed', desktop.matches && !open);
        toggle?.setAttribute('aria-expanded', String(open));
        toggle?.setAttribute('aria-label', open ? 'بستن منو' : 'بازکردن منو');
        const arrow = toggle?.querySelector('[data-sidebar-arrow]');
        if (arrow) arrow.textContent = open ? '›' : '‹';
        if (sidebar) sidebar.inert = !open;
        document.body.style.overflow = !desktop.matches && open ? 'hidden' : '';
        if (returnFocus) toggle?.focus();
    };

    if (sidebar && toggle) {
        setSidebar(desktop.matches);
        toggle.addEventListener('click', () => setSidebar(!sidebarOpen));
        close?.addEventListener('click', () => setSidebar(false, true));
        backdrop?.addEventListener('click', () => setSidebar(false, true));
        desktop.addEventListener('change', () => setSidebar(desktop.matches));
    }

    const menus = [...document.querySelectorAll('[data-shell-menu]')];
    menus.forEach((menu) => {
        menu.addEventListener('toggle', () => {
            if (menu.open) menus.forEach((other) => { if (other !== menu) other.open = false; });
        });
    });
    document.addEventListener('click', (event) => {
        menus.forEach((menu) => { if (!menu.contains(event.target)) menu.open = false; });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            const openMenu = menus.find((menu) => menu.open);
            if (openMenu) {
                openMenu.open = false;
                openMenu.querySelector('summary')?.focus();
            } else if (sidebarOpen && sidebar) {
                setSidebar(false, true);
            }
        }
        if (event.key === 'Tab' && sidebarOpen && !desktop.matches && sidebar) {
            const controls = [...sidebar.querySelectorAll('a[href], button:not([disabled])')];
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
                event.preventDefault(); last?.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
                event.preventDefault(); first?.focus();
            }
        }
    });
})();
