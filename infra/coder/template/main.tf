# Hearth Home workspace template.
# Based on Coder's official `docker` starter template (coder/coder examples/templates/docker,
# tag v2.38.7), changed to: hearth-dev:latest image, the repo bind-mounted at its host path,
# code-server opening the repo, web (3000) and API (8000) apps, GPU access.
terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    docker = {
      source = "kreuzwerker/docker"
    }
  }
}

locals {
  username  = data.coder_workspace_owner.me.name
  repo_path = "/path/to/memo"
  # Only the admin's workspace publishes 3000/8000 on the host loopback (one owner per port).
  publish_ports = data.coder_workspace_owner.me.name == "hearth" && data.coder_workspace.me.name == "hearth"
}

variable "docker_socket" {
  default     = ""
  description = "(Optional) Docker socket URI"
  type        = string
}

provider "docker" {
  host = var.docker_socket != "" ? var.docker_socket : null
}

data "coder_provisioner" "me" {}
data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

resource "coder_agent" "main" {
  arch           = data.coder_provisioner.me.arch
  os             = "linux"
  dir            = local.repo_path
  startup_script = <<-EOT
    set -e
    if [ ! -f ~/.init_done ]; then
      cp -rT /etc/skel ~
      touch ~/.init_done
    fi
    git config --global --add safe.directory '*' || true
  EOT

  env = {
    GIT_AUTHOR_NAME     = coalesce(data.coder_workspace_owner.me.full_name, data.coder_workspace_owner.me.name)
    GIT_AUTHOR_EMAIL    = "${data.coder_workspace_owner.me.email}"
    GIT_COMMITTER_NAME  = coalesce(data.coder_workspace_owner.me.full_name, data.coder_workspace_owner.me.name)
    GIT_COMMITTER_EMAIL = "${data.coder_workspace_owner.me.email}"
  }

  metadata {
    display_name = "CPU Usage"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM Usage"
    key          = "1_ram_usage"
    script       = "coder stat mem"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "GPU"
    key          = "2_gpu"
    script       = "nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv,noheader 2>/dev/null || echo none"
    interval     = 30
    timeout      = 2
  }

  metadata {
    display_name = "Load Average (Host)"
    key          = "6_load_host"
    script       = <<EOT
      echo "`cat /proc/loadavg | awk '{ print $1 }'` `nproc`" | awk '{ printf "%0.2f", $1/$2 }'
    EOT
    interval     = 60
    timeout      = 1
  }
}

# See https://registry.coder.com/modules/coder/code-server
module "code-server" {
  count    = data.coder_workspace.me.start_count
  source   = "registry.coder.com/coder/code-server/coder"
  version  = "~> 1.0"
  agent_id = coder_agent.main.id
  folder   = local.repo_path
  order    = 1
}

# Hearth web (Next.js) on 3000 and API (FastAPI) on 8000, opened through Coder.
# Subdomain apps work because the dev tunnel sets a wildcard app URL (*--<id>.<region>.try.coder.app).
resource "coder_app" "web" {
  agent_id     = coder_agent.main.id
  slug         = "web"
  display_name = "Hearth web :3000"
  icon         = "/icon/nodejs.svg"
  url          = "http://localhost:3000"
  subdomain    = true
  share        = "authenticated"
  order        = 2
}

resource "coder_app" "api" {
  agent_id     = coder_agent.main.id
  slug         = "api"
  display_name = "Hearth API :8000"
  icon         = "/icon/python.svg"
  url          = "http://localhost:8000"
  subdomain    = true
  share        = "authenticated"
  order        = 3
}

resource "docker_volume" "home_volume" {
  name = "coder-${data.coder_workspace.me.id}-home"
  lifecycle {
    ignore_changes = all
  }
  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
  labels {
    label = "coder.owner_id"
    value = data.coder_workspace_owner.me.id
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  labels {
    label = "coder.workspace_name_at_creation"
    value = data.coder_workspace.me.name
  }
}

resource "docker_container" "workspace" {
  count    = data.coder_workspace.me.start_count
  image    = "hearth-dev:latest"
  name     = "coder-${data.coder_workspace_owner.me.name}-${lower(data.coder_workspace.me.name)}"
  hostname = data.coder_workspace.me.name
  user     = "1000:1000"
  # Use the docker gateway if the access URL is 127.0.0.1
  entrypoint  = ["sh", "-c", replace(coder_agent.main.init_script, "/localhost|127\\.0\\.0\\.1/", "host.docker.internal")]
  env         = ["CODER_AGENT_TOKEN=${coder_agent.main.token}"]
  working_dir = local.repo_path
  gpus        = "all"
  host {
    host = "host.docker.internal"
    ip   = "host-gateway"
  }
  volumes {
    container_path = "/home/coder"
    volume_name    = docker_volume.home_volume.name
    read_only      = false
  }
  # Identical path inside and out: git worktrees store absolute paths.
  volumes {
    container_path = local.repo_path
    host_path      = local.repo_path
    read_only      = false
  }

  dynamic "ports" {
    for_each = local.publish_ports ? [3000, 8000] : []
    content {
      internal = ports.value
      external = ports.value
      ip       = "127.0.0.1"
    }
  }

  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
  labels {
    label = "coder.owner_id"
    value = data.coder_workspace_owner.me.id
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  labels {
    label = "coder.workspace_name"
    value = data.coder_workspace.me.name
  }
  labels {
    label = "hearth.workspace"
    value = "true"
  }
}
