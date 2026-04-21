from __future__ import annotations


SNAPSHOT_V2_JS = r"""() => {
    const ACTIONABLE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const CONTENT = 'h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,label,span,div,figcaption,caption,time,mark,strong,em,[role=heading],[role=cell],[role=rowheader],[role=columnheader]';
    const recorder = globalThis.__rpaPlaywrightRecorder || null;
    const result = { actionable_nodes: [], content_nodes: [], containers: [], field_groups: [] };
    const containerMap = new Map();
    let actionableIndex = 1;
    let contentIndex = 1;
    let containerIndex = 1;
    let fieldGroupIndex = 1;

    function normalizeText(value, limit) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit || 160);
    }

    function isVisible(el, rect) {
        if (!rect || rect.width <= 0 || rect.height <= 0)
            return false;
        const style = getComputedStyle(el);
        if (!style)
            return false;
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    }

    function centerPoint(rect) {
        return {
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
        };
    }

    function hitTestOk(el, rect) {
        try {
            const point = centerPoint(rect);
            const hit = document.elementFromPoint(point.x, point.y);
            if (!hit)
                return false;
            return hit === el || el.contains(hit) || hit.contains(el);
        } catch (e) {
            return false;
        }
    }

    function bbox(rect) {
        return {
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
        };
    }

    function fallbackRole(el) {
        const explicitRole = el.getAttribute('role');
        if (explicitRole)
            return explicitRole;
        const tag = el.tagName.toLowerCase();
        if (tag === 'button')
            return 'button';
        if (tag === 'select')
            return 'combobox';
        if (tag === 'textarea')
            return 'textbox';
        if (tag === 'input') {
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'checkbox')
                return 'checkbox';
            if (type === 'radio')
                return 'radio';
            if (type === 'button' || type === 'submit')
                return 'button';
            return 'textbox';
        }
        if (tag === 'a' && el.hasAttribute('href'))
            return 'link';
        return '';
    }

    function getRole(el) {
        try {
            return recorder && recorder.getRole ? recorder.getRole(el) || '' : fallbackRole(el);
        } catch (e) {
            return fallbackRole(el);
        }
    }

    function getAccessibleName(el) {
        try {
            if (recorder && recorder.getAccessibleName)
                return normalizeText(recorder.getAccessibleName(el) || '', 160);
        } catch (e) {}
        return normalizeText(el.getAttribute('aria-label') || el.innerText || el.value || '', 160);
    }

    function detectContainerKind(el) {
        if (!el)
            return '';
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        if (tag === 'table' || role === 'table' || role === 'grid')
            return 'table';
        if (tag === 'ul' || tag === 'ol' || role === 'list')
            return 'list';
        if (role === 'toolbar')
            return 'toolbar';
        if (tag === 'form')
            return 'form_section';
        if (tag === 'section')
            return 'form_section';
        if (tag === 'article')
            return 'card_group';
        if (el.classList && el.classList.contains('aui-form-item'))
            return 'form_section';
        if (el.classList && el.classList.contains('ant-form-item'))
            return 'form_section';
        if (el.classList && el.classList.contains('el-form-item'))
            return 'form_section';
        if (el.getAttribute('data-prop'))
            return 'form_section';
        if (el.classList && el.classList.contains('field-panel'))
            return 'form_section';
        if (el.classList && el.classList.contains('field-item'))
            return 'form_section';
        if (el.classList && el.classList.contains('aui-collapse-item__content'))
            return 'form_section';
        return '';
    }

    function detectContainerName(el) {
        if (!el)
            return '';
        const direct = normalizeText(
            el.getAttribute('aria-label') ||
            el.getAttribute('title') ||
            (el.querySelector('caption,h1,h2,h3,h4,legend,[role=heading]') || {}).innerText ||
            '',
            80
        );
        return direct;
    }

    function ensureContainer(el) {
        const containerEl = el.closest('table,[role=table],[role=grid],ul,ol,[role=list],form,[role=toolbar],section,article,.aui-form-item,.ant-form-item,.el-form-item,[data-prop],.field-panel,.field-item,.aui-collapse-item__content');
        if (!containerEl)
            return '';
        if (containerMap.has(containerEl))
            return containerMap.get(containerEl).container_id;
        const rect = containerEl.getBoundingClientRect();
        const container = {
            container_id: 'container-' + containerIndex++,
            frame_path: [],
            container_kind: detectContainerKind(containerEl) || 'container',
            name: detectContainerName(containerEl),
            bbox: bbox(rect),
            summary: normalizeText(containerEl.innerText || '', 120),
            child_actionable_ids: [],
            child_content_ids: [],
            child_field_group_ids: [],
        };
        containerMap.set(containerEl, container);
        result.containers.push(container);
        return container.container_id;
    }

    function buildFallbackLocator(el, role, name, text, placeholder, title) {
        if (role && name) {
            return {
                primary: { method: 'role', role, name },
                candidates: [{ kind: 'role', selected: true, locator: { method: 'role', role, name }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback role candidate' }],
                validation: { status: 'fallback', details: 'fallback role candidate', selected_candidate_index: 0, selected_candidate_kind: 'role' },
            };
        }
        if (placeholder) {
            return {
                primary: { method: 'placeholder', value: placeholder },
                candidates: [{ kind: 'placeholder', selected: true, locator: { method: 'placeholder', value: placeholder }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback placeholder candidate' }],
                validation: { status: 'fallback', details: 'fallback placeholder candidate', selected_candidate_index: 0, selected_candidate_kind: 'placeholder' },
            };
        }
        if (text || name) {
            const value = text || name;
            return {
                primary: { method: 'text', value },
                candidates: [{ kind: 'text', selected: true, locator: { method: 'text', value }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback text candidate' }],
                validation: { status: 'fallback', details: 'fallback text candidate', selected_candidate_index: 0, selected_candidate_kind: 'text' },
            };
        }
        if (title) {
            return {
                primary: { method: 'title', value: title },
                candidates: [{ kind: 'title', selected: true, locator: { method: 'title', value: title }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback title candidate' }],
                validation: { status: 'fallback', details: 'fallback title candidate', selected_candidate_index: 0, selected_candidate_kind: 'title' },
            };
        }
        const tag = el.tagName.toLowerCase();
        return {
            primary: { method: 'css', value: tag },
            candidates: [{ kind: 'css', selected: true, locator: { method: 'css', value: tag }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback css candidate' }],
            validation: { status: 'fallback', details: 'fallback css candidate', selected_candidate_index: 0, selected_candidate_kind: 'css' },
        };
    }

    function buildLocatorBundle(el, role, name, text, placeholder, title) {
        try {
            if (recorder && recorder.buildLocatorBundle) {
                const bundle = recorder.buildLocatorBundle(el);
                if (bundle && bundle.primary)
                    return bundle;
            }
        } catch (e) {}
        return buildFallbackLocator(el, role, name, text, placeholder, title);
    }

    function actionKinds(el, role) {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const actions = new Set();
        if (tag === 'input' || tag === 'textarea' || el.isContentEditable)
            actions.add('fill');
        if (tag === 'select')
            actions.add('select');
        if (!actions.size || role === 'button' || role === 'link' || role === 'checkbox' || role === 'radio')
            actions.add('click');
        if (role === 'textbox')
            actions.add('press');
        if (type === 'checkbox' || type === 'radio') {
            actions.delete('fill');
            actions.add('click');
        }
        return Array.from(actions);
    }

    function semanticKind(el, role) {
        const tag = el.tagName.toLowerCase();
        if (role === 'heading' || /^h[1-6]$/.test(tag))
            return 'heading';
        if (tag === 'td' || role === 'cell')
            return 'cell';
        if (tag === 'th' || role === 'rowheader' || role === 'columnheader')
            return 'header_cell';
        if (tag === 'li')
            return 'item';
        if (tag === 'label')
            return 'label';
        return 'text';
    }

    function isFieldControl(el, role) {
        const tag = el.tagName.toLowerCase();
        if (el.isContentEditable)
            return true;
        if (tag === 'input' || tag === 'textarea' || tag === 'select')
            return true;
        return role === 'textbox' || role === 'combobox' || role === 'checkbox' || role === 'radio';
    }

    function matchByDataProp(el) {
        const formItem = el.closest('.aui-form-item,[data-prop]');
        if (!formItem) return '';
        const dataProp = formItem.getAttribute('data-prop');
        if (!dataProp) return '';
        const label = formItem.querySelector('label[for="' + dataProp + '"]');
        if (label) return normalizeText(label.innerText || label.textContent || '', 80);
        const labelEl = formItem.querySelector('.aui-form-item__label,.ant-form-item-label,.el-form-item__label');
        if (labelEl) return normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
        return '';
    }

    function matchByFormContainer(el) {
        const formItem = el.closest('.aui-form-item,.ant-form-item,.el-form-item,.field-panel,.field-item');
        if (!formItem) return '';
        const labelEl = formItem.querySelector('.aui-form-item__label,.ant-form-item-label,.el-form-item__label,label');
        if (!labelEl) return '';
        const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
        if (!text) return '';
        return text;
    }

    function findValueInContainer(container, controlNode) {
        // Priority 1: display-only content (AUI pattern)
        const displayOnly = container.querySelector('.aui-input-display-only__content');
        if (displayOnly) {
            const text = normalizeText(displayOnly.innerText || displayOnly.textContent || '', 160);
            if (text) return { element: displayOnly, text: text };
        }
        // Priority 2: data-field attribute
        const dataField = container.querySelector('[data-field]');
        if (dataField) {
            const text = normalizeText(dataField.innerText || dataField.textContent || '', 160);
            if (text) return { element: dataField, text: text };
        }
        // Priority 3: Ant Design display text
        const antText = container.querySelector('.ant-form-text');
        if (antText) {
            const text = normalizeText(antText.innerText || antText.textContent || '', 160);
            if (text) return { element: antText, text: text };
        }
        // Priority 4: disabled input value
        const disabledInput = container.querySelector('input[disabled],textarea[disabled]');
        if (disabledInput && disabledInput !== controlNode) {
            const val = normalizeText(disabledInput.value || disabledInput.getAttribute('title') || '', 160);
            if (val) return { element: disabledInput, text: val };
        }
        return null;
    }

    function buildStableLocator(container, valueElement) {
        // Priority 1: data-prop on the container (most stable for AUI)
        const dataProp = container.getAttribute('data-prop');
        if (dataProp) {
            return { method: 'css', value: '[data-prop="' + dataProp + '"]' };
        }
        // Priority 2: data-field on the value element
        if (valueElement) {
            const dataField = valueElement.getAttribute('data-field');
            if (dataField) {
                return { method: 'css', value: '[data-field="' + dataField + '"]' };
            }
        }
        // Priority 3: use the element's existing locator (will be built by buildFallbackLocator)
        return null;
    }

    function fieldNameFromElement(el, role) {
        // Priority 1: el.labels (standard <label for="id">)
        const labelTexts = [];
        try {
            if (el.labels) {
                for (const labelEl of Array.from(el.labels)) {
                    const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
                    if (text)
                        labelTexts.push(text);
                }
            }
        } catch (e) {}
        // Priority 2: aria-label
        const ariaLabel = normalizeText(el.getAttribute('aria-label') || '', 80);
        if (ariaLabel)
            return ariaLabel;
        // Priority 3: aria-labelledby
        const ariaLabelledBy = normalizeText(el.getAttribute('aria-labelledby') || '', 80);
        if (ariaLabelledBy) {
            const parts = [];
            for (const id of ariaLabelledBy.split(/\s+/)) {
                if (!id)
                    continue;
                const labelEl = document.getElementById(id);
                if (!labelEl)
                    continue;
                const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
                if (text)
                    parts.push(text);
            }
            if (parts.length)
                return normalizeText(parts.join(' '), 80);
        }
        if (labelTexts.length)
            return labelTexts[0];
        // Priority 4: Framework container lookup
        const dataPropMatch = matchByDataProp(el);
        if (dataPropMatch)
            return dataPropMatch;
        const formContainerMatch = matchByFormContainer(el);
        if (formContainerMatch)
            return formContainerMatch;
        // Priority 5: placeholder
        const placeholder = normalizeText(el.getAttribute('placeholder') || '', 80);
        if (placeholder)
            return placeholder;
        // Priority 6: title
        const title = normalizeText(el.getAttribute('title') || '', 80);
        if (title)
            return title;
        // Priority 7: getAccessibleName
        const name = getAccessibleName(el);
        if (name)
            return name;
        // Priority 8: role fallback
        if (role)
            return normalizeText(role, 80);
        return '';
    }

    function fieldNameFromNode(node) {
        return normalizeText(
            node.name ||
            node.field_name ||
            node.placeholder ||
            node.title ||
            node.text ||
            node.role ||
            '',
            80
        );
    }

    function fieldGroupKey(group) {
        return [
            group.container_id || '',
            normalizeText(group.field_name || '', 80),
        ].join('|');
    }

    function fieldGroupPriority(group) {
        let score = 0;
        if (group.field_node_id)
            score += 8;
        if (group.value_node_id)
            score += 4;
        if (group.extraction_kind === 'control_state')
            score += 2;
        if (group.locator && group.locator.method)
            score += 1;
        return score;
    }

    function fieldValueNodeCandidates(fieldName, containerId, controlNode) {
        const normalizedFieldName = normalizeText(fieldName || '', 80);
        const candidates = result.content_nodes.filter(node => node.container_id === containerId);
        const matches = [];
        for (const node of candidates) {
            const nodeFieldName = normalizeText(node.field_name || '', 80);
            if (!nodeFieldName || !normalizedFieldName)
                continue;
            if (nodeFieldName === normalizedFieldName || nodeFieldName.includes(normalizedFieldName) || normalizedFieldName.includes(nodeFieldName)) {
                if (node.node_id !== controlNode.node_id)
                    matches.push(node);
            }
        }
        if (matches.length)
            return matches[0];

        let nearest = null;
        let nearestScore = Infinity;
        const controlRect = controlNode.bbox || {};
        for (const node of candidates) {
            if (node.node_id === controlNode.node_id)
                continue;
            if (!node.text)
                continue;
            const nodeText = normalizeText(node.text || '', 80);
            if (!nodeText || nodeText === normalizedFieldName)
                continue;
            const rect = node.bbox || {};
            const dx = Math.abs((rect.x || 0) - (controlRect.x || 0));
            const dy = Math.abs((rect.y || 0) - (controlRect.y || 0));
            const score = dy * 2 + dx;
            if (dy <= 120 && dx <= 500 && score < nearestScore) {
                nearest = node;
                nearestScore = score;
            }
        }
        return nearest;
    }

    function addFieldGroup(group) {
        if (!group || !group.field_name)
            return;
        const key = fieldGroupKey(group);
        if (!addFieldGroup.byKey)
            addFieldGroup.byKey = new Map();
        const existing = addFieldGroup.byKey.get(key);
        let storedGroup = group;
        if (existing) {
            if (fieldGroupPriority(group) < fieldGroupPriority(existing))
                return;
            const field_group_id = existing.field_group_id;
            Object.assign(existing, group);
            existing.field_group_id = field_group_id;
            addFieldGroup.byKey.set(key, existing);
            storedGroup = existing;
        } else {
            group.field_group_id = 'field-group-' + fieldGroupIndex++;
            result.field_groups.push(group);
            addFieldGroup.byKey.set(key, group);
        }
        if (storedGroup.container_id) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === group.container_id);
            if (container)
                container.child_field_group_ids = Array.from(new Set([...(container.child_field_group_ids || []), storedGroup.field_group_id].filter(Boolean)));
        }
    }

    const totalActionable = document.querySelectorAll(ACTIONABLE).length;
    const totalContent = document.querySelectorAll(CONTENT).length;
    const ACTIONABLE_CAP = Math.min(totalActionable, 300);
    const CONTENT_CAP = Math.min(totalContent, 400);
    const actionableSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(ACTIONABLE))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        if (el.disabled)
            continue;
        const role = getRole(el);
        const name = getAccessibleName(el);
        const text = normalizeText(el.innerText || '', 160);
        const placeholder = normalizeText(el.getAttribute('placeholder') || '', 80);
        const title = normalizeText(el.getAttribute('title') || '', 80);
        const key = [role, name, placeholder, title, bbox(rect).x, bbox(rect).y].join('|');
        if (actionableSeen.has(key))
            continue;
        actionableSeen.add(key);
        const containerId = ensureContainer(el);
        const locatorBundle = buildLocatorBundle(el, role, name, text, placeholder, title);
        const node = {
            node_id: 'actionable-' + actionableIndex++,
            frame_path: [],
            container_id: containerId,
            tag: el.tagName.toLowerCase(),
            role,
            name,
            text,
            type: normalizeText(el.getAttribute('type') || '', 40),
            placeholder,
            title,
            bbox: bbox(rect),
            center_point: centerPoint(rect),
            is_visible: true,
            is_enabled: !el.disabled,
            hit_test_ok: hitTestOk(el, rect),
            action_kinds: actionKinds(el, role),
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates || [],
            validation: locatorBundle.validation || { status: 'fallback', details: 'locator bundle unavailable' },
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                role,
                name,
                href: normalizeText(el.getAttribute('href') || '', 120),
            },
        };
        result.actionable_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_actionable_ids.push(node.node_id);
        }
        if (result.actionable_nodes.length >= ACTIONABLE_CAP)
            break;
    }

    const contentSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(CONTENT))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        const text = normalizeText(el.innerText || '', 200);
        if (!text)
            continue;
        const key = [text, bbox(rect).x, bbox(rect).y].join('|');
        if (contentSeen.has(key))
            continue;
        // Deduplicate span/div text already captured by a heading or paragraph
        const ctag = el.tagName.toLowerCase();
        if (ctag === 'span' || ctag === 'div') {
            const isDupText = result.content_nodes.some(n => n.text === text);
            if (isDupText) continue;
        }
        contentSeen.add(key);
        const role = getRole(el);
        const containerId = ensureContainer(el);
        const node = {
            node_id: 'content-' + contentIndex++,
            frame_path: [],
            container_id: containerId,
            semantic_kind: semanticKind(el, role),
            role,
            text,
            bbox: bbox(rect),
            locator: buildFallbackLocator(el, role, '', text, '', '').primary,
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
            },
        };
        // Detect field value pattern: annotate value nodes with their field label
        if (ctag === 'span' || ctag === 'div') {
            const dataField = el.getAttribute('data-field');
            const hasValueClass = /value/i.test(el.className || '');
            if (dataField || hasValueClass) {
                let fieldName = '';
                const prevSib = el.previousElementSibling;
                if (prevSib) {
                    fieldName = normalizeText(prevSib.innerText || prevSib.textContent || '', 80);
                }
                if (!fieldName) {
                    const parent = el.parentElement;
                    if (parent) {
                        const labelEl = parent.querySelector('.field-label, [class*="label"]');
                        if (labelEl && labelEl !== el) {
                            fieldName = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
                        }
                    }
                }
                if (fieldName && fieldName !== text) {
                    node.field_name = fieldName;
                }
                // Use a specific CSS locator for data-field elements to avoid
                // strict-mode violations when the same text appears elsewhere.
                if (dataField) {
                    node.locator = { method: 'css', value: '[data-field="' + dataField + '"]' };
                }
            }
        }
        result.content_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_content_ids.push(node.node_id);
        }
        if (result.content_nodes.length >= CONTENT_CAP)
            break;
    }

    for (const node of result.content_nodes) {
        if (!node.field_name)
            continue;
        addFieldGroup({
            frame_path: [],
            container_id: node.container_id,
            container_kind: (Array.from(containerMap.values()).find(item => item.container_id === node.container_id) || {}).container_kind || '',
            field_name: node.field_name,
            field_node_id: null,
            value_node_id: node.node_id,
            label_node_id: null,
            bbox: node.bbox,
            locator: node.locator,
            value_locator: node.locator,
            locator_candidates: [{ kind: 'text', selected: true, locator: node.locator }],
            selected_locator_kind: 'content_nodes',
            extraction_kind: 'text',
            allow_empty_fallback: true,
            fallback_locator: node.locator,
            fallback_frame_path: [],
        });
    }

    for (const node of result.actionable_nodes) {
        const tag = (node.tag || '').toLowerCase();
        if (!(tag === 'input' || tag === 'textarea' || tag === 'select' || node.role === 'textbox' || node.role === 'combobox' || node.role === 'checkbox' || node.role === 'radio' || node.type === 'contenteditable'))
            continue;
        const fieldName = fieldNameFromNode(node);
        if (!fieldName)
            continue;
        // Try to find a value node using existing name-based matching
        let valueNode = fieldValueNodeCandidates(fieldName, node.container_id, node);
        let stableLocator = null;
        // Look up the DOM element for the container (key in containerMap is the DOM element)
        const containerEntry = Array.from(containerMap.entries())
            .find(([domEl, cObj]) => cObj.container_id === node.container_id);
        const containerDomEl = containerEntry ? containerEntry[0] : null;
        // If no value node found by name, try container-based value search
        if (!valueNode && containerDomEl) {
            try {
                const found = findValueInContainer(containerDomEl, null);
                if (found) {
                    valueNode = {
                        node_id: 'content-derived-' + fieldGroupIndex,
                        text: found.text,
                        bbox: found.element.getBoundingClientRect ? bbox(found.element.getBoundingClientRect()) : node.bbox,
                        locator: buildStableLocator(containerDomEl, found.element) || { method: 'text', value: found.text },
                        locator_candidates: [],
                        container_id: node.container_id,
                    };
                }
            } catch (e) {}
        }
        // Build stable value_locator from container
        if (containerDomEl) {
            try {
                stableLocator = buildStableLocator(containerDomEl, null);
            } catch (e) {}
        }
        const containerObj = containerEntry ? containerEntry[1] : {};
        const controlExtractionKind = node.role === 'checkbox' || node.role === 'radio' ? 'control_state' : 'control_value';
        addFieldGroup({
            frame_path: [],
            container_id: node.container_id,
            container_kind: containerObj.container_kind || '',
            field_name: fieldName,
            field_control_kind: node.role || node.type || tag,
            field_node_id: node.node_id,
            value_node_id: valueNode ? valueNode.node_id : null,
            label_node_id: null,
            bbox: valueNode ? valueNode.bbox : node.bbox,
            locator: valueNode ? valueNode.locator : (stableLocator || node.locator),
            value_locator: stableLocator || node.locator,
            locator_candidates: valueNode ? (valueNode.locator_candidates || []) : (node.locator_candidates || []),
            selected_locator_kind: valueNode ? 'content_nodes' : 'actionable_nodes',
            extraction_kind: controlExtractionKind,
            allow_empty_fallback: false,
            fallback_locator: valueNode ? valueNode.locator : node.locator,
            fallback_frame_path: [],
        });
    }

    return JSON.stringify(result);
}"""
