# content-local/ — site-private content

Anything you put in this directory is **git-ignored and never published**.

This is where an organisation keeps the runbooks it cannot share: internal
hostnames, service names, vendor contract numbers, processes that are nobody
else's business.

## How it works

The compiler reads both roots, in order:

```
content/          public, generic, CC BY-SA    ← pull upstream updates freely
content-local/    yours, private               ← never committed
```

A record here with the same `code` as a public one **replaces it entirely**.
So you can clone the repository, override `VPN-001` with your own VPN
procedure, and still take upstream updates without a single merge conflict.

## Layout

Mirror the public tree:

```
content-local/
├── runbooks/
│   └── tr/
│       ├── VPN-001-acme-vpn-baglanmiyor.yaml
│       └── APP-050-acme-erp-acilmiyor.yaml
└── lexicon/
    └── tr/
        └── kurum-terimleri.yaml     # your own device names and synonyms
```

Everything in `CONTRIBUTING-CONTENT.md` applies here too — including the
secret scanner. Site-private does not mean "safe to write the admin password
into". Reference a vault entry by name; the compiler will stop you either way.

## Two layers of protection

1. `content-local/**` is the first line of `.gitignore`.
2. A pre-commit hook refuses to stage anything here, because `git add -f`
   overrides `.gitignore` and a private runbook committed once stays in the
   history forever.

Install the hook once:

```bash
pip install pre-commit && pre-commit install
```

## Keeping a backup

Since this directory is not in git, back it up yourself. It is plain YAML, so
a private repository, a file share or an encrypted archive all work:

```bash
tar czf content-local-$(date +%Y%m%d).tar.gz content-local/
```

---

*This README is the only file in this directory that is tracked.*
