"""lcm2-pg shared dotted config-key reader (GAP-H2).

Stdlib-only text scan over the LIVE profile's config.yaml. The gateway
tools-exe carries NEITHER PyYAML NOR ruamel (C2RESID2S R1 lesson — a bare
`import yaml` raised inside the gateway and the reader silently returned
nothing), so YAML is only a fast path when it HAPPENS to exist; the text
fallback below is always authoritative-capable.

Scope rule mirrors pgconn.agent_schema(): the live home comes from
hermes_constants.get_hermes_home() (context-local override -> HERMES_HOME
-> platform default) so a routed gateway turn reads ITS OWN profile's
config, never root's. No HOME_ISOLATION=0 root fallback here on purpose:
an engine threshold that fell back to root's value would let a multiplexed
gateway silently apply one profile's number to another.
"""
from __future__ import annotations


def _live_config_path():
    import os
    home = ""
    try:
        from hermes_constants import get_hermes_home

        h = get_hermes_home()
        if h:
            home = str(h)
    except Exception:  # no Hermes on this interpreter: env behaviour
        home = os.environ.get("HERMES_HOME", "")
    if not home:
        return None
    f = os.path.join(os.path.expanduser(home), "config.yaml")
    return f if os.path.isfile(f) else None


def _strip_val(v: str) -> str:
    v = v.split("#", 1)[0].strip().strip("'\"")
    return v


def _get_text(path, keys, depth, want_depth, lines=None):
    """Recursive descent for a nested mapping chain `keys` (len==want_depth).
    A dedent to a lesser indent than a block's items ends the block, so a
    same-named key under a DIFFERENT top-level block can never leak in.
    Inline-flow form (compression: {threshold: 0.5}) is recognized."""
    import re
    if lines is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return None
    key = keys[0]
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent < depth * 2:  # left this block (0,2,4,... canonical)
            break
        if indent != depth * 2:
            continue  # odd indentation: this scanner's canonical layout
        name, sep, val = stripped.partition(":")
        if not sep or name.strip() != key:
            continue
        val = _strip_val(val)
        if depth + 1 == want_depth:
            return val if val else None
        if val.startswith("{"):  # flow map: find the child inside
            m = re.search(r"\b" + re.escape(keys[depth + 1]) + r"\s*:\s*([^,}\\s]+)", val)
            return m.group(1).strip("'\"") if m and depth + 2 == want_depth else None
        # descend: consume the child block (deeper-indented lines)
        j = i
        while j < len(lines):
            r2 = lines[j]
            s2 = r2.strip()
            if not s2 or s2.startswith("#"):
                j += 1
                continue
            if len(r2) - len(r2.lstrip()) <= depth * 2:
                break
            j += 1
        got = _get_text(path, keys[1:], depth + 1, want_depth, lines[i:j])
        if got is not None:
            return got
    return None


def get_config_key(dotted: str):
    """Return the raw string value of a dotted key (e.g.
    'compression.threshold') from the LIVE profile config.yaml, or None
    when unresolvable (missing file/key/malformed). Never raises."""
    path = _live_config_path()
    if not path:
        return None
    keys = [k for k in dotted.split(".") if k]
    if not keys:
        return None
    try:
        import yaml  # fast path only if the host interpreter carries it
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            cfg = yaml.safe_load(fh) or {}
        for k in keys:
            if not isinstance(cfg, dict) or k not in cfg:
                cfg = None
                break
            cfg = cfg[k]
        if cfg is not None:
            if isinstance(cfg, (dict, list, bool)) or cfg is None:
                return None
            return str(cfg)
    except Exception:
        pass
    try:
        return _get_text(path, keys, 0, len(keys))
    except Exception:
        return None
