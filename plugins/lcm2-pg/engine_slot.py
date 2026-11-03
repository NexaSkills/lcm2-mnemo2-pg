"""X-1 engine-slot precedence resolver (shared rule, single source).

Problem (X-1, warren proof line "Plugin 'lcm-pg' registered context
engine: lcm" on a NEW-stack gateway): when BOTH legacy lcm-pg and new
lcm2-pg plugins are installed, the host's ONE context-engine slot is
filled first-come-first-served by general plugin load order
(hermes_cli/plugins.py:register_context_engine warns and rejects the
second registration). Registration order decides, not intent, and
new-stack gateway sessions silently degrade while :54331 looks healthy
(trade T-2 / warren W-loop / template D-1 = same bug, different hosts).

Fix shape — explicit precedence source resolved at registration:

    CONTEXT_ENGINE=new|old       env marker WINS (pin, highest)
    else context.engine config   "lcm2-pg" -> new ; "lcm" -> old
    else LCM2_PG_DSN set         new-stack configured -> new
    else :54331 socket live      new instance reachable -> new
    else                         old (legacy default-wins = unchanged)

plus a deterministic registration-order inversion on a narrow, audited
route: the precedence WINNER may retire exactly one rival
context-engine registration (a ``kind == "context_engine"`` handle
owned by the rival plugin in the manager's ownership ledger, whose
lease release returns the slot to None), then claim the slot itself.
The loser logs ONE line naming winner + reason. If the host shape
offers no retire route the rule still logs its verdict and order
stands — never silence.

Single-stack hosts are provably untouched: with no rival plugin dir
installed, resolve_engine_slot returns ("", "single-stack") and
enforce_slot() short-circuits without probes or hooks, so both
plugins' register() runs its pre-X-1 body byte-identically.
"""
from __future__ import annotations

import os
import socket

NEW = "new"   # lcm2-pg holds the context-engine slot
OLD = "old"   # legacy lcm-pg holds it

PROBE_HOST_DEFAULT = "~/lcm2-mnemo2-pg/pg/run"
PROBE_PORT_DEFAULT = 54331

_CACHE: dict = {}


def _rival_reservation(manager, rival_key: str):
    """An active reservation record the rival plugin deferred earlier
    (loser-first shape, slot was free then). None if none is pending."""
    ledger = getattr(manager, "_ownership_ledger", None)
    if not isinstance(ledger, dict):
        return None
    for handles in ledger.values():
        for h in list(handles or []):
            try:
                if (str(getattr(h, "plugin_key", "")) == rival_key
                        and getattr(h, "key", "") == "engine-slot"
                        and getattr(h, "active", False)):
                    return h
            except Exception:
                continue
    return None


def _defer_record(manager, side: str, log) -> bool:
    """Loser-first shape: record the deferral on the loser's own ledger
    key. No lease on the real context-engine slot is taken (the loser
    never touches the slot), no engine object exists anywhere from this
    path — it is purely a ledger fact ('this plugin was told to defer
    its engine claim by the X-1 rule').

    Both sides may defer, but ONLY via their own lane's integration:
    lcm2-pg's plugin_entry/context_engine lanes call enforce_slot
    directly; the legacy lcm-pg lane goes through engine_slot_gate.py,
    which ships alongside this module. A legacy-side defer from a lane
    WITHOUT the gate integrated would leave the slot empty (nobody
    left to claim), so gate.py's wrapper — not enforce_slot itself —
    owns that path; enforce_slot trusts its caller.
    """
    try:
        ledger = getattr(manager, "_ownership_ledger", None)
        if not isinstance(ledger, dict):
            return False
        key = f"plugins:{side}"
        for h in ledger.setdefault(key, []):
            if getattr(h, "key", "") == "engine_slot_deferred":
                return True  # already recorded
        ledger[key].append(_DeferralRecord(side))
        return True
    except Exception:
        return False


class _DeferralRecord:
    """Ledger-visible fact: side was told to defer its engine claim."""

    kind = "engine_slot_defer"

    def __init__(self, side):
        self.plugin_key = side
        self.key = "engine_slot_deferred"
        self.active = True

    def dispose(self):
        self.active = False


def _rival_dirs():
    """Base HERMES_HOME dirs the resolver stats under (profile home wins;
    the process-global real home is skipped under HOME_ISOLATION)."""
    homes = [os.environ.get("HERMES_HOME")]
    if not os.environ.get("HOME_ISOLATION"):
        homes.append(os.path.expanduser("~/.hermes"))
    return [d for d in homes if d]


def _rival_installed(other: str) -> bool:
    """Is plugin `other` installed anywhere the host can actually load a
    plugin from? The host scans <base>/plugins/ and, for profiles,
    <base>/profiles/<name>/plugins/ — a plugin dir directly under <base>
    is NEVER loadable, so stats must mirror that discovery layout.
    (A HERMES_HOME ending in /plugins is itself the scan dir.)"""
    bases = list(_rival_dirs())
    for base in list(bases):
        if "/profiles/" in base:
            bases.append(base.rsplit("/profiles/", 1)[0])
    scan_dirs = []
    for base in bases:
        if os.path.basename(base.rstrip("/")) == "plugins":
            scan_dirs.append(base)
        else:
            scan_dirs.append(os.path.join(base, "plugins"))
    prof_root = os.path.join(os.environ.get("HERMES_HOME") or "", "profiles")
    try:
        for p in sorted(os.listdir(prof_root)):
            scan_dirs.append(os.path.join(prof_root, p, "plugins"))
    except OSError:
        pass
    return any(os.path.isdir(os.path.join(d, other)) for d in scan_dirs)


def _marker() -> str:
    v = (os.environ.get("CONTEXT_ENGINE") or "").strip().lower()
    return v if v in (NEW, OLD) else ""


def _config_engine() -> str:
    """context.engine from host config.

    NOT part of the boot rule (reads of global config.yaml are
    profile-dependent and non-deterministic across hosts — the very
    thing X-1 removes). Kept as a diagnostic for engine_slot_status /
    check_all only: which engine the operator's config NAMES, for
    logging a mismatch against the boot verdict."""
    try:
        import yaml
        homes = [os.environ.get("HERMES_HOME")]
        if not os.environ.get("HOME_ISOLATION"):
            homes.append("~/.hermes")
        for home in homes:
            if not home:
                continue
            f = os.path.join(os.path.expanduser(home), "config.yaml")
            if not os.path.isfile(f):
                continue
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                cfg = yaml.safe_load(fh) or {}
            ctx_cfg = cfg.get("context") if isinstance(cfg, dict) else None
            name = (ctx_cfg or {}).get("engine") if isinstance(ctx_cfg, dict) else None
            name = (name or "").strip().lower()
            if name == "lcm2-pg":
                return NEW
            if name == "lcm":
                return OLD
    except Exception:
        pass
    return ""


def _dsn_configured() -> bool:
    return bool((os.environ.get("LCM2_PG_DSN") or "").strip())


def _instance_reachable(timeout: float = 0.35) -> bool:
    host = os.environ.get("LCM2_PG_HOST") or os.path.expanduser(PROBE_HOST_DEFAULT)
    try:
        port = int(os.environ.get("LCM2_PG_PORT") or PROBE_PORT_DEFAULT)
    except ValueError:
        return False
    unixy = host.startswith("/") or ":" in host
    family = socket.AF_UNIX if unixy else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(host if unixy else (host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _env_override():
    """Explicit operator marker (CONTEXT_ENGINE env) — the ONLY
    deterministic precedence source at boot. Host config
    (context.engine) is deliberately NOT consulted here: profile vs
    global config resolution varies per host and reintroduces exactly
    the non-determinism X-1 removes; config is a diagnostic only (see
    _config_engine)."""
    m = _marker()
    if m:
        return m, f"marker:CONTEXT_ENGINE={m}"
    return "", ""


def resolve_engine_slot(side: str, _force: bool = False) -> tuple[str, str]:
    """(winner, reason) for side in {'lcm-pg','lcm2-pg'}. Winner NEW/OLD,
    or "" when no rival plugin is installed (=> pre-X-1 behavior, no
    change). Cached per-process (the slot is a boot-time decision);
    _force re-derives (tests). Never raises: hiccups degrade down the
    rule chain; a failed probe is only ever the negative answer."""
    cached = _CACHE.get("verdict")
    if cached is not None and not _force:
        return cached
    other = "lcm-pg" if side == "lcm2-pg" else "lcm2-pg"
    if not _rival_installed(other):
        verdict = ("", "single-stack")
    else:
        o, why = _env_override()  # explicit operator intent beats auto signals
        if o:
            verdict = (o, why)
        elif _dsn_configured():
            verdict = (NEW, "env:LCM2_PG_DSN")
        else:
            try:
                live = _instance_reachable()
            except Exception:
                live = False
            verdict = (NEW, "probe:new-instance-live") if live \
                else (OLD, "default:legacy")
    _CACHE["verdict"] = verdict
    return verdict


def _rival_slot_handle(manager, rival_key: str):
    """The rival plugin's context_engine registration handle, iff it is
    CURRENTLY the slot occupant (lease-current identity-checked against
    manager._context_engine). None otherwise — we never retire an entry
    that no longer fills the slot, and we never touch a third plugin's
    registration (its lease belongs to another plugin's ledger)."""
    ledger = getattr(manager, "_ownership_ledger", None)
    if not isinstance(ledger, dict):
        return None
    current = getattr(manager, "_context_engine", None)
    if current is None:
        return None
    for handles in ledger.values():
        for h in list(handles or []):
            try:
                if getattr(h, "kind", "") != "context_engine" or not getattr(h, "active", False):
                    continue
                if str(getattr(h, "plugin_key", "")) != rival_key:
                    continue
                lease = _lease_for(h)
                if lease is not None and lease.current is current:
                    return h
            except Exception:
                continue
    return None


def _lease_for(handle):
    """Find the replacement-coordinator lease a handle disposes."""
    rel = getattr(handle, "release", None)
    inner = getattr(rel, "__self__", None)
    if inner is not None and hasattr(inner, "current"):
        return inner
    factory = getattr(rel, "__closure__", None) or ()
    for cell in factory:
        try:
            v = cell.cell_contents
        except (AttributeError, ValueError):
            continue
        if hasattr(v, "current") and hasattr(v, "dispose"):
            return v
        inner = getattr(v, "__self__", None)
        if inner is not None and hasattr(inner, "current"):
            return inner
    return None


def enforce_slot(ctx, side: str, register_self, log) -> str:
    """Call from side's register(). register_self = zero-arg callable
    performing THIS plugin's slot registration (its normal code path).

    Order-independent outcome: the precedence winner ends up holding the
    slot; exactly ONE clear line names the verdict. Returns a diagnostic
    token; never raises.
    """
    try:
        winner, reason = resolve_engine_slot(side)
    except Exception as exc:  # pragma: no cover
        log.warning("%s: engine-slot resolver failed (%s); registration "
                    "order stands", side, exc)
        return "resolver-error"
    if winner == "":
        # Single-stack: the rule is a no-op — run THIS plugin's normal
        # claim path unchanged (must not skip the claim itself).
        try:
            register_self()
        except Exception:
            pass
        return "single-stack"
    mine = (winner == NEW) == (side == "lcm2-pg")
    winner_name = "lcm2-pg" if winner == NEW else "lcm-pg"
    manager = getattr(ctx, "_manager", None)
    current = getattr(manager, "_context_engine", None) if manager is not None else None
    if current is None:
        # slot free. Winner claims normally. A LOSER that arrives FIRST
        # never takes the slot: it records a deferral ledger fact (no
        # lease on the real slot — the engine-slot key's lease belongs to
        # the real registration) and returns. Any later arrival sees a
        # free slot with no rival claim visible and resolves per rule:
        # winner registers, loser defers again (idempotent).
        if mine:
            try:
                register_self()
            except Exception as exc:
                log.warning("%s: slot registration failed: %s", side, exc)
            return "registered"
        _defer_record(manager, side, log)
        log.info("%s: context-engine slot resolved to '%s' (%s); claim "
                 "deferred — loser never takes the slot.", side,
                 winner_name, reason)
        return "reserved-for-winner"
    # slot occupied: ours or the rival's (or a third plugin's)
    rival_key = "lcm-pg" if side == "lcm2-pg" else "lcm2-pg"
    handle = _rival_slot_handle(manager, rival_key) if manager is not None else None
    if handle is None:
        # occupant is not ledger-attributed to the rival (third plugin
        # or ours): normal path — register_self hits the host's
        # one-engine gate. (A rival DEFERRAL record never occupies the
        # slot, so it cannot appear here.)
        try:
            register_self()
        except Exception:
            pass
        if not mine:
            log.info("%s: context-engine slot resolved to '%s' (%s); %s yields.",
                     side, winner_name, reason, side)
            return "yielded"
        return "slot-occupied-foreign"
    if mine:
        try:
            handle.dispose()
        except Exception as exc:
            log.warning("%s: engine-slot precedence (X-1): winner is '%s' "
                        "(%s) but rival retire failed (%s); registration "
                        "order stands", side, side, reason, exc)
            return "retire-failed"
        try:
            register_self()
        except Exception as exc:
            log.warning("%s: slot claim after retire failed: %s", side, exc)
            return "claim-failed"
        log.info("%s: engine-slot precedence (X-1): slot moved from '%s' "
                 "to '%s' — %s", side, rival_key, side, reason)
        return "slot-claimed"
    log.info("%s: context-engine slot resolved to '%s' (%s); %s yields.",
             side, winner_name, reason, side)
    return "yielded"


def name_override():
    """Engine NAME override for the host's own discovery lane
    (agent_init._select_context_engine consults context.engine BEFORE the
    general plugin lane, so an operator who names the LOSING engine gets a
    silently degraded session even with X-1 installed). This resolves the
    slot with the same rule and returns the winner's engine name, or ""
    when the rule is inert (single-stack / resolver hiccup) so the caller
    passes the configured name through verbatim. Cached per-process."""
    try:
        winner, _ = resolve_engine_slot("lcm2-pg")
    except Exception:
        return ""
    return {"new": "lcm2-pg", "old": "lcm"}.get(winner, "")


def slot_name_for(configured: str) -> str:
    """Name the host loader should actually use for `configured` under the
    X-1 rule. Identical to `configured` unless the dual-stack rule names a
    different engine (order-independent, deterministic)."""
    w = name_override()
    return w or configured
