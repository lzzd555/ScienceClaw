import re
with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'r', encoding='utf-8') as f:
    content = f.read()

# Look for hardcoded background or text colors that weren't converted properly
matches = re.findall(r'#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})\b', content)
print(f"Found {len(matches)} potential hex colors:")
print(set(matches))
