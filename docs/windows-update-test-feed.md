# Hosting the Windows update test feed

This is the owner-run hosting procedure for the acceptance checklist in
[Windows release](windows-release.md). It reserves a stable HTTPS address before
two test installers are built. Creating an account, publishing files and running
the installed app remain owner actions; this guide does not authorize them for
the implementation agent.

## Choose the hosting boundary

Cloudflare Pages Direct Upload is a browser-only option for these static files.
Its Free plan and built-in SSL avoid a custom domain or server for this check.
See [Cloudflare Pages](https://www.cloudflare.com/products/pages/). Use the free
option and review the account's current limits; no paid add-on is needed for
this procedure.

The feed and installer will be publicly downloadable. An unlisted URL and
`noindex` do not make them private. Use this route only if publishing this test
build is acceptable. Otherwise, use an owner-controlled HTTPS server reachable
from the isolated Windows machine, with a certificate that machine already
trusts. The current updater cannot sign in through a browser authentication page
or supply private-host cookies or tokens. Do not put credentials in its URL.

Pages currently limits each asset to 25 MiB. The local 0.1.3 installer is
21.62 MiB; check each new test installer before upload. If it grows past the
limit, choose another host rather than truncating or splitting the setup.
See [Pages limits](https://developers.cloudflare.com/pages/platform/limits/).

## Reserve the address

1. Open [Cloudflare's dashboard](https://dash.cloudflare.com/), sign in to your
   own account and open Workers & Pages. Select Create application, then the
   Pages Get started / Drag and drop your files route. Choose Direct Upload,
   without connecting the private source repository.
2. Pick a separate project name such as `wiki-agent-update-check-<unique>`.
   Upload only the starter folder
   [`examples/windows-update-feed/`](examples/windows-update-feed/). It contains
   `index.html`, `404.html` and `_headers`; it contains no installer or secrets.
3. Deploy that folder. Open the stable site address shown by the dashboard,
   such as `https://wiki-agent-update-check-example.pages.dev`. Confirm the
   page says that the Windows update test host is reserved. Keep this project
   and hostname unchanged for both test versions.
4. Append `/updates/latest.json` to that actual site address. This is the feed
   URL to supply for the builds. A 404 at that path is expected until the signed
   test metadata is uploaded; the initial page only reserves the hostname.

Cloudflare documents the dashboard upload and subsequent deployments in
[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/).
Use the stable project hostname, not a deployment-specific preview URL: each
failure case replaces the metadata at the same address.

The starter's `_headers` gives `/updates/*` a `Cache-Control: no-store` response
and requests that crawlers avoid the site. Cloudflare applies this file to static
assets; it is not a password or access policy. See
[Pages headers](https://developers.cloudflare.com/pages/configuration/headers/).
The top-level `404.html` supplies a missing-file page, as described in
[Serving Pages](https://developers.cloudflare.com/pages/configuration/serving-pages/).
Do not add a catch-all redirect that would return the home page for missing
installers.

## Populate the feed after the builds

The build operator needs the exact feed URL before building two increasing
versions with `--update-feed`. Both versions must embed that URL and the same
update public key. The existing 0.1.3 installer embeds the production GitHub
feed and cannot perform this isolated-feed upgrade.

The build operator returns the older and newer test installers, their hashes,
both `.exe.sig` files and the newer build's original `latest.json`. Install the older version on the
isolated Windows machine. Keep an untouched local copy of all returned files,
including signatures, outside the upload folder.

1. Copy the starter to a separate upload folder. Create `updates/` inside it.
2. Copy the newer setup and its generated `latest.json` into `updates/`. Keep
   the setup filename unchanged. Confirm the manifest's
   `platforms.windows-x86_64.url` points to that setup on this test hostname.
3. Upload the complete folder to the same Pages project using Create a new
   deployment. Choose its production deployment environment to update the stable
   test hostname. This is a deployment of the separate test site; never change
   wiki-agent's production GitHub feed.
4. Open the feed URL in a private browser window without signing in. Confirm it
   returns JSON for the newer version, not an HTML login or challenge page.
   Download the referenced setup and compare its SHA-256 to the returned build
   hash before the app runs the update.

The final upload has this shape. Version numbers below are examples, not builds
already produced for an endpoint.

```text
feed-upload/
  index.html
  404.html
  _headers
  updates/
    latest.json
    wiki-agent_0.1.5_x64-setup.exe
```

Upload only that folder. The source checkout, `.env`, update private key,
`payload/`, conversations and acceptance receipts stay outside it. A setup
contains application code even though it excludes personal records.

## Run the three update cases in order

Use copies of the newer build's original manifest for the first two cases.
Keep the version and platform key unchanged. Save each manifest and result
locally with the test receipts; do not upload the receipts.

| Case | Change on the isolated feed | Required observation |
| --- | --- | --- |
| Missing download | Change only the platform `url` to an absent filename in this host's `updates/` directory | Update installation reports a download error; the older app and its backend remain usable |
| Invalid signature | Restore the actual setup URL; replace the platform `signature` with a signature from the older build, leaving the newer setup bytes untouched | Verification rejects the payload; the older app, records and backend remain usable |
| Successful retry | Restore the exact original newer manifest and setup | Update succeeds, the newer version opens, and the original records and CLI login remain |

For each case, redeploy the complete upload folder to the same hostname. In the
older app choose App update / Check again, then Install update. Record the exact
displayed error or success, version, timestamp and a backend request before and
after the action. Do not test a successful upgrade first: after upgrading, that
same version is no longer an offered update for the remaining failure cases.

On the Windows machine, open the browser's developer tools Network tab, load
the actual feed URL and inspect its response body and headers. Expect `no-store`
for the metadata. Record the fetched version, asset URL and signature for each
case. If a new deployment still returns an old
version or manifest, resolve that host/cache issue before interpreting the
app's result. Do not bypass TLS or updater signature verification.

Finish the install, login, hooks, restart and uninstall checks in
[Required acceptance](windows-release.md#required-acceptance). The feed setup
alone does not establish desktop acceptance. Keep the test host isolated from
production; after collecting results, the owner can retire its deployments
through the hosting account without deleting local user records.
