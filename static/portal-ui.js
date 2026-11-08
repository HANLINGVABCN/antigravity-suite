/* Accessible, exclusive dialogs and stable polling updates. */
((root) => {
    const stack = [];
    const returnTargets = new Map();
    let pointerPressed = false;
    let restoreTarget = null;
    const focusSelector = 'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]';
    const visible = element => !element.hidden && element.getClientRects().length > 0;
    const top = () => document.getElementById(stack.at(-1));

    function setVisible(mask, shown) {
        mask.hidden = !shown;
        mask.inert = !shown;
        mask.classList.toggle('active', shown);
        mask.setAttribute('aria-hidden', String(!shown));
    }
    function refreshBackground() {
        const opened = stack.length > 0;
        document.body.classList.toggle('dialog-open', opened);
        const page = document.getElementById('app-page');
        if (page) page.inert = opened;
    }
    function focusDialog(mask) {
        const candidates = [...mask.querySelectorAll(focusSelector)].filter(element => visible(element) && !element.readOnly);
        const element = candidates.find(element => element.matches('input,select,textarea')) ||
            candidates.find(element => !element.classList.contains('dialog-close')) || candidates[0];
        (element || mask.querySelector('.dialog-panel')).focus({preventScroll:true});
    }
    function open(id) {
        const mask = document.getElementById(id);
        if (!mask) throw new Error('找不到弹窗');
        if (!stack.length) restoreTarget = document.activeElement;
        const previous = top();
        if (previous && previous.id !== id) returnTargets.set(id, document.activeElement);
        if (previous) setVisible(previous, false);
        const index = stack.indexOf(id);
        if (index >= 0) stack.splice(index);
        stack.push(id);
        clearError(id);
        setVisible(mask, true);
        mask.querySelector('.dialog-scroll').scrollTop = 0;
        refreshBackground();
        focusDialog(mask);
    }
    function close(id) {
        const index = stack.indexOf(id);
        const mask = document.getElementById(id);
        if (mask) setVisible(mask, false);
        if (index < 0) return;
        const wasTop = index === stack.length - 1;
        stack.splice(index, 1);
        const previous = top();
        if (wasTop && previous) {
            setVisible(previous, true);
            const target = returnTargets.get(id);
            if (target?.isConnected && previous.contains(target)) target.focus({preventScroll:true});
            else focusDialog(previous);
        }
        returnTargets.delete(id);
        refreshBackground();
        if (!stack.length) {
            if (restoreTarget?.isConnected) restoreTarget.focus({preventScroll:true});
            root.renderUI?.();
        }
    }
    function closeAll() {
        for (const mask of document.querySelectorAll('.modal-mask')) setVisible(mask, false);
        stack.length = 0;
        returnTargets.clear();
        refreshBackground();
        if (restoreTarget?.isConnected) restoreTarget.focus({preventScroll:true});
        root.renderUI?.();
    }
    function clearError(id) {
        const error = document.getElementById(id)?.querySelector('.dialog-error');
        if (error) { error.hidden = true; error.textContent = ''; }
    }
    function showError(id, message) {
        const mask = document.getElementById(id);
        const error = mask?.querySelector('.dialog-error');
        if (!error) return;
        error.hidden = false;
        error.textContent = message;
        error.scrollIntoView({block:'nearest'});
    }
    async function dismiss(mask) {
        if (mask.id === 'modal-file-editor') { close(mask.id); return; }
        if (mask.id === 'modal-import-profile' && root.cancelProfileImport) { await root.cancelProfileImport(); return; }
        root.closeModals?.();
    }
    function prepareDialog(mask) {
        const panel = mask.firstElementChild;
        if (!panel || panel.classList.contains('dialog-panel')) return;
        panel.classList.add('dialog-panel'); panel.tabIndex = -1;
        panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
        const title = panel.querySelector('.modal-title,.modal-header');
        const heading = document.createElement('div'); heading.className = 'dialog-heading';
        if (title) {
            title.id ||= mask.id + '-title'; panel.setAttribute('aria-labelledby', title.id);
            title.classList.add('dialog-title'); heading.appendChild(title);
        }
        const closeButton = document.createElement('button');
        closeButton.className = 'dialog-close'; closeButton.type = 'button'; closeButton.textContent = '×';
        closeButton.setAttribute('aria-label', '关闭' + (title?.textContent || '弹窗'));
        closeButton.onclick = () => dismiss(mask).catch(error => showError(mask.id, error.message));
        heading.appendChild(closeButton);
        const last = panel.lastElementChild;
        const footer = last && last.querySelector('button') &&
            (last.classList.contains('modal-footer') || last.style.display === 'flex') ? last : null;
        if (footer) { footer.remove(); footer.classList.add('dialog-actions'); }
        const body = document.createElement('div'); body.className = 'dialog-scroll';
        const error = document.createElement('div'); error.className = 'dialog-error'; error.hidden = true; error.setAttribute('role', 'alert');
        body.appendChild(error);
        while (panel.firstChild) body.appendChild(panel.firstChild);
        panel.append(heading, body);
        if (footer) panel.appendChild(footer);
        mask.onclick = event => {
            if (event.target === mask) dismiss(mask).catch(error => showError(mask.id, error.message));
        };
        setVisible(mask, false);
    }
    function canRenderCards() { return !pointerPressed && stack.length === 0; }
    function updateCards(container, cards, emptyText) {
        const wanted = new Set(cards.map(card => card.id));
        const existing = new Map([...container.children].map(element => [element.dataset.cardId, element]));
        for (const element of [...container.children]) if (!wanted.has(element.dataset.cardId)) element.remove();
        if (!cards.length) {
            if (!container.querySelector('.empty-list')) {
                const message = document.createElement('div'); message.className = 'empty-list'; message.textContent = emptyText;
                container.replaceChildren(message);
            }
            return;
        }
        cards.forEach((card, index) => {
            let element = existing.get(card.id);
            if (!element) { element = document.createElement('div'); element.dataset.cardId = card.id; }
            if (element.dataset.renderedMarkup !== card.html) {
                const focused = element.contains(document.activeElement) ? document.activeElement : null;
                const focusedId = focused?.id, action = focused?.getAttribute('onclick');
                element.innerHTML = card.html;
                element.dataset.renderedMarkup = card.html;
                if (focused) {
                    const replacement = [...element.querySelectorAll(focusSelector)].find(item =>
                        focusedId ? item.id === focusedId : action && item.getAttribute('onclick') === action);
                    replacement?.focus({preventScroll:true});
                }
            }
            element.className = card.className;
            if (container.children[index] !== element) container.insertBefore(element, container.children[index] || null);
        });
    }
    document.addEventListener('keydown', event => {
        const mask = top();
        if (!mask) return;
        if (event.key === 'Escape') { event.preventDefault(); dismiss(mask).catch(error => showError(mask.id, error.message)); return; }
        if (event.key !== 'Tab') return;
        const elements = [...mask.querySelectorAll(focusSelector)].filter(visible);
        if (!elements.length) { event.preventDefault(); return; }
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && (document.activeElement === first || !mask.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !mask.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    });
    document.addEventListener('pointerdown', () => { pointerPressed = true; });
    root.addEventListener('blur', () => { pointerPressed = false; });
    for (const event of ['pointerup', 'pointercancel']) document.addEventListener(event, () => {
        pointerPressed = false;
        // Do not replace the clicked button between pointerup and its click event.
        // Navigation/start/exit handlers must run before delayed card reconciliation.
        setTimeout(() => root.renderUI?.(),0);
    });
    document.querySelectorAll('.modal-mask').forEach(prepareDialog);
    root.PortalUI = {open, close, closeAll, showError, clearError, canRenderCards, updateCards, hasOpenDialog:() => stack.length > 0};
})(window);
