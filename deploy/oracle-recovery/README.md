# Recovering the old Oracle VM (150.136.151.230)

**Status established 2026-08-06:** the VM is **alive** and still serving the real
"Kale.ai — Hardware Engineering AI" Next.js app on `https://kale.150.136.151.230.nip.io`.
Ports 22/80/443 are all open. But **no SSH key and no Oracle API key exist anywhere on this
laptop** (verified: `deploy-oracle.sh` says the key was "not stored on this machine";
`ssh ubuntu@…` → Permission denied (publickey); a full disk scan found only library test
`.pem` fixtures). SSH is publickey-only, so there is **no way back in from the laptop alone**
— recovery has to be authorised through your Oracle Cloud login. That is the one step only
you can take.

The method here is **non-destructive**: it does not reboot the box, touch the running app,
or change networking. It uses the **Oracle Cloud Agent → Run Command** feature to run one
script as root straight from the browser console — restoring our SSH access and, as a bonus,
snapshotting the app source that lives *only* on this box.

## Step 1 — run the recovery script from the OCI Console (needs your login)

1. Sign in at <https://cloud.oracle.com>.
2. **Compute → Instances**, open the instance with public IP **150.136.151.230**.
3. In the left menu under *Resources* (or the **More actions** button), choose
   **Run command** → **Create command** (this is the Oracle Cloud Agent "Custom scripts"
   feature; it's enabled by default on Oracle's platform images).
4. Paste the entire contents of **`recover.sh`** (in this folder) into the command box and
   run it. It finishes in a few seconds and prints where it saved the source snapshot.

> If **Run command** is greyed out (the agent plugin is off), use the fallback in Step 3.

## Step 2 — pull everything back to the laptop

Once the command reports success:

```powershell
cd "C:\Users\Kerem\OneDrive\Desktop\Kale-Forge-Complete-2026\deploy\oracle-recovery"
.\pull-source.ps1 -VmIp 150.136.151.230
```

This verifies SSH is restored with the new recovery key
(`~/.ssh/kale-oracle-recovery`), then downloads and unpacks:

- `recovered/app-source/` — the Kale.ai app source (the thing that existed nowhere else)
- `recovered/kale-app-container-inspect.json` / `kale-compose-resolved.yml` — exactly how
  it runs, so it can be rebuilt or redeployed later

From then on: `ssh -i ~/.ssh/kale-oracle-recovery ubuntu@150.136.151.230`.

## Step 3 — fallback if "Run command" isn't available

Two console-only routes that also need just your Oracle login:

- **Cloud Shell + OCI CLI:** open Cloud Shell (top-right terminal icon), then
  `oci compute instance-action` won't add a key, but you can
  `oci instance-agent command create …` to run the same `recover.sh`. Simpler: use the
  **Console Connection**.
- **Boot-volume swap (heaviest, last resort):** stop the instance, detach its boot volume,
  attach it to a second small instance you can log into, append
  `~/.ssh/kale-oracle-recovery.pub` to `/home/ubuntu/.ssh/authorized_keys` on the mounted
  volume, reattach, boot. This causes a few minutes of app downtime; the Run Command route
  above avoids that entirely.

## The recovery key

Generated on this laptop for this purpose (private half stays here):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEYfzAZJ71K7o7bzbFMmOvR6na+ZpmFrZY1QH4YT6BnE kale-oracle-recovery
```

Private key: `~/.ssh/kale-oracle-recovery`. `recover.sh` embeds the public half.
