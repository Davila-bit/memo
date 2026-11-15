# Coder dev environment (Memo)

Coder v2.38.7 (OSS) in Docker, built-in Postgres, data in volume `hearth-coder-home`.

- **Start:** `docker compose -f infra/coder/compose.yaml up -d` (stop: `... stop`). Rebuild the workspace image: `docker build -t hearth-dev:latest infra/coder`.
- **URLs:** local `http://127.0.0.1:7080`; public (built-in dev tunnel, `CODER_ACCESS_URL` unset) `https://<tunnel-id>.pit-1.try.coder.app`. The URL survives restarts (key in the volume); confirm with `docker logs hearth-coder | grep try.coder.app`.
- **Admin:** user `hearth`; password in `~/.config/memo-secrets/.env.coder` on the host (outside the repo, so workspaces cannot read it).
- **Workspace `hearth`** (container `coder-hearth-hearth`, image `hearth-dev:latest`): the repo is bind-mounted at `/path/to/memo`, GPU on. Apps: code-server (VS Code in the browser), `web` (:3000) and `api` (:8000). Ports 3000/8000 are also published on host `127.0.0.1`.
- **Teammate web URLs:** `https://web--hearth--hearth--<tunnel-id>.pit-1.try.coder.app/` and `https://api--hearth--hearth--<tunnel-id>.pit-1.try.coder.app/`. Apps are shared with any signed-in user.
- **Push template changes:** `docker cp infra/coder/template/. hearth-coder:/home/coder/hearth-template/ && docker exec hearth-coder coder templates push hearth -d /home/coder/hearth-template --yes`, then `coder update hearth`.
- **Add a teammate:** admin goes to Deployment → Users → Create user (or `docker exec hearth-coder coder users create -u NAME -e EMAIL -p PASSWORD`). They sign in at the public URL and create their own workspace from the `hearth` template (code-server is owner-only; the subdomain `web`/`api` URLs of any workspace open for every signed-in user). All workspaces mount the same host checkout, so use git worktrees for parallel work.
- **Host-side commands in the workspace:** `scripts/ws '<cmd>'` (`WS_ENV="A,B"` passes env vars through).
- **Tear down:** `docker exec hearth-coder coder delete hearth --yes`, then `docker compose -f infra/coder/compose.yaml down -v` (deletes all Coder data). Workspace home volumes are named `coder-<id>-home`.
