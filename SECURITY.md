# Security policy

## Reporting a vulnerability

Vulnerabilities are reported privately through GitHub private vulnerability
reporting: open the repository's **Security** tab and select **Report a
vulnerability**. Public issues are not used for vulnerability reports.

A report includes, where available, the affected port (Python, TypeScript or
Rust) and version, and an input that reproduces the problem.

## Supported versions

Fixes are released for the latest release only. The Python, TypeScript and
Rust packages share one version number and are released together.

## Threat model

The inputs to the library are email bodies (plain text and HTML), which are
untrusted content. The library performs no I/O: it does not read files, open network
connections or execute input. The relevant class of issue is processing time
or memory use that grows faster than the size of the input.
