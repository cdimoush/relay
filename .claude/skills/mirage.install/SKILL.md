---
name: mirage.install
description: Guided setup for Mirage Docker environment
allowed-tools: Bash, Read, AskUserQuestion, Task
---

# Mirage Install

Guided install for the Mirage Docker dev environment. Walks the user through prerequisites, image build, and first launch.

## Phase 0: Container Guard

Check if already running inside the container:

```bash
test -f /.dockerenv && echo "IN_CONTAINER" || echo "HOST"
```

If `IN_CONTAINER`: print "You're already inside the Mirage container — this skill is for host-side setup. Nothing to do." and **stop immediately**.

## Phase 1: Prerequisites Check

Run these diagnostic commands (in parallel where possible):

```bash
docker --version
docker compose version
nvidia-smi
docker info 2>/dev/null | grep -i runtime
```

Check results:
- `docker` and `docker compose` must be present.
- `nvidia-smi` must succeed (parse driver version for reporting).
- Docker nvidia runtime should be listed.

If any prerequisite is missing, print a clear error explaining what's needed and **stop**. Do not attempt to install system dependencies.

## Phase 2: Build

Spawn a **Bash sub-agent** (via the Task tool) to run the Docker build. This isolates the long-running build and its verbose output from the main context window.

The Mirage Dockerfile is a multi-stage build (ROS 2 Jazzy from source + Isaac Sim 5.1.0). Stage 1 compiles ROS and takes significant time on first build.

```bash
cd /home/conner/incubator/mirage
docker build -f .devcontainer/Dockerfile -t mirage:latest .
```

If the build fails, report the error output to the user and suggest next steps. Don't try heroic fixes.

## Phase 3: First Launch + Verify

Use the devcontainer configuration or run directly:

```bash
docker run --gpus all --runtime=nvidia --network=host --ipc=host \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /home/conner/incubator/mirage:/workspace \
  -it mirage:latest bash
```

If launch succeeds, report success with quick-start info:
- How to enter the container: `docker exec -it <container> bash`
- ROS 2 environment is auto-sourced via entrypoint
- Beads is available via `bd` commands

If launch fails, run `docker logs` for diagnostics and report to the user.
