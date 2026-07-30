# AIP DRP Hub Adapter

This provider-specific reference is historical deployment context, not a portable default.

As reviewed on 2026-07-30:

- API base: `https://drp-term.kube.aip.de/api/v1`
- allowed origin: `https://drp-term.kube.aip.de`
- human-facing product route: `https://drphub-p4n.aip.de/share/<product-id>`

Example public health probe:

```bash
export DRPHUB_ALLOWED_ORIGIN='https://drp-term.kube.aip.de'
python scripts/drphub_products.py \
  --base-url 'https://drp-term.kube.aip.de/api/v1' \
  health
```

Do not construct `/product/<id>` as a web page, use an `http://` URL copied from a tool
descriptor, disable TLS verification, or silently substitute another host after a
redirect.

Authenticated access must use organization-approved secret injection. Do not save a JWT,
service token, acting-user UUID, response containing private metadata, or audit data in a
project file or command transcript.
