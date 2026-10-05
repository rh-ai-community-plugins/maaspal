# Contributing

Thank you for your interest in MaaS:PAL, a community plugin for the Red Hat
OpenShift AI Dashboard.

## How to Contribute

1. Fork the repository and create a feature branch from `main`.
2. Make your changes and make sure validation passes (Node 20+, Python 3.11+,
   Helm; install the BFF's dependencies in a virtualenv with `make install`):

   ```bash
   make validate
   ```

3. Changes to scenarios, tasks or the run page: also check them against a live
   cluster (see "Verification" in `CLAUDE.md`), and record what you confirmed in
   `docs/architecture/empirical-verification-checklist.md`.
4. UI changes: use the colour tokens and components in
   [`docs/design/colors-and-components.md`](docs/design/colors-and-components.md)
   (no colour literals — a test enforces it), and check the change in both the
   light and the dark dashboard theme.
5. Adding a scenario that should ship with MaaS:PAL: add its name to
   `BUILTIN_SCENARIOS` in `bff/api/routes/scenarios.py`, or the catalog labels
   it Custom.
6. Submit a pull request with a clear description of the change.

## Reporting Issues

Please use [GitHub Issues](https://github.com/rh-ai-community-plugins/maaspal/issues)
to report bugs or suggest improvements.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
