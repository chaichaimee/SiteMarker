# constants.py

TAP_THRESHOLD = 0.4
VIEWPORT_SCAN_RANGE = 50
AUTO_CLICK_RETRY_DELAY = 500
MAX_CLICK_RETRIES = 2

MAX_LOAD_MORE_ATTEMPTS = 100
MAX_NO_GROWTH_ATTEMPTS = 5
LOAD_MORE_TIMEOUT_MS = 800
# Kept small and yielded back to the event loop after every chunk (via
# core.callLater) rather than processed all at once: each paragraph performs
# several COM/UIA round-trips, and a large chunk on a heavy SPA/complex-UIA
# page can easily take longer than NVDA's Watchdog threshold, causing a
# force-terminate or restart.
SCAN_PARAGRAPHS_PER_CHUNK = 10
MAX_SCAN_PARAGRAPHS_TOTAL = 8000
PAGE_DOWN_BATCH_SIZE = 3
SORT_PARAGRAPHS_PER_CHUNK = 20
MAX_SORT_ITERATIONS = 2000
MAX_COMBOBOX_RESUME_ATTEMPTS = 20
# Bounds _primedTreeInterceptors so a long session visiting many documents/SPA
# routes can't grow it without limit; oldest entries are evicted first.
MAX_PRIMED_TREE_INTERCEPTORS = 50
# Buffer priming (_primeDocumentBuffer) intentionally touches only a handful of
# paragraphs now, with a longer per-step delay, instead of the original 60
# paragraphs at a 15ms cadence: each step's COM/UIA round-trips can individually
# take longer than 15ms, so a tight loop of them was able to starve the event
# loop for hundreds of milliseconds and trip the Watchdog. A second full pass is
# no longer scheduled -- one light pass is enough to warm the buffer.
PRIME_DOCUMENT_MAX_PARAGRAPHS = 5
PRIME_DOCUMENT_STEP_DELAY_MS = 50
# Was 20 seconds. That value let the "just had scan activity" flag stay hot long
# after any actual scan/jump/autoclick had already finished, so completely unrelated
# focus events (tabbing into or pasting text in an ordinary edit field elsewhere on
# the page, well after the scan concluded) were being misread as the same kind of
# focus hijack this guard exists to correct, and had their focus yanked back. The
# real race this protects against (a synthetic scroll keystroke landing focus on the
# wrong control) resolves within LOAD_MORE_TIMEOUT_MS. Scan/dispatch completion now
# also clears this proactively; this is just the outer safety ceiling.
FOCUS_RECOVERY_WINDOW_SEC = 1.5

# New: Full-scan retry for auto-click when target not yet present (e.g. YouTube skip ad)
MAX_FULL_SCAN_RETRIES = 5
FULL_SCAN_RETRY_DELAY_MS = 1500



