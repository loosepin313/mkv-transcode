# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Report a vulnerability

Do not open a public issue with the details of a security problem.

Use GitHub private vulnerability reporting:

<https://github.com/loosepin313/mkv-transcode/security/advisories/new>

That page works only after the repository owner enables the feature. If the
page shows an error, use the fallback:

1. Open a normal issue with the title `Security`.
2. Write one line: "I found a security problem. Please open a private
   channel."
3. Do not include the flaw, the exploit, the version, or the file name.

The owner then replies with a private way to send the details, and hides the
public comments.

There is no security email address. GitHub does not accept mail sent to a
`users.noreply.github.com` address, so any such address would bounce.

Include these items once you have a private channel:

- The version, from `mkv-transcode --version`.
- The distribution and the ffmpeg version.
- Whether you run the system service or the user service.
- The smallest change or input that shows the problem.

You get an answer within 14 days. The owner publishes a fix and a security
advisory before any public write-up.

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
