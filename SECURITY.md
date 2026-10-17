# Security policy

agentplay reads sensitive local data: your coding agents' transcripts. We treat three kinds of bug as security issues:

- **Redaction bugs:** any path by which a map, a log line, the local API or the viewer exposes prompt text, model output, tool input or output, file contents, command arguments or credentials.
- **Local server issues:** the viewer server is meant to bind only to `127.0.0.1` and serve read-only data. Anything that lets another origin or another machine read it, or lets a request write data, is a security issue.
- **Anything leaving the machine** beyond what the README's privacy section lists for `agentplay review`.

## Reporting

Please **do not open a public issue** for these. Report privately through GitHub: on the repository page, open **Security → Report a vulnerability**. Include:

- what you found and how to reproduce it (a small synthetic transcript is ideal);
- the version (`agentplay --version`) and your OS;
- whether you would like to be credited.

If you cannot use GitHub, email the fallback contact: [info@entelligence.ai](mailto:info@entelligence.ai)

We will acknowledge your report within 3 business days and aim to ship a fix within 30 days, sooner for anything that exposes content. We will tell you when it is fixed and credit you in the release notes if you want.

## Supported versions

Security fixes go into the latest release. The project is pre-1.0, so please upgrade rather than expect backports.
