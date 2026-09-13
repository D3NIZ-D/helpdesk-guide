# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report it privately through GitHub Security Advisories:

<https://github.com/D3NIZ-D/helpdesk-guide/security/advisories/new>

Include what you can: affected version (`helpdesk --version`), how to
reproduce, and what an attacker gains. A proof of concept helps but is not
required.

**What to expect**

| Stage | Target |
|---|---|
| Acknowledgement | 3 working days |
| Initial assessment | 10 working days |
| Fix for a confirmed high-severity issue | 30 days |

We will credit you in the advisory and the changelog unless you prefer
otherwise. This is a volunteer project; there is no bounty programme.

## Supported versions

Only the latest released version receives security fixes. The project is
pre-1.0 and moving; please stay current.

## Threat model

helpdesk-guide runs locally, serves a loopback HTTP interface, and reads
content files that may arrive through a pull request. The interesting
boundaries are therefore:

**The loopback server is not a boundary by itself.** Any process on the
machine can reach `127.0.0.1`, including JavaScript in any page the user has
open. A random token is issued per `serve`, passed in the launch URL and
exchanged for a `SameSite=Strict` cookie; state-changing requests also check
`Origin` and `Host`. A strict CSP forbids inline script, remote script,
framing and outbound connections.

**Content is untrusted input.** Runbooks are parsed with `yaml.safe_load` and
can never construct Python objects. Edge conditions (`os == "windows"`) are
evaluated by a small hand-written parser, not `eval`. Escalation templates use
a mustache subset with no expression evaluator. Markdown renders with HTML
disabled. Media paths are locked to the `media/` directory.

**Content must not contain credentials.** The compiler scans every record for
passwords, API keys, private keys, JWTs, connection strings, licence keys and
long base64 blobs, and *stops the build* on a hit. This is the gate that keeps
a knowledge base from becoming a leak. Structural rules — an AWS key id, a PEM
block — cannot be defused by a nearby "for example".

**Personal data is minimised.** End-user names and e-mail addresses are never
stored. Free-text queries are masked before they reach the database. Retention
is configurable (365 days by default) and enforced by `helpdesk prune`.

## Out of scope

* Binding to a non-loopback address with `--host`. It is supported and prints
  a warning; the server has no authentication and is not designed to face a
  network. Exposing it is a deployment decision, not a vulnerability.
* Content that is factually wrong. That is a content bug — file a
  `runbook_error` issue. It is taken seriously, but it is not a security
  report.
* Denial of service through a deliberately enormous content file.

## For people running this in an organisation

* Keep site-specific runbooks in `content-local/`, which is git-ignored.
* Run `helpdesk lint` in your own CI as well; the secret scanner is a second
  line of defence, not a first.
* Schedule `helpdesk prune` if your retention policy is shorter than the
  default.
* The database holds your runbooks and your session history. Treat the file
  with the same care as the knowledge base itself.
