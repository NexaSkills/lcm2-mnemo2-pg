# Makes tests/ a package so each suite's test modules import as
# tests.lcm2.* / tests.mnemo2.* — no sys.path shadowing between the two
# suites when they share one pytest process (qa3 N-3), and `import
# tests.mnemo2.conftest` resolves to OUR conftest deterministically.
