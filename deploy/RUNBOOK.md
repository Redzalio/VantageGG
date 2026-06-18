# VantageGG — go-live runbook (Contabo VPS + Cloudflare + Docker + Caddy)

Target: **https://vantagegg.com** on a Contabo VPS (Ubuntu 24.04), app in Docker, Caddy for HTTPS,
Cloudflare for DNS. Most of the server steps Claude can run for you over SSH once you grant access
(steps marked 🤖). The ones marked 🧍 need you (money / accounts / dashboards / 2FA).

---

## 0. Prereqs (🧍 you)
- [ ] Contabo **Cloud VPS 20** provisioned (Ubuntu 24.04, 100 GB NVMe, auto-backup on). Note its **IPv4**.
- [ ] During VPS creation, paste your **SSH public key** (the `vantagegg_deploy.pub` Claude generated).
- [ ] Domain **vantagegg.com** on Cloudflare (done ✅).

## 1. DNS — point the domain at the box (🧍 in Cloudflare, ~2 min)
In Cloudflare → vantagegg.com → DNS → Add record:
- Type **A**, Name **@**, IPv4 **<VPS IP>**, Proxy status **DNS only (grey cloud)**.  ← grey cloud matters: the orange proxy caps uploads at 100 MB and demos are 300-500 MB.
- (optional) Type **A**, Name **www**, same IP, DNS only — enables the www→apex redirect.

## 2. Log in (🤖 once your key is on the box)
```bash
ssh -i ~/.ssh/vantagegg_deploy root@<VPS IP>
```

## 3. Install Docker + Compose (🤖)
```bash
curl -fsSL https://get.docker.com | sh
docker compose version    # sanity check
```

## 4. Get the code onto the box (🤖, after the repo is on GitHub — 🧍 create the private repo)
```bash
git clone https://github.com/<you>/vantagegg.git /opt/vantagegg
cd /opt/vantagegg
```
(If you'd rather not use GitHub yet, Claude can `rsync` the folder up instead.)

## 5. Configure (🤖)
```bash
cd /opt/vantagegg
cp deploy/vantagegg.env.example .env
# generate the session secret and write it into .env:
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
mkdir -p data           # the persistent volume (DB, caches, uploads, nades)
```

## 6. Harden the app port (🤖)
By default compose publishes 8770 on 0.0.0.0 (the public IP). Behind Caddy it should only listen on
localhost. Edit `docker-compose.yml`:
```yaml
    ports:
      - "127.0.0.1:8770:8770"     # was "8770:8770" — now only Caddy can reach it
```

## 7. Start the app (🤖)
```bash
docker compose up -d --build
docker compose ps           # should show it running
curl -s localhost:8770/api/me  # quick liveness check
```

## 8. HTTPS with Caddy (🤖)
```bash
apt-get install -y caddy
cp deploy/Caddyfile /etc/caddy/Caddyfile
systemctl restart caddy
```

## 9. Firewall — open 80 + 443 (🤖)
```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```
(Also check Contabo's panel firewall, if you enabled one, allows 80/443.)

## 10. Verify (🤖)
- `curl -I https://vantagegg.com` → 200, valid cert.
- Open it in a browser → landing page, then **Sign in through Steam** → returns logged in.
- Upload a demo → it parses (watch `docker compose logs -f`) → 2D radar shows the **map background** (this is the .png-ignore fix paying off) → 3D loads.

---

## Updating later (🤖, ~1 min)
```bash
cd /opt/vantagegg && git pull && docker compose up -d --build
```
Your `./data` (DB, demos, goals, users) is on a volume and is untouched. `db.migrate()` runs additive
schema migrations automatically on startup, and `goals.json` (if any) imports itself once.

## Backups
Contabo auto-backup covers the whole disk. For an app-level snapshot before a risky change:
```bash
tar czf /root/vantagegg-data-$(date +%F).tgz -C /opt/vantagegg data
```

## Troubleshooting
- **Blank maps in 2D replay** → the image wasn't in the build. Confirm `.dockerignore` keeps `!static/**/*.png` (fixed in this repo) and rebuild.
- **Login loops / "logged out on restart"** → `SECRET_KEY` empty or changing; set a fixed one (step 5).
- **Upload fails at ~100 MB** → Cloudflare proxy is ON (orange cloud). Switch the A record to DNS only.
- **Cert won't issue** → port 80 blocked, or DNS not pointing at the box yet (wait for propagation).
