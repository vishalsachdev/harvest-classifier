<!-- audience: external -->
# Contributing

Issues and pull requests are welcome. This is a small personal project, so
please open an issue before starting anything large.

The project is MIT licensed; by contributing you agree your contribution is
offered under the same terms.

Ground rules that keep the project honest:

* **The safety tests are load bearing.** If a change makes
  `tests/test_safety.py` fail, the change is wrong, not the test. The package
  must not be able to drive a session.
* **New behaviour comes with a test that fails first.** Especially for the
  guard, the allow-list and the policy.
* **The suite stays offline.** No test may need a network connection or an API
  key. Use the fixture transport.
* **No personal or institutional data** in code, tests, fixtures or commit
  messages. The fixtures are invented and should stay that way.
* **Do not add an execute path.** If you want the recommendations wired to
  something, do that in your own code, where you own the consequences.

Run the suite with `.venv/bin/python -m pytest`. It should take under a second.
