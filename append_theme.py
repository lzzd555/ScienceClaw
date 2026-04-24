with open('RpaClaw/frontend/src/assets/theme.css', 'a') as f:
    f.write('''

/* Sky theme for api monitor pages */
.api-monitor-teal {
    --border-input-active: #38bdf8; /* sky-400 */
    --text-brand: #38bdf8; /* sky-400 */
    accent-color: #38bdf8; /* sky-400 */
}

.api-monitor-teal input[type="text"],
.api-monitor-teal input[type="textarea"],
.api-monitor-teal input[type="number"],
.api-monitor-teal textarea,
.api-monitor-teal select {
    caret-color: #38bdf8; /* sky-400 */
}

.api-monitor-teal input[type="text"]::selection,
.api-monitor-teal input[type="number"]::selection,
.api-monitor-teal textarea::selection {
    background-color: rgba(56, 189, 248, 0.3); /* sky-400 with opacity */
}

.api-monitor-teal input[type="text"]::-moz-selection,
.api-monitor-teal input[type="number"]::-moz-selection,
.api-monitor-teal textarea::-moz-selection {
    background-color: rgba(56, 189, 248, 0.3); /* sky-400 with opacity */
}

/* Ensure consistent sky focus ring for all inputs */
.api-monitor-teal input:focus,
.api-monitor-teal textarea:focus,
.api-monitor-teal select:focus {
    --tw-ring-color: rgba(56, 189, 248, 0.3); /* sky-400/30 */
}
''')
