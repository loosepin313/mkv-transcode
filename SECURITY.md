# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Report a vulnerability

Do not open a public issue for a security problem. Use GitHub private
vulnerability reporting on the repository page:

**Security** tab, then **Report a vulnerability**.

Include these items:

- The version, from `mkv-transcode --version`.
- The distribution and the ffmpeg version.
- Whether you run the system service or the user service.
- The smallest change or input that shows the problem.

You get an answer within 14 days. I publish a fix and a security advisory
before any public write-up.

## Trust model

This section states what the tool trusts. It helps you judge whether a finding
is a real problem.

The tool has no network listener. It opens no port and runs no web server. It
makes one kind of outbound request: HTTPS to `datasets.imdbws.com`.

The tool trusts three things:

1. The config file. Every value in it is a command line fragment or a path.
2. The command line arguments.
3. The contents of `paths.incoming`.

Anyone who can write to `paths.incoming` can already make the tool run ffmpeg
as the service user. Treat `incoming` as trusted storage. Do not export it over
Samba or NFS to people you do not trust.

The tool does not follow symbolic links in `incoming`. It skips them in the
scanner and again in the pipeline. A link cannot point the pipeline at a file
outside `incoming`.

Output names pass through `sanitize_component`. A hostile file name cannot
write outside `paths.transcoded`.

Job logs and JSON reports are owner-only, mode `0600`.

## What the tool does not do

- It does not run a shell. Every ffmpeg call passes an argument list.
- It does not use `eval`, `exec`, `pickle`, or a YAML loader.
- It has no third-party runtime dependencies.
- It does not upload your file names. The IMDb index is local.
