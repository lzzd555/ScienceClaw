import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_JS_PATH = REPO_ROOT / "RpaClaw" / "backend" / "rpa" / "vendor" / "playwright_recorder_capture.js"


def _run_capture_probe(scenario: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("node is required to validate recorder capture JavaScript")

    probe = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');

        function makeElement(options = {{}}) {{
          const attributes = options.attributes || {{}};
          const element = {{
            tagName: options.tagName || 'BUTTON',
            className: options.className || '',
            id: options.id || '',
            textContent: options.textContent || '',
            parentElement: options.parentElement || null,
            nextElementSibling: options.nextElementSibling || null,
            firstElementChild: options.firstElementChild || null,
            children: options.children || [],
            isContentEditable: false,
            contains(other) {{
              return other === this || this.children.includes(other);
            }},
            getAttribute(name) {{
              return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
            }},
            hasAttribute(name) {{
              return Object.prototype.hasOwnProperty.call(attributes, name);
            }},
            closest(selector) {{
              if (options.closest && Object.prototype.hasOwnProperty.call(options.closest, selector)) {{
                return options.closest[selector];
              }}
              return null;
            }},
          }};
          for (const child of element.children) child.parentElement = element;
          return element;
        }}

        let installedOptions = null;
        const emitted = [];
        const context = {{
          console,
          CSS: {{ escape: value => String(value) }},
          location: {{ href: 'https://example.test/login' }},
          document: {{}},
          window: {{
            __rpa_tab_id: 'tab-1',
            __rpa_emit: payload => emitted.push(JSON.parse(payload)),
            __rpaPlaywrightRecorder: {{
              buildLocatorBundle(element) {{
                return {{
                  primary: {{
                    method: 'role',
                    role: (element.getAttribute('role') || element.tagName.toLowerCase()),
                    name: element.textContent || '',
                  }},
                  candidates: [],
                  validation: {{ status: 'ok' }},
                }};
              }},
              getRole(element) {{
                return element.getAttribute('role') || element.tagName.toLowerCase();
              }},
              getAccessibleName(element) {{
                return element.textContent || '';
              }},
            }},
            __rpaPlaywrightActions: {{
              install(options) {{
                installedOptions = options;
              }},
            }},
          }},
        }};
        context.globalThis = context.window;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(CAPTURE_JS_PATH))}, 'utf8'), context);

        try {{
          const scenario = {json.dumps(scenario)};
          if (scenario === 'plain-button-click') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Login',
              attributes: {{ role: 'button' }},
            }});
            installedOptions.emitAction('click', button, {{}});
          }} else if (scenario === 'menu-item-click') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const item = makeElement({{
              tagName: 'A',
              textContent: 'Export CSV',
              attributes: {{ role: 'menuitem' }},
              closest: {{ '[role="menu"], [role="listbox"]': menu }},
            }});
            installedOptions.emitAction('click', item, {{}});
          }} else if (scenario === 'hover-menu-trigger') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Export',
              attributes: {{ role: 'button', 'aria-haspopup': 'menu' }},
              nextElementSibling: menu,
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'hover-list-popup-trigger') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Download',
              attributes: {{ role: 'button', 'aria-haspopup': 'list' }},
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'plain-button-hover') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Login',
              attributes: {{ role: 'button' }},
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'menu-item-hover') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const item = makeElement({{
              tagName: 'A',
              textContent: 'Export CSV',
              attributes: {{ role: 'menuitem' }},
              closest: {{ '[role="menu"], [role="listbox"]': menu }},
            }});
            installedOptions.emitAction('hover', item, {{}});
          }}
          process.stdout.write(JSON.stringify({{ ok: true, emitted }}));
        }} catch (error) {{
          process.stdout.write(JSON.stringify({{
            ok: false,
            name: error.name,
            message: error.message,
            stack: String(error.stack || ''),
          }}));
          process.exitCode = 1;
        }}
        """
    )
    completed = subprocess.run(
        ["node", "-e", probe],
        check=False,
        text=True,
        capture_output=True,
    )
    output = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    assert output, completed.stderr
    payload = json.loads(output)
    assert completed.returncode == 0, payload
    assert payload["ok"] is True
    return payload


def test_plain_button_click_does_not_throw_or_get_menu_context():
    payload = _run_capture_probe("plain-button-click")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    assert event["locator"]["name"] == "Login"
    assert "menu_context" not in event.get("signals", {})


def test_menu_item_click_keeps_menu_context_signal():
    payload = _run_capture_probe("menu-item-click")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    assert event["signals"]["menu_context"]["is_menu_item"] is True


def test_hover_menu_trigger_keeps_trigger_signal():
    payload = _run_capture_probe("hover-menu-trigger")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["hover"]["is_menu_trigger_candidate"] is True


def test_hover_list_popup_trigger_keeps_trigger_signal():
    payload = _run_capture_probe("hover-list-popup-trigger")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["hover"]["is_menu_trigger_candidate"] is True


def test_plain_button_hover_does_not_get_trigger_signal():
    payload = _run_capture_probe("plain-button-hover")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert "hover" not in event.get("signals", {})


def test_menu_item_hover_gets_menu_context_signal():
    payload = _run_capture_probe("menu-item-hover")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["menu_context"]["is_menu_item"] is True
