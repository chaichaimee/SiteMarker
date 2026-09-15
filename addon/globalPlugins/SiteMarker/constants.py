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

# _isBufferReady fast path: a document whose rendered text already comfortably
# exceeds this length is treated as loaded without waiting for a second poll.
# Short of this, readiness instead falls back to a two-poll stability check
# (see _bufferReadySamples), since some real pages -- a minimal search-engine
# homepage, for instance -- never reach this length at all even fully loaded.
BUFFER_READY_MIN_LENGTH = 500

# ------------------------------------------------------------------
# Customizable jump/auto-click shortcuts (see customShortcuts.py)
# ------------------------------------------------------------------
# Only "j" (jump) and "alt+j" (auto-click) remain as built-in default
# physical bindings. Every other key (what used to be the f/d/z and
# alt+c/x/z defaults) is no longer pre-registered by the add-on at all --
# those are now ordinary per-marker custom shortcuts, assigned individually
# per marker via Marker Properties' "Custom Shortcut..." button and bound
# dynamically by GlobalPlugin._syncCustomMarkerGestureBindings(). This keeps
# exactly one script (and one physical key) reserved by default for each
# action type, so nothing is taken from NVDA's own single-letter browse-mode
# navigation (F, D, Z, etc.) unless the user explicitly assigns it.
DEFAULT_CUSTOMIZABLE_GESTURES = {
	"siteMarker_jump_j": "kb:j",
	"siteMarker_jump_j_back": "kb:shift+j",
	"siteMarker_autoClick_j": "kb:alt+j",
}

# Display order and label for each customizable action in the Custom
# Shortcuts dialog. Kept separate from DEFAULT_CUSTOMIZABLE_GESTURES (rather
# than merged into one structure) since the label text needs to go through
# _translate() at call time, not at module import time.
CUSTOMIZABLE_ACTION_ORDER = list(DEFAULT_CUSTOMIZABLE_GESTURES.keys())

# Hard cap on consecutive blank line/paragraph skips for the opt-in
# skipBlankLines site setting, so a document that is blank all the way to
# its boundary can't turn one keypress into an unbounded loop.
MAX_BLANK_LINE_SKIP_STEPS = 500

# Auto-trigger-on-page-load (Marker Properties' "Auto-jump/Auto-click on page
# load" checkbox): how long to wait between buffer-readiness polls, and how
# many attempts before trying anyway on a best-effort basis, mirroring the
# existing jump/auto-click retry gate in _handleGlobalJump/_handleAutoClick
# but with a much larger total budget -- this runs unattended in the
# background rather than during an interactive keypress, and heavy SPA feeds
# (observed on Facebook) can take several seconds longer to render meaningful
# content than the 2-second budget used for an interactive press is willing
# to make a user wait through.
AUTO_TRIGGER_RETRY_DELAY_MS = 500
AUTO_TRIGGER_MAX_RETRIES = 15

# Fallback treeInterceptor-change poll (see
# ContextHelpersMixin._pollForTreeInterceptorChange): confirmed via log
# evidence that event_treeInterceptor_gainFocus never reaches this add-on in
# some environments (most likely another loaded global plugin breaking the
# event chain before it gets here), and that in-page navigation that doesn't
# also change the foreground window (e.g. following a bookmark while already
# in the browser) never fires event_foreground either. A low-frequency poll
# is the fallback that doesn't depend on any single event's propagation
# actually reaching this add-on. 1 second is frequent enough that a fresh
# page's auto-trigger/priming isn't meaningfully delayed, without being a
# hot enough path to raise the log-volume/responsiveness concerns that apply
# to per-keystroke or per-focus-change code (5.16).
TREE_INTERCEPTOR_POLL_INTERVAL_MS = 1000

# The only keystroke offered as a fixed default in Marker Properties' key
# combo box. A marker whose "keystroke" value is anything else (including
# what used to be the standard f/d/z / alt+c/x/z choices) is a per-marker
# custom shortcut (see customShortcuts.py /
# GlobalPlugin._syncCustomMarkerGestureBindings) rather than a shared
# default layer -- kept as single-item sets (not bare strings) so the
# "in STANDARD_..._KEYS" membership checks used throughout the codebase
# don't need special-casing.
STANDARD_JUMP_KEYS = frozenset({"j"})
STANDARD_AUTOCLICK_KEYS = frozenset({"alt+j"})





