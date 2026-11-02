"""Phase-3 close-out (P3L/P3END): shared-target capture at connect time.

Two fixes, one bug class (silent cross-agent misrouting under concurrent
agents in one process):

1. pgconn.__enter__: snapshot the shared schema THROUGH the env-binding
   funnel (_envget, not os.environ) into self._conn_env_shared.
2. _own_ddl (private kind): bake the cross-agent shared_* views against
   THAT captured target (env default only when nothing was captured),
   so a rival thread's MNEMO2_SHARED_SCHEMA pin can never be baked into
   THIS schema's views.
3. test_r8_concurrency.test_concurrent_shared_surface: the writer threads
   now genuinely overlap (the P3END gate run exposed that the original
   sequencing serialized thread b behind thread a's join — the test was
   structurally unable to observe the race it names).

Verified on this tree: r8 target test 3x green, full mnemo2 suite green.
"""
