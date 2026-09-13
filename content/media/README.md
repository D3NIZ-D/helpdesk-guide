# media/

Screenshots and diagrams referenced from runbooks.

Reference them from a node with a path relative to this directory:

```yaml
- key: N52
  type: talimat
  title: "Video kablosunu yeniden bağla"
  media: ["img/displayport-klips.png"]
```

The server serves this directory at `/media/`, locked to it — a path that
tries to escape is refused.

## Rules

* **No organisation logos, real usernames, real hostnames or real serial
  numbers in a screenshot.** Blur or crop them. The secret-scanning rules
  that apply to text cannot read an image, so this one is on you.
* Keep files small: crop to the part that matters, and prefer PNG for UI
  screenshots.
* Media is licensed **CC BY-SA 4.0** like the rest of `content/`. Do not add
  a vendor's screenshot from their documentation.
* Site-private media belongs in `content-local/`, not here.
