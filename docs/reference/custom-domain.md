# Custom domain: pendulastic.com

How to move the live app from `https://pendulastic-app.vercel.app` to
`https://pendulastic.com` (+ `www`). This is a guide only: nothing here has
been bought, signed into, or changed. Every step below is done by the owner.

Vercel facts were checked against Vercel's docs on 2026-11-16:
[add a domain](https://vercel.com/docs/domains/working-with-domains/add-a-domain),
[set up a custom domain](https://vercel.com/docs/domains/set-up-custom-domain),
[deploying and redirecting](https://vercel.com/docs/domains/working-with-domains/deploying-and-redirecting),
[troubleshooting](https://vercel.com/docs/domains/troubleshooting).

## The short version

GoDaddy is only the **registrar** (who you rent the name from). Vercel stays
the **host**. Nothing about the build or the CLI deploy from `webapp/dist`
changes. You point the name at Vercel with two DNS records.

## Steps

1. **Buy `pendulastic.com` at GoDaddy** (owner). Decline the add-ons you do not
   need (GoDaddy website builder, email, "SSL certificate": Vercel issues the
   certificate itself for free). Turn on auto-renew. Keep WHOIS privacy on.

2. **Add both names to the Vercel project `pendulastic-app`**: Project >
   Settings > Domains > Add, enter `pendulastic.com`. Vercel offers to add
   `www.pendulastic.com` too and to redirect one to the other. Pick
   **`www.pendulastic.com` as primary, with `pendulastic.com` redirecting to it**.
   That is what Vercel recommends, and a CNAME on `www` lets Vercel move its
   edge without you touching DNS again. If you would rather the bare name be
   the address people see, apex-primary also works. Pick one and keep it,
   because changing it later changes the origin (see "What users notice").
   (CLI equivalent, run by the owner: `vercel domains add pendulastic.com`.)

3. **Read the exact record values off that Domains page.** Do not copy them
   from this guide or another project. Vercel's docs say to use "the values
   shown for your project". At the time of writing they are:

   | GoDaddy "Type" | "Name" | "Value" | Notes |
   |---|---|---|---|
   | A | `@` | the A value Vercel shows. The troubleshooting page says "Many projects show `76.76.21.21`, and newer projects can show an address such as `216.198.79.1`" | apex, `pendulastic.com` |
   | CNAME | `www` | the project-specific target Vercel shows, of the form `<hash>.vercel-dns-0NN.com` | Older projects showed `cname.vercel-dns.com`. Use whatever your page says. |

4. **At GoDaddy, set the records**: Domain Portfolio > `pendulastic.com` > DNS >
   DNS Records.
   - **Delete GoDaddy's defaults first.** That means the `@` A record that says
     "Parked", any `@` AAAA record, and the default `www` CNAME (it points at
     `@`). Vercel's troubleshooting page says: "Remove any other `A` or `AAAA`
     records for the apex domain before you add the new record." The same
     page says that from a third-party DNS provider "you won't be able to
     point an `AAAA` record to Vercel" (no IPv6 yet). So do not add one.
   - Add the A and CNAME records from step 3. **Use exactly what the Domains
     page shows.** On absolute CNAME records, the troubleshooting page says
     "you **must** copy the value exactly as it appears, **including** the
     period". If GoDaddy's form rejects a trailing dot, enter it without and
     check that Vercel then shows the record as valid.
   - Leave GoDaddy "Forwarding" off. Vercel does the apex-to-www redirect.
   - Leave the nameservers on GoDaddy. Pointing the domain at Vercel's
     nameservers is an alternative for any domain: both apex domains and
     subdomains "can also be configured using the Nameservers method". It is
     only *required* for wildcard domains, where certificates need DNS-01
     ("Use Vercel nameservers or delegate certificate validation"). It moves
     all DNS for the domain to Vercel and can take 24-48 h. Not needed here.

5. **Wait for Vercel to show "Valid Configuration"** for both names. This is
   usually minutes; for a domain just bought it can take a few hours.
   **HTTPS is automatic.** Vercel "automatically provisions an SSL certificate
   after DNS verification succeeds" (Let's Encrypt, HTTP-01), and renews it.
   You buy and upload nothing.

6. **Check it**: open `https://pendulastic.com`. It should land on
   `https://www.pendulastic.com/`, and the build line in Session should match
   `webapp/dist/src/build-id.js`. Check that `/sw.js` still returns
   `Cache-Control: no-cache`. That comes from `vercel.json`, which applies to
   every domain on the project.

Keep `pendulastic-app.vercel.app` assigned for now. Do not redirect it yet
(see below).

## What users notice

A browser keys everything by **origin**, and `https://www.pendulastic.com` is
a different origin from `https://pendulastic-app.vercel.app`. So:

- **Re-install once.** The service worker, offline cache and Home Screen
  install all belong to the old origin. Each phone opens the new address
  online once, then uses Share > Add to Home Screen (iOS) or Install app
  (Android) again, and deletes the old icon.
- **Trials do not move.** IndexedDB and localStorage are per origin, and the
  new address starts empty. Before switching, **export every session from
  the old app**. Then, on the new address:
  1. Create each participant first, using the same clinic ID.
  2. Select that participant.
  3. Use Trends > import, **one session at a time**: its `-manifest.json`,
     plus the `-mas.csv` beside it if there is one.

  The import attaches to the currently selected participant. It refuses a
  bundle whose clinic ID belongs to someone else, and it never overwrites
  existing records.
- **Research mode is off again.** The research-mode flag lives in
  localStorage on the old origin. Turn it on again on each device that used
  it by opening the new address once with `?research=1`.
- **Keep the old URL serving, not redirecting, during the move.** While
  `pendulastic-app.vercel.app` keeps serving deployments, an installed old
  icon keeps updating to each new build as before (its service worker checks
  `sw.js` on that origin). Once that URL is redirected, the old install's
  update check no longer reaches a service worker. It then keeps running
  whatever build it last cached, and never updates again. Redirect only
  after every device has exported and re-installed.
- **Motion permission is asked again.** DeviceMotion permission (the iOS
  "Allow motion & orientation" prompt) is granted per origin, so the first
  trial on the new address prompts once.
- Pick www-or-apex once, for the same reason: switching the primary later is
  another origin change.

## GoDaddy vs buying the domain through Vercel

| | GoDaddy | Vercel (Settings > Domains > Buy) |
|---|---|---|
| DNS setup | Manual, two records (above) | None. Records and certificate are set up automatically |
| Where you manage it | Two accounts (GoDaddy + Vercel) | One |
| Price | Often a low first-year price; check the **renewal** price and decline add-ons | Shown at checkout; renewals are not refundable, and refunds only within 4 days of purchase |
| Email at the domain | Can be bought from GoDaddy | Vercel "does not provide a mail service", so you would need a separate mail provider either way |
| Moving hosts later | Easy: change two records | Domain can be transferred out later |

Either works. Vercel is the less work, and the less chance of a typo, for a
domain that only serves this app. GoDaddy makes sense if the owner already
has a GoDaddy account, or wants the domain kept separate from the hosting
account.
