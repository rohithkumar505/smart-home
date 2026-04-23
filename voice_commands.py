"""
Smart Home voice-style command intents (text / speech-to-text).
Prefix and regex patterns are matched longest-first so specific phrases win.
"""
import re

# --- Wake words & filler (stripped from the start / end before matching) ---

_WAKE_START = (
    'hey home ',
    'hey ',
    'hi ',
    'yo ',
    'hey house ',
    'hey smart home ',
    'hi home ',
    'hi house ',
    'ok home ',
    'okay home ',
    'ok house ',
    'alexa ',
    'hey alexa ',
    'hey google ',
    'ok google ',
    'hey siri ',
    'siri ',
    'computer ',
    'jarvis ',
    'cortana ',
    'listen ',
    'attention ',
    'smart home ',
)

_COURTESY_START = (
    'could you please ',
    'would you please ',
    'will you please ',
    'can you please ',
    'please could you ',
    'please would you ',
    'please can you ',
    'could you ',
    'would you ',
    'will you ',
    'can you ',
    'please ',
    'kindly ',
    'go ahead and ',
    'go ahead ',
    'just ',
    'quickly ',
    'simply ',
    'i need you to ',
    'i want you to ',
    'i need to ',
    'i want to ',
    'i would like to ',
    "i'd like to ",
    'id like to ',
    'help me ',
    'for me ',
)

_TRAIL = (
    ' please',
    ' thanks',
    ' thank you',
    ' now',
    ' right now',
    ' immediately',
    ' asap',
    ' when you can',
    ' if you would',
    ' if you could',
    ' for me',
    ' honey',
    ' dear',
)

_WAKE_SORTED = tuple(sorted(_WAKE_START, key=len, reverse=True))
_COURTESY_SORTED = tuple(sorted(_COURTESY_START, key=len, reverse=True))


def _strip_wake_and_courtesy(n: str) -> str:
    n = n.strip()
    changed = True
    while changed:
        changed = False
        for p in _WAKE_SORTED:
            if n.startswith(p):
                n = n[len(p) :].lstrip()
                changed = True
        for p in _COURTESY_SORTED:
            if n.startswith(p):
                n = n[len(p) :].lstrip()
                changed = True
    for t in _TRAIL:
        if n.endswith(t):
            n = n[: -len(t)].rstrip()
    return n.strip()


# --- ON: phrase must appear at the beginning; remainder is the device target ---

_ON_PREFIXES_RAW = (
    # Ultra-polite / assistant style
    'could you please turn on the ',
    'would you please turn on the ',
    'will you please turn on the ',
    'can you please turn on the ',
    'please go ahead and turn on the ',
    'please be so kind as to turn on the ',
    # Multi-word verbs
    'switch on the power to the ',
    'switch on the power to ',
    'flip on the power to the ',
    'flip on the power to ',
    'bring online the ',
    'bring online ',
    'bring up the ',
    'bring up ',
    'power up the ',
    'power up ',
    'boot up the ',
    'boot up ',
    'ramp up the ',
    'ramp up ',
    'spin up the ',
    'spin up ',
    'crank up the ',
    'crank up ',
    'fire up the ',
    'fire up ',
    'kick on the ',
    'kick on ',
    'pop on the ',
    'pop on ',
    'flick on the ',
    'flick on ',
    'flip on the ',
    'flip on ',
    'enable the ',
    'enable ',
    'activate the ',
    'activate ',
    'energize the ',
    'energize ',
    'illuminate the ',
    'illuminate ',
    'brighten the ',
    'brighten ',
    'light up the ',
    'light up ',
    'wake the ',
    'wake up the ',
    'wake up ',
    'start the ',
    'start up the ',
    'start up ',
    'engage the ',
    'engage ',
    'arm the ',
    'open the circuit for the ',
    'restore power to the ',
    'restore power to ',
    'resume power to the ',
    'resume power to ',
    'put power to the ',
    'put power to ',
    'send power to the ',
    'send power to ',
    'give me the ',
    'give me ',
    'show me the ',
    'show me ',
    'i need the ',
    'i need ',
    'i want the ',
    'i want ',
    'we need the ',
    'we need ',
    'let me have the ',
    'let me have ',
    'let there be ',
    'set the ',
    'spark up the ',
    'spark up ',
    'juice up the ',
    'juice up ',
    'put on the ',
    'put on ',
    'turn on the ',
    'turn on ',
    'switch on the ',
    'switch on ',
    'power on the ',
    'power on ',
    'toggle on the ',
    'toggle on ',
)

_OFF_PREFIXES_RAW = (
    'could you please turn off the ',
    'would you please turn off the ',
    'will you please turn off the ',
    'can you please turn off the ',
    'please go ahead and turn off the ',
    'please be so kind as to turn off the ',
    'switch off the power to the ',
    'switch off the power to ',
    'cut power to the ',
    'cut power to ',
    'kill power to the ',
    'kill power to ',
    'disconnect the ',
    'disconnect ',
    'deactivate the ',
    'deactivate ',
    'disable the ',
    'disable ',
    'shut down the ',
    'shut down ',
    'shut off the ',
    'shut off ',
    'power down the ',
    'power down ',
    'stand down the ',
    'stand down ',
    'silence the ',
    'silence ',
    'dim the ',
    'dim ',
    'blackout the ',
    'blackout ',
    'stop the ',
    'stop ',
    'halt the ',
    'halt ',
    'cut the ',
    'cut ',
    'turn down the ',
    'turn down ',
    'turn off the ',
    'turn off ',
    'switch off the ',
    'switch off ',
    'power off the ',
    'power off ',
    'toggle off the ',
    'toggle off ',
    'flick off the ',
    'flick off ',
    'flip off the ',
    'flip off ',
    'put off the ',
    'put off ',
    'quench the ',
    'quench ',
    'rest the ',
    'rest ',
)

VOICE_ON_PREFIXES = sorted(set(_ON_PREFIXES_RAW), key=lambda x: -len(x))
VOICE_OFF_PREFIXES = sorted(set(_OFF_PREFIXES_RAW), key=lambda x: -len(x))

_ARTICLE = re.compile(r'^(the|a|an)\s+')

# Reject suffix captures that still look like a command (avoid "make the fan on" → wrong target)
_BAD_SUFFIX_INNER = re.compile(
    r'^(make|turn|switch|power|put|set|give|show|please|can|could|would|will|go|help|'
    r'get|take|send|bring|cut|kill|dim|open|start)\b',
    re.I,
)


def _suffix_inner_ok(body: str) -> bool:
    b = (body or '').strip().lower()
    if len(b) < 2:
        return False
    return not _BAD_SUFFIX_INNER.search(b)


# (compiled regex, want_on) — specific patterns before generic "… on / … off" suffix.
_VOICE_REGEX = [
    # "turn the patio fan on" / "switch the kitchen light off"
    (re.compile(r'^turn\s+the\s+(.+?)\s+on\s*$', re.I), True),
    (re.compile(r'^turn\s+the\s+(.+?)\s+off\s*$', re.I), False),
    (re.compile(r'^switch\s+the\s+(.+?)\s+on\s*$', re.I), True),
    (re.compile(r'^switch\s+the\s+(.+?)\s+off\s*$', re.I), False),
    # "make the … brighter / darker / dim" (capture stops before the adjective)
    (re.compile(r'^make\s+(?:the\s+|)(.+?)\s+brighter\s*$', re.I), True),
    (re.compile(r'^make\s+(?:the\s+|)(.+?)\s+darker\s*$', re.I), False),
    (re.compile(r'^make\s+(?:the\s+|)(.+?)\s+dim\s*$', re.I), False),
    # "kill the … power" / "kill the basement lights" (no trailing "power")
    (re.compile(r'^kill\s+(?:the\s+|)(.+?)\s+power\s*$', re.I), False),
    (re.compile(r'^kill\s+the\s+(.+)$', re.I), False),
    # Polite full-sentence forms (before short suffix match)
    (re.compile(r"^i\s*(?:would\s*like|'d\s*like|d\s*like)\s+(?:the\s+|)(.+?)\s+on\s*$", re.I), True),
    (re.compile(r"^i\s*(?:would\s*like|'d\s*like|d\s*like)\s+(?:the\s+|)(.+?)\s+off\s*$", re.I), False),
    (re.compile(r'^i\s*(?:would\s*like|want|need)\s+(?:the\s+|)(.+?)\s+on\s*$', re.I), True),
    (re.compile(r'^i\s*(?:would\s*like|want|need)\s+(?:the\s+|)(.+?)\s+off\s*$', re.I), False),
    # Suffix style: "kitchen fan on" / "patio lights off" (last — most general)
    (re.compile(r'^(.{2,120}?)\s+on\s*$', re.I), True),
    (re.compile(r'^(.{2,120}?)\s+off\s*$', re.I), False),
]


def _article_strip(target: str) -> str:
    t = target.strip()
    return _ARTICLE.sub('', t, count=1).strip()


def parse_voice_intent(normalized_text: str):
    """
    normalized_text: lowercased, punctuation collapsed to spaces (caller provides).
    Returns dict: ok, want_on, target, error
    """
    n = (normalized_text or '').strip()
    if not n:
        return {
            'ok': False,
            'want_on': None,
            'target': '',
            'error': 'Say something like: “Hey home, turn on the kitchen light.”',
        }

    n = _strip_wake_and_courtesy(n)
    if not n:
        return {
            'ok': False,
            'want_on': None,
            'target': '',
            'error': 'Try a device command after the wake phrase.',
        }

    # 1) Longest prefix wins (ON / OFF lists separate)
    for prefix in VOICE_ON_PREFIXES:
        if n.startswith(prefix):
            target = _article_strip(n[len(prefix) :])
            return {'ok': True, 'want_on': True, 'target': target, 'error': None}

    for prefix in VOICE_OFF_PREFIXES:
        if n.startswith(prefix):
            target = _article_strip(n[len(prefix) :])
            return {'ok': True, 'want_on': False, 'target': target, 'error': None}

    # 2) Regex series (fancy natural phrasing)
    for rx, want_on in _VOICE_REGEX:
        m = rx.match(n)
        if not m:
            continue
        inner = (m.group(1) or '').strip()
        if not inner or not _suffix_inner_ok(inner):
            continue
        target = _article_strip(inner)
        if target:
            return {'ok': True, 'want_on': want_on, 'target': target, 'error': None}

    ff = parse_freeform_intent(n)
    if ff['ok']:
        return ff

    return {
        'ok': False,
        'want_on': None,
        'target': '',
        'error': ff.get('error')
        or (
            'Say what to change and how — e.g. “fan off”, “on the kitchen light”, '
            '“turn the AC on”, or “kill the basement lights”.'
        ),
    }


# --- Free-form: ON/OFF words can appear anywhere (natural speech / STT) ---

# (compiled regex, weight) — longer / clearer phrases scored higher. Used with re.search.
_OFF_SIGNALS = [
    (re.compile(r'\bturn\s+off\b', re.I), 12),
    (re.compile(r'\bswitch\s+off\b', re.I), 12),
    (re.compile(r'\bshut\s+off\b', re.I), 12),
    (re.compile(r'\bpower\s+off\b', re.I), 12),
    (re.compile(r'\bpower\s+down\b', re.I), 12),
    (re.compile(r'\bshut\s+down\b', re.I), 10),
    (re.compile(r'\bturn\s+down\b', re.I), 8),
    (re.compile(r'\bdeactivate\b', re.I), 9),
    (re.compile(r'\bdisable\b', re.I), 9),
    (re.compile(r'\bdisconnect\b', re.I), 8),
    (re.compile(r'\bblackout\b', re.I), 9),
    (re.compile(r'\bstand\s+down\b', re.I), 8),
    (re.compile(r'\bkill\b', re.I), 6),
    (re.compile(r'\bstop\b', re.I), 6),
    (re.compile(r'\bhalt\b', re.I), 6),
    (re.compile(r'\bquench\b', re.I), 6),
    (re.compile(r'\bdim\b', re.I), 7),
    (re.compile(r'\boff\b', re.I), 4),
]

_ON_SIGNALS = [
    (re.compile(r'\bturn\s+on\b', re.I), 12),
    (re.compile(r'\bswitch\s+on\b', re.I), 12),
    (re.compile(r'\bpower\s+on\b', re.I), 12),
    (re.compile(r'\bpower\s+up\b', re.I), 12),
    (re.compile(r'\benable\b', re.I), 9),
    (re.compile(r'\bactivate\b', re.I), 9),
    (re.compile(r'\bengage\b', re.I), 7),
    (re.compile(r'\bstart\b', re.I), 7),
    (re.compile(r'\billuminate\b', re.I), 10),
    (re.compile(r'\bbrighten\b', re.I), 9),
    (re.compile(r'\benergize\b', re.I), 9),
    (re.compile(r'\bopen\b', re.I), 5),
    (re.compile(r'\bon\b', re.I), 3),
]

_FREEFORM_HEAD_ON = [
    re.compile(r'^on\s+the\s+(.+)$', re.I),
    re.compile(r'^on\s+my\s+(.+)$', re.I),
    re.compile(r'^get\s+the\s+(.+?)\s+on\b', re.I),
    re.compile(r'^get\s+(.+?)\s+on\b', re.I),
    re.compile(r'^put\s+the\s+(.+?)\s+on\b', re.I),
    re.compile(r'^put\s+(.+?)\s+on\b', re.I),
]

_FREEFORM_HEAD_OFF = [
    re.compile(r'^off\s+the\s+(.+)$', re.I),
    re.compile(r'^off\s+my\s+(.+)$', re.I),
    re.compile(r'^get\s+the\s+(.+?)\s+off\b', re.I),
    re.compile(r'^get\s+(.+?)\s+off\b', re.I),
]

# Remove from residue after scoring (longest pattern strings first via sort key)
_STRIP_FOR_RESIDUE = []


def _build_strip_patterns():
    global _STRIP_FOR_RESIDUE
    if _STRIP_FOR_RESIDUE:
        return
    seen = set()
    for rx, _ in _OFF_SIGNALS + _ON_SIGNALS:
        s = rx.pattern
        if s not in seen:
            seen.add(s)
            _STRIP_FOR_RESIDUE.append(rx)
    _STRIP_FOR_RESIDUE.sort(key=lambda r: -len(r.pattern))


_build_strip_patterns()

_GLUE_WORDS = re.compile(
    r'\b(?:please|thanks|thank\s+you|the|a|an|my|our|your|it|this|that|to|for|me|us|we|'
    r'i|you|ya|gimme|give|get|set|make|put|take|need|want|like|gonna|wanna|'
    r'something|anything|whatever|just|now|right|here|there|again|also|'
    r'can|could|would|will|should|must)\b',
    re.I,
)


def _score_signals(n: str):
    off_s = 0
    on_s = 0
    for rx, w in _OFF_SIGNALS:
        if rx.search(n):
            off_s += w
    for rx, w in _ON_SIGNALS:
        if rx.search(n):
            on_s += w
    return on_s, off_s


def _strip_signals_for_target(n: str) -> str:
    work = n
    for rx in _STRIP_FOR_RESIDUE:
        work = rx.sub(' ', work)
    work = _GLUE_WORDS.sub(' ', work)
    work = re.sub(r'\s+', ' ', work).strip()
    return work


def parse_freeform_intent(n: str):
    """
    Natural speech: ON/OFF can appear anywhere (e.g. "fan off", "get the light on",
    "whatever just kill the AC"). Returns same dict shape as parse_voice_intent.
    """
    if not n:
        return {'ok': False, 'want_on': None, 'target': '', 'error': ''}

    # "on the kitchen light" / "off the fan"
    for rx in _FREEFORM_HEAD_ON:
        m = rx.match(n)
        if m:
            t = _article_strip(m.group(1))
            if t:
                return {'ok': True, 'want_on': True, 'target': t, 'error': None}
    for rx in _FREEFORM_HEAD_OFF:
        m = rx.match(n)
        if m:
            t = _article_strip(m.group(1))
            if t:
                return {'ok': True, 'want_on': False, 'target': t, 'error': None}

    on_s, off_s = _score_signals(n)

    if on_s <= 0 and off_s <= 0:
        return {'ok': False, 'want_on': None, 'target': '', 'error': ''}

    if on_s == off_s and on_s >= 2:
        return {
            'ok': False,
            'want_on': None,
            'target': '',
            'error': 'Say clearly whether to turn something ON or OFF.',
        }

    if off_s > on_s and off_s >= 3:
        want_on = False
    elif on_s > off_s and on_s >= 3:
        want_on = True
    elif off_s >= 4 and off_s >= on_s:
        want_on = False
    elif on_s >= 4 and on_s >= off_s:
        want_on = True
    elif off_s > on_s and off_s >= 2:
        want_on = False
    elif on_s > off_s and on_s >= 2:
        want_on = True
    else:
        return {'ok': False, 'want_on': None, 'target': '', 'error': ''}

    target = _strip_signals_for_target(n)
    target = _article_strip(target)
    if len(target) < 1:
        # e.g. only "off" or "on" — try whole string minus lone on/off tokens
        target = re.sub(r'^(on|off)\s+|\s+(on|off)$', ' ', n, flags=re.I)
        target = _GLUE_WORDS.sub(' ', target)
        target = re.sub(r'\s+', ' ', target).strip()
        target = _article_strip(target)

    if len(target) < 1:
        return {
            'ok': False,
            'want_on': None,
            'target': '',
            'error': 'Add a device name or type (fan, light, AC…) so we know what to control.',
        }

    return {'ok': True, 'want_on': want_on, 'target': target, 'error': None}
