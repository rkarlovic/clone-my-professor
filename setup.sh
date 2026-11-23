#!/usr/bin/env bash
# Set up and start Clone My Professor (CMP) on Linux or macOS. Safe to run again: it updates
# and starts. Checks for Git and Docker (with --install it installs them), fetches the project
# if needed, creates .env with random passwords and secrets, builds and starts all containers
# (vLLM too when an NVIDIA GPU is present), downloads the AI models and waits until CMP answers.
#
#   ./setup.sh                 check, set up, start
#   ./setup.sh --install       also install missing Git / Docker (apt, dnf or Homebrew; sudo)
#   ./setup.sh --with-moodle   plus a local test Moodle connected to CMP
#   ./setup.sh --with-tracing  plus Langfuse: a trace of every answer (http://127.0.0.1:3000)
#   ./setup.sh --check         then run the smoke test (10 questions; ~12 min on a CPU)
#   ./setup.sh --benchmark     then the internal benchmark on the courses in data/ (docs/GPU-SETUP.md)
#   ./setup.sh --benchmark --benchmark-args "--retrieval-only"   options of scripts.benchmark
#   ./setup.sh --stop          stop everything (data is kept)
#   ./setup.sh --status
#   options: --site <host name> (default localhost)  --no-dev-signin  --dir <folder>
set -euo pipefail

REPO_URL="https://github.com/rkarlovic/clone-my-professor.git"
INSTALL=0 WITH_MOODLE=0 WITH_TRACING=0 CHECK=0 BENCHMARK=0 BENCHMARK_ARGS="" STOP=0 STATUS=0 SITE="localhost" NO_DEV=0 DIR="clone-my-professor"
while [ $# -gt 0 ]; do
  case "$1" in
    --install) INSTALL=1 ;;
    --with-moodle) WITH_MOODLE=1 ;;
    --with-tracing) WITH_TRACING=1 ;;
    --check) CHECK=1 ;;
    --benchmark) BENCHMARK=1 ;;
    --benchmark-args) BENCHMARK_ARGS="$2"; shift ;;
    --stop) STOP=1 ;;
    --status) STATUS=1 ;;
    --site) SITE="$2"; shift ;;
    --no-dev-signin) NO_DEV=1 ;;
    --dir) DIR="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
  shift
done

say() { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*"; }
fail() { printf '\033[31mxx  %s\033[0m\n' "$*"; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }
secret() { head -c "${1:-36}" /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

# --- 1. tools ------------------------------------------------------------------------------------
install_tools() {
  if [ "$(uname)" = "Darwin" ]; then
    has brew || fail "Homebrew is needed to install automatically: https://brew.sh"
    has git || brew install git
    has docker || { brew install --cask docker; open -a Docker; }
  elif has apt-get; then
    sudo apt-get update
    has git || sudo apt-get install -y git
    has docker || sudo apt-get install -y docker.io docker-compose-v2
    sudo usermod -aG docker "$USER" || true
  elif has dnf; then
    has git || sudo dnf install -y git
    has docker || { sudo dnf install -y docker docker-compose; sudo systemctl enable --now docker; }
    sudo usermod -aG docker "$USER" || true
  else
    fail "no supported package manager (apt, dnf, Homebrew); install Git and Docker by hand"
  fi
}
if ! has git || ! has docker; then
  if [ "$INSTALL" = 1 ]; then
    say "Installing missing tools..."
    install_tools
  else
    has git || warn "Git is missing: https://git-scm.com/downloads"
    has docker || warn "Docker is missing: https://docs.docker.com/engine/install/ (Linux) or Docker Desktop (macOS)"
    warn "Or run: ./setup.sh --install"
    exit 1
  fi
fi
docker compose version >/dev/null 2>&1 || fail "the Docker Compose plugin is missing (docker compose)"

# --- 2. the project ------------------------------------------------------------------------------
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$ROOT/infra/docker-compose.yml" ]; then
  if [ ! -f "$DIR/infra/docker-compose.yml" ]; then
    say "Fetching the project into $DIR ..."
    git clone "$REPO_URL" "$DIR"
  fi
  ROOT="$(cd "$DIR" && pwd)"
fi
cd "$ROOT"

# --- 3. Docker running? -------------------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  [ "$(uname)" = "Darwin" ] && open -a Docker 2>/dev/null || true
  for _ in $(seq 1 36); do docker info >/dev/null 2>&1 && break; sleep 5; done
  docker info >/dev/null 2>&1 || fail "Docker is not running (on Linux: sudo systemctl start docker; you may need to log in again after being added to the docker group)"
fi

# The graphics card: which models file fits its memory. 24 GB+: Qwen 3.5 9B (full precision),
# Ollama on the GPU too; 14-23 GB: the 9B with 4-bit weights; 7-13 GB: the 4B with 4-bit weights,
# embeddings on the CPU; smaller or none: everything on the CPU.
GPU=0 GPU_OLLAMA=0 VRAM_MB=0 MODELS_FILE=models.yaml
if has nvidia-smi; then
  VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')"
  VRAM_MB="${VRAM_MB:-0}"
fi
if [ "$VRAM_MB" -ge 22000 ]; then GPU=1 GPU_OLLAMA=1 MODELS_FILE=models.vllm.yaml
elif [ "$VRAM_MB" -ge 14000 ]; then GPU=1 MODELS_FILE=models.vllm-16gb.yaml
elif [ "$VRAM_MB" -ge 7000 ]; then GPU=1 MODELS_FILE=models.vllm-8gb.yaml
elif [ "$VRAM_MB" -gt 0 ]; then warn "The NVIDIA card has only $VRAM_MB MB of memory, too little for vLLM: the models run on the CPU."
fi
[ "$GPU" = 1 ] && say "NVIDIA GPU with $(( (VRAM_MB + 512) / 1024 )) GB of memory: config/$MODELS_FILE"
COMPOSE=(docker compose -f infra/docker-compose.yml)
[ "$GPU" = 1 ] && COMPOSE+=(-f infra/docker-compose.gpu.yml)
[ "$GPU_OLLAMA" = 1 ] && COMPOSE+=(-f infra/docker-compose.gpu-ollama.yml)
TRACING=$WITH_TRACING
[ -f .env ] && grep -q "^LANGFUSE_SALT=" .env && TRACING=1  # chosen before: stays on
[ "$TRACING" = 1 ] && COMPOSE+=(-f infra/docker-compose.langfuse.yml)
COMPOSE+=(--env-file .env)
[ "$WITH_MOODLE" = 1 ] && COMPOSE+=(--profile moodle)

if [ "$STOP" = 1 ]; then say "Stopping CMP (data is kept)..."; "${COMPOSE[@]}" --profile moodle stop; exit 0; fi
if [ "$STATUS" = 1 ]; then "${COMPOSE[@]}" --profile moodle ps; exit 0; fi

# --- 4. .env -------------------------------------------------------------------------------------
if [ ! -f .env ]; then
  say "Creating .env with new random passwords and secrets..."
  cp .env.example .env
  for p in change-me-owner change-me-app change-me-moodle-db; do
    v="$(secret 24)"; sed -i.bak "s|$p|$v|g" .env
  done
  sed -i.bak "s|change-me-Admin-1!|$(secret 18)aA1!|g; s|change-me-Test-1!|$(secret 18)aA1!|g" .env
  rm -f .env.bak
fi
get_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
set_env() {
  if grep -qE "^$1=" .env; then
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
[ -n "$(get_env SESSION_SECRET)" ] || set_env SESSION_SECRET "$(secret 48)"
[ -n "$(get_env ADMIN_TOKEN)" ] || set_env ADMIN_TOKEN "$(secret 36)"
[ -n "$(get_env CMP_SITE)" ] || set_env CMP_SITE "$SITE"
# A real host name: HTTPS with an automatic certificate. localhost: plain http (a secure context
# for browsers anyway), so a local trial shows no certificate warning.
if [ "$(get_env CMP_SITE)" = localhost ]; then
  set_env CMP_SCHEME http; set_env CMP_SITE_ADDRESS http://localhost
else
  set_env CMP_SCHEME https; set_env CMP_SITE_ADDRESS "$(get_env CMP_SITE)"
fi
SCHEME="$(get_env CMP_SCHEME)"
if [ -z "$(get_env AUTH_MODE)" ]; then
  # Name-only sign-in is for trying CMP on this computer; a real server uses LTI / SSO only.
  if [ "$SITE" = "localhost" ] && [ "$NO_DEV" = 0 ]; then set_env AUTH_MODE dev; else set_env AUTH_MODE prod; fi
fi
if [ "$TRACING" = 1 ]; then
  # Langfuse's own passwords and the API keys CMP sends traces with (created in Langfuse on its
  # first start). Only missing ones are added, so a rerun keeps existing data readable.
  hex() { od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'; }
  for kv in "LANGFUSE_PUBLIC_KEY=pk-lf-$(hex 16)" "LANGFUSE_SECRET_KEY=sk-lf-$(hex 24)" \
    "LANGFUSE_DB_PASSWORD=$(hex 18)" "LANGFUSE_SALT=$(hex 24)" "LANGFUSE_ENCRYPTION_KEY=$(hex 32)" \
    "LANGFUSE_NEXTAUTH_SECRET=$(hex 32)" "LANGFUSE_CLICKHOUSE_PASSWORD=$(hex 18)" \
    "LANGFUSE_MINIO_PASSWORD=$(hex 18)" "LANGFUSE_REDIS_PASSWORD=$(hex 18)" \
    "LANGFUSE_ADMIN_PASSWORD=$(hex 12)Aa1!"; do
    [ -n "$(get_env "${kv%%=*}")" ] || set_env "${kv%%=*}" "${kv#*=}"
  done
fi
# The models file for this computer and, with a GPU, how vLLM starts its model.
set_env CMP_MODELS_FILE "$MODELS_FILE"
if [ "$GPU" = 1 ]; then
  set_env VLLM_MODEL "$(sed -n 's/^serve_model:[[:space:]]*\([^[:space:]]*\).*/\1/p' "config/$MODELS_FILE")"
  set_env VLLM_ARGS "$(sed -n 's/^serve_args:[[:space:]]*"\([^"]*\)".*/\1/p' "config/$MODELS_FILE")"
fi

# --- 5. build and start --------------------------------------------------------------------------
if [ "$GPU" = 1 ]; then say "Building and starting CMP (vLLM serves $(get_env VLLM_MODEL); the first start downloads it)..."
else say "Building and starting CMP (no NVIDIA GPU: models run on the CPU)..."; fi
"${COMPOSE[@]}" up -d --build

# --- 6. models -----------------------------------------------------------------------------------
# The api container reads the active models file (models.vllm.yaml with a GPU) and pulls every
# Ollama model it names that is missing; vLLM downloads its own model (VLLM_MODEL) on start.
say "Checking the AI models (missing ones are downloaded once; can take a while)..."
"${COMPOSE[@]}" exec -T api python -m scripts.models --pull

# --- 6b. with a GPU: wait until vLLM has loaded its model -------------------------------------------
# The first start downloads the model (several GB) before vLLM answers; questions sent earlier
# fail with "connection refused". If vLLM stops or keeps restarting, show why.
VLLM_READY=1
if [ "$GPU" = 1 ]; then
  VLLM_READY=0
  say "Waiting for vLLM to load $(get_env VLLM_MODEL) (the first start downloads it: 10-30 minutes)..."
  VLLM_ID="$("${COMPOSE[@]}" ps -q vllm 2>/dev/null | grep -E "^[0-9a-f]{12,}$" | head -1)"
  STARTED=$(date +%s); LAST_NOTE=$STARTED
  while [ -n "$VLLM_ID" ] && [ $(( $(date +%s) - STARTED )) -lt 2700 ]; do
    read -r V_STATUS V_RESTARTS V_HEALTH <<< "$(docker inspect --format '{{.State.Status}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$VLLM_ID" 2>/dev/null)"
    if [ "$V_HEALTH" = healthy ]; then VLLM_READY=1; break; fi
    case "$V_STATUS" in exited|dead) break ;; esac
    [ "${V_RESTARTS:-0}" -gt 0 ] && break  # crashed (restarts on its own)
    if [ $(( $(date +%s) - LAST_NOTE )) -ge 60 ]; then
      echo "    $(( ($(date +%s) - STARTED) / 60 )) min: $(docker logs --tail 1 "$VLLM_ID" 2>&1 | cut -c1-110)"
      LAST_NOTE=$(date +%s)
    fi
    sleep 10
  done
  if [ "$VLLM_READY" = 1 ]; then say "vLLM is ready ($(( ($(date +%s) - STARTED) / 60 )) min)."
  else
    warn "vLLM did not start. Its last log lines:"
    [ -n "$VLLM_ID" ] && docker logs --tail 40 "$VLLM_ID" 2>&1 | sed 's/^/    /'
    warn "Look for the cause in docs/GPU-SETUP.md, section 'If something goes wrong'; paste these lines when asking for help."
  fi
fi

# --- 7. wait until CMP answers --------------------------------------------------------------------
SITE_NAME="$(get_env CMP_SITE)"
say "Waiting for $SCHEME://$SITE_NAME ..."
OK=0
for _ in $(seq 1 60); do
  [ "$(curl -k -s -o /dev/null -w '%{http_code}' "$SCHEME://$SITE_NAME/health" || true)" = 200 ] && { OK=1; break; }
  sleep 5
done
[ "$OK" = 1 ] || warn "CMP did not answer yet; check with: ./setup.sh --status"

# --- 8. optional: a local test Moodle ---------------------------------------------------------------
if [ "$WITH_MOODLE" = 1 ]; then
  MOODLE_PORT="$(get_env CMP_MOODLE_PORT)"; MOODLE_PORT="${MOODLE_PORT:-8080}"
  say "Setting up the local Moodle (http://127.0.0.1:$MOODLE_PORT)..."
  for _ in $(seq 1 30); do  # wait until Moodle's web server answers
    case "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$MOODLE_PORT/login/index.php" || true)" in 200|303) break ;; esac
    sleep 3
  done
  "${COMPOSE[@]}" exec -T -u www-data moodle sh /opt/cmp/install.sh >/dev/null 2>&1 || true
  JSON="$("${COMPOSE[@]}" exec -T -u www-data moodle php /opt/cmp/setup_cmp.php 2>/dev/null | sed -n '/^{/,$p')"
  field() { printf '%s' "$JSON" | sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" | head -1; }
  set_env TEST_MOODLE_WS_TOKEN "$(field ws_token)"
  set_env CMP_FRAME_ANCESTORS "http://127.0.0.1:$MOODLE_PORT"
  "${COMPOSE[@]}" up -d --force-recreate api worker web
  ISSUER="$(field issuer | sed 's#\\/#/#g')"
  "${COMPOSE[@]}" exec -T api python -m scripts.lti_platform add --name "Moodle (local test)" \
    --issuer "$ISSUER" --client-id "$(field client_id)" --auth-url "$(field auth_url | sed 's#\\/#/#g')" \
    --jwks-url http://moodle/mod/lti/certs.php --deployment-id "$(field deployment_id)"
  "${COMPOSE[@]}" exec -T api python -m scripts.lti_platform ws --issuer "$ISSUER" \
    --base-url http://moodle --token-env TEST_MOODLE_WS_TOKEN
fi


# --- 9. optional: smoke test ------------------------------------------------------------------------
if [ "$CHECK" = 1 ] && [ "$VLLM_READY" = 0 ]; then warn "Smoke test skipped: vLLM is not running (see above)."
elif [ "$CHECK" = 1 ]; then
  say "Smoke test: CMP's own small course and 10 questions (about 12 minutes on a CPU, seconds on a GPU)..."
  "${COMPOSE[@]}" exec -T api python -m scripts.smoketest || warn "The smoke test did not pass; see the lines marked FAIL above."
fi
# --- 10. optional: the internal benchmark (course folders in data/, sets in eval/sets/) -------------
if [ "$BENCHMARK" = 1 ] && [ "$VLLM_READY" = 0 ]; then warn "Benchmark skipped: vLLM is not running (see above)."
elif [ "$BENCHMARK" = 1 ]; then
  say "Benchmark: every course in data/ with a question set; summary in eval/reports/ (hours on a CPU)..."
  # run as this user, so the reports and the parse cache in the project folder stay yours
  # shellcheck disable=SC2086
  "${COMPOSE[@]}" run --rm --user "$(id -u):$(id -g)" benchmark python -m scripts.benchmark $BENCHMARK_ARGS \
    || warn "The benchmark reported failed steps; see 'Failed' at the end of its output."
fi
# --- done -------------------------------------------------------------------------------------------
echo
say "CMP is running: $SCHEME://$SITE_NAME"
[ "$(get_env AUTH_MODE)" = dev ] && echo "    Sign in on the start page with any name (test sign-in, this computer only)."
echo "    Admin page: $SCHEME://$SITE_NAME/#/admin  (key: ADMIN_TOKEN in .env)"
if [ "$WITH_MOODLE" = 1 ]; then
  echo "    Test Moodle: http://127.0.0.1:$MOODLE_PORT  users teacher1 / student1, password: MOODLE_TEST_PASSWORD in .env"
  echo "    Open the course 'Rad s darovitom djecom (Moodle demo)' and the activity 'Asistent (CMP)'."
fi
if [ "$TRACING" = 1 ]; then
  LF_PORT="$(get_env LANGFUSE_PORT)"; LF_USER="$(get_env LANGFUSE_ADMIN_EMAIL)"
  echo "    Langfuse (traces): http://127.0.0.1:${LF_PORT:-3000}  user ${LF_USER:-admin@cmp.local}, password: LANGFUSE_ADMIN_PASSWORD in .env"
fi
echo "    Stop: ./setup.sh --stop    Start again: ./setup.sh    Status: ./setup.sh --status"
