# Security Policy

## Reporting a vulnerability in markitdown-gc

Please do not report security vulnerabilities through public GitHub issues.

Use GitHub private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/greatgc-flow/markitdown-gc/security/advisories/new)). Include the affected version, a minimal reproduction, and the impact. Reports in English or Korean are welcome.

Supported version: the latest release on [PyPI](https://pypi.org/project/markitdown-gc/).

## Upstream

This is an unofficial fork of [microsoft/markitdown](https://github.com/microsoft/markitdown) and is not affiliated with Microsoft. Vulnerabilities that exist only in upstream code are best reported to Microsoft through the [MSRC](https://msrc.microsoft.com/create-report); this fork picks up upstream fixes through its weekly sync.

## Processing untrusted files

MarkItDown reads files with the privileges of the current process. Run it on untrusted input in a sandbox or container, and use the narrowest `convert_*` method you need.
