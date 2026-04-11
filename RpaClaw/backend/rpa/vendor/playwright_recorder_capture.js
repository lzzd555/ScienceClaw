(() => {
    if (window.__rpa_injected) return;
    window.__rpa_injected = true;
    window.__rpa_paused = false;

    function norm(value) {
        return (value || '').replace(/\s+/g, ' ').trim();
    }

    var INTERACTIVE = ['BUTTON', 'A', 'SELECT', 'TEXTAREA'];
    var INTERACTIVE_ROLES = ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem',
        'option', 'switch', 'combobox'];

    function retarget(el) {
        if (!el || !el.tagName) return null;
        if (['INPUT', 'TEXTAREA', 'SELECT'].indexOf(el.tagName) >= 0) return el;
        if (el.isContentEditable) return el;
        var cur = el;
        while (cur && cur !== document.body) {
            if (INTERACTIVE.indexOf(cur.tagName) >= 0) return cur;
            var role = cur.getAttribute && cur.getAttribute('role');
            if (role && INTERACTIVE_ROLES.indexOf(role) >= 0) return cur;
            cur = cur.parentElement;
        }
        return el;
    }

    function cssEsc(value) {
        try {
            return CSS.escape(value);
        } catch (error) {
            return String(value).replace(/([\\"'\[\](){}|^$.*+?])/g, '\\$1');
        }
    }

    function isGuidLike(id) {
        if (!id || id.length < 8) return false;
        var transitions = 0;
        for (var i = 1; i < id.length; i++) {
            var prev = charType(id[i - 1]);
            var next = charType(id[i]);
            if (prev !== next) transitions++;
        }
        return transitions >= id.length / 4;
    }

    function charType(char) {
        if (char >= 'a' && char <= 'z') return 1;
        if (char >= 'A' && char <= 'Z') return 2;
        if (char >= '0' && char <= '9') return 3;
        return 4;
    }

    function cssFallback(el) {
        var parts = [];
        var cur = el;
        while (cur && cur !== document.body && cur !== document.documentElement) {
            var seg = cur.tagName.toLowerCase();
            if (cur.id && !isGuidLike(cur.id)) {
                parts.unshift('#' + cssEsc(cur.id));
                break;
            }
            if (cur.parentElement) {
                var siblings = cur.parentElement.children;
                var sameTagIndex = 0;
                var sameTagCount = 0;
                for (var i = 0; i < siblings.length; i++) {
                    if (siblings[i].tagName === cur.tagName) {
                        sameTagCount += 1;
                        if (siblings[i] === cur) sameTagIndex = sameTagCount;
                    }
                }
                if (sameTagCount > 1) seg += ':nth-of-type(' + sameTagIndex + ')';
            }
            parts.unshift(seg);
            cur = cur.parentElement;
            if (parts.length >= 4) break;
        }
        return parts.join(' > ');
    }

    function describeFrameElement(frameEl) {
        if (!frameEl) return 'iframe';
        var tag = (frameEl.tagName || 'iframe').toLowerCase();
        var name = frameEl.getAttribute('name');
        if (name) return tag + '[name="' + cssEsc(name) + '"]';
        var title = frameEl.getAttribute('title');
        if (title) return tag + '[title="' + cssEsc(title) + '"]';
        if (frameEl.id && !isGuidLike(frameEl.id)) return tag + '#' + cssEsc(frameEl.id);
        var src = frameEl.getAttribute('src');
        if (src) return tag + '[src="' + cssEsc(src) + '"]';
        return cssFallback(frameEl);
    }

    function getFramePath() {
        var path = [];
        var currentWindow = window;
        try {
            while (currentWindow && currentWindow !== currentWindow.parent) {
                var frameEl = currentWindow === window ? window.frameElement : currentWindow.frameElement;
                if (!frameEl) break;
                path.unshift(describeFrameElement(frameEl));
                currentWindow = currentWindow.parent;
            }
        } catch (error) {
            // Cross-origin parent access can fail; keep the path collected so far.
        }
        return path;
    }

    function buildLocatorBundle(el) {
        var target = retarget(el);
        if (!target) {
            return {
                primary: null,
                candidates: [],
                validation: { status: 'broken', details: 'No target element to generate selector for' }
            };
        }
        if (!window.__rpaPlaywrightRecorder || !window.__rpaPlaywrightRecorder.buildLocatorBundle) {
            return {
                primary: null,
                candidates: [],
                validation: { status: 'broken', details: 'Playwright recorder runtime is unavailable' }
            };
        }
        return window.__rpaPlaywrightRecorder.buildLocatorBundle(target);
    }

    function buildElementSnapshot(el) {
        el = retarget(el);
        if (!el) return {};
        var text = norm(el.textContent || '');
        var role = '';
        var name = '';
        if (window.__rpaPlaywrightRecorder) {
            try {
                role = window.__rpaPlaywrightRecorder.getRole(el) || '';
            } catch (error) {}
            try {
                name = window.__rpaPlaywrightRecorder.getAccessibleName(el) || '';
            } catch (error) {}
        }
        return {
            tag: el.tagName.toLowerCase(),
            role: role,
            name: name,
            text: text.substring(0, 120),
            id: el.id || '',
            classes: (typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean) : []).slice(0, 6),
            type: el.getAttribute('type') || '',
            placeholder: norm(el.getAttribute('placeholder') || ''),
            title: norm(el.getAttribute('title') || ''),
            name_attr: el.getAttribute('name') || ''
        };
    }

    var _lastAction = null;
    var _eventSequence = 0;
    var _activeTarget = null;
    var _activeLocatorBundle = null;

    function rememberActiveTarget(el) {
        if (!el) return null;
        var target = retarget(el);
        if (_activeTarget !== target) {
            _activeTarget = target;
            _activeLocatorBundle = null;
        }
        return _activeTarget;
    }

    function resolveActiveTarget() {
        if (_activeTarget && _activeTarget.isConnected) return _activeTarget;
        if (document.activeElement && document.activeElement !== document.body) {
            return rememberActiveTarget(document.activeElement);
        }
        return null;
    }

    function ensureActiveLocatorBundle(el) {
        var target = el ? rememberActiveTarget(el) : resolveActiveTarget();
        if (!target) return null;
        if (!_activeLocatorBundle) _activeLocatorBundle = buildLocatorBundle(target);
        return _activeLocatorBundle;
    }

    function emit(evt) {
        evt.timestamp = Date.now();
        _eventSequence += 1;
        evt.sequence = _eventSequence;
        evt.url = location.href;
        evt.frame_path = getFramePath();
        _lastAction = { action: evt.action, time: evt.timestamp };
        window.__rpa_emit(JSON.stringify(evt));
    }

    document.addEventListener('click', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        rememberActiveTarget(el);
        if (!el || el.tagName === 'SELECT' || el.tagName === 'OPTION') return;
        var locatorBundle = buildLocatorBundle(el);
        emit({
            action: 'click',
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates,
            validation: locatorBundle.validation,
            element_snapshot: buildElementSnapshot(el),
            tag: retarget(el).tagName
        });
    }, true);

    document.addEventListener('focusin', function(e) {
        if (window.__rpa_paused) return;
        var el = rememberActiveTarget(e.target);
        if (!el) return;
        ensureActiveLocatorBundle(el);
    }, true);

    document.addEventListener('focusout', function(e) {
        if (_activeTarget === e.target) {
            _activeTarget = null;
            _activeLocatorBundle = null;
        }
    }, true);

    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = rememberActiveTarget(e.target);
        if (!el) return;
        var isPassword = el.tagName === 'INPUT' && el.type === 'password';
        var rawValue = typeof el.value === 'string' ? el.value : (el.textContent || '');
        var locatorBundle = ensureActiveLocatorBundle(el);
        if (!locatorBundle) return;
        emit({
            action: 'fill',
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates,
            validation: locatorBundle.validation,
            element_snapshot: buildElementSnapshot(el),
            value: isPassword ? '{{credential}}' : rawValue,
            tag: el.tagName,
            sensitive: isPassword
        });
    }, true);

    document.addEventListener('change', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        var el = e.target;
        if (!el || el.tagName !== 'SELECT') return;
        var locatorBundle = buildLocatorBundle(el);
        emit({
            action: 'select',
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates,
            validation: locatorBundle.validation,
            element_snapshot: buildElementSnapshot(el),
            value: el.value || '',
            tag: el.tagName
        });
    }, true);

    document.addEventListener('keydown', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
        if (e.key === 'Enter') {
            var el = resolveActiveTarget();
            if (!el) return;
            var locatorBundle = ensureActiveLocatorBundle(el);
            if (!locatorBundle) return;
            emit({
                action: 'press',
                locator: locatorBundle.primary,
                locator_candidates: locatorBundle.candidates,
                validation: locatorBundle.validation,
                element_snapshot: buildElementSnapshot(el),
                value: 'Enter',
                tag: el.tagName
            });
        }
    }, true);

    console.log('[RPA] Event capture injected');
})();
