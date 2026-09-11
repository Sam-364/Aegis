# Aegis on Kubernetes (Kustomize)

Plain manifests for the Aegis runtime, organised as a Kustomize base plus
`dev` and `production` overlays. A Helm chart with the same topology lives in
[`../helm/aegis`](../helm/aegis) - pick one; do not mix them in a namespace.

```
deploy/kubernetes/
├── base/                       # everything environment-independent
│   ├── kustomization.yaml      # namespace, labels, ConfigMap generators, images
│   ├── namespace.yaml          # `aegis` namespace (PSA baseline, warn restricted)
│   ├── serviceaccount.yaml     # no API access, no token mounted
│   ├── secret.example.yaml     # TEMPLATE for `aegis-secrets` (not applied)
│   ├── api.yaml                # Deployment (2) + Service + PDB + HPA (CPU 70%, 2-6)
│   ├── worker.yaml             # Deployment (2) + PDB, liveness on :9464, 60s grace
│   ├── detector.yaml           # Deployment (1), Redis leader lock, metrics :9465
│   ├── web.yaml                # Deployment (2) + Service + PDB, uid 1001
│   ├── migrate-job.yaml        # Job `aegis-migrate` running `aegis migrate`
│   ├── ingress.yaml            # nginx Ingress for api + web, TLS placeholder
│   └── networkpolicy.yaml      # default-deny ingress + explicit allows
├── overlays/
│   ├── dev/                    # kind/minikube: 1 replica each, in-cluster infra
│   │   ├── kustomization.yaml  # merges config, generates dev Secret, patches
│   │   ├── simulator.yaml      # aegis-simulator (dev only)
│   │   ├── postgres.yaml       # pgvector/pgvector:pg17 StatefulSet (1)
│   │   ├── redis.yaml          # redis:7-alpine (1)
│   │   ├── temporal.yaml       # temporalio/auto-setup + temporalio/ui
│   │   └── networkpolicy-dev.yaml
│   └── production/             # managed Postgres/Redis/Temporal, TLS, spread
│       ├── kustomization.yaml
│       ├── patches/            # namespace PSA restricted, resources, hpa, ingress
│       └── externalsecret.example.yaml
└── monitoring/                 # optional prometheus-operator CRDs
    ├── servicemonitor-api.yaml # api /metrics via Service port `http`
    ├── podmonitor-worker.yaml  # worker :9464
    └── podmonitor-detector.yaml# detector :9465
```

## Processes

| Component | Command           | Port | Probes                                     | Notes |
|-----------|-------------------|------|--------------------------------------------|-------|
| api       | `aegis-api`       | 8600 | readiness `/ready`, liveness `/health`     | `/ready` verifies schema at head, Redis, Temporal, infrastructure gateway |
| worker    | `aegis-worker`    | 9464 | liveness `GET :9464/` (metrics server)     | no readiness (no Service traffic); `terminationGracePeriodSeconds: 60` |
| detector  | `aegis-detector`  | 9465 | liveness `GET :9465/`                      | leader lock in Redis - 1 replica is enough, more are standbys |
| web       | Next.js standalone| 3000 | `GET /`                                    | image runs as uid 1001 |
| simulator | `aegis-simulator` | 8601 | `GET /health`                              | **dev overlay only** |
| migrate   | `aegis migrate`   | -    | Job                                        | must complete before api reports ready |

All first-party containers run as non-root (uid 1000, web 1001), with
`readOnlyRootFilesystem: true`, all capabilities dropped, `RuntimeDefault`
seccomp and no service-account token. `/tmp` (and `/app/.next/cache` for web)
are `emptyDir`s.

## Configuration

* **ConfigMap `aegis-config`** - every non-secret `AEGIS_*` setting
  (`src/aegis/config.py`). Generated with a content hash, so a change rolls the
  Deployments. Overlays override keys with `configMapGenerator` + `behavior: merge`.
* **ConfigMap `aegis-web-config`** - console runtime env. `NEXT_PUBLIC_*` values are
  inlined into the browser bundle **at image build time** (`apps/web/Dockerfile`
  build args); keep them in sync with how you built `aegis/web`.
* **Secret `aegis-secrets`** - `AEGIS_LLM_API_KEY`, `AEGIS_DATABASE_URL`,
  `AEGIS_REDIS_URL`, `AEGIS_API_KEYS`. **Not created by the base or the production
  overlay.** See [`base/secret.example.yaml`](base/secret.example.yaml):

  ```bash
  kubectl create namespace aegis --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n aegis create secret generic aegis-secrets \
    --from-literal=AEGIS_LLM_API_KEY='sk-...' \
    --from-literal=AEGIS_DATABASE_URL='postgresql+asyncpg://aegis:...@db.internal:5432/aegis' \
    --from-literal=AEGIS_REDIS_URL='rediss://:...@redis.internal:6379/0' \
    --from-literal=AEGIS_API_KEYS='k1:admin:oncall,k2:viewer:dashboard'
  ```

  or sync it with External Secrets Operator
  ([`overlays/production/externalsecret.example.yaml`](overlays/production/externalsecret.example.yaml)).
  The LLM key and API keys are marked `optional` on the pods; the DB and Redis
  URLs are required.

Settings validation to keep in mind (`config.py`):

* `AEGIS_ENVIRONMENT=production` **refuses** `AEGIS_API_AUTH_MODE=disabled` - the
  production overlay sets `api_key`; populate `AEGIS_API_KEYS`.
* `AEGIS_LLM_PROVIDER=openai` requires the key; the detector, simulator and
  migrate Job force `disabled` since they never call the model.

## Dev: kind / minikube

```bash
# 1. cluster + ingress controller (kind example)
kind create cluster --config - <<'YAML'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - { containerPort: 80, hostPort: 80 }
YAML
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx wait --for=condition=available deploy/ingress-nginx-controller --timeout=5m

# 2. images (same tags docker compose produces)
docker compose build
kind load docker-image aegis/aegis:local aegis/web:local

# 3. deploy
kubectl apply -k deploy/kubernetes/overlays/dev
kubectl -n aegis wait --for=condition=complete --timeout=10m job/aegis-migrate
kubectl -n aegis rollout status deploy/api deploy/worker deploy/detector deploy/web

# 4. use it (localtest.me resolves to 127.0.0.1)
curl -s http://api.aegis.localtest.me/ready | jq
open http://aegis.localtest.me            # console
open http://temporal.aegis.localtest.me   # Temporal UI
```

The dev overlay runs a single-replica pgvector Postgres (2Gi PVC), Redis,
Temporal auto-setup + UI and the simulator, sets `AEGIS_LLM_PROVIDER=disabled`
(deterministic planner, no key needed) and generates a throwaway
`aegis-secrets`. To use a real model, set `AEGIS_LLM_PROVIDER=openai` in the
overlay's `configMapGenerator` and the key in its `secretGenerator`.

## Production

Postgres (with pgvector), Redis and Temporal are **not** deployed. Point the
runtime at managed services:

| Setting                  | Where                       | Example |
|--------------------------|-----------------------------|---------|
| `AEGIS_DATABASE_URL`     | Secret `aegis-secrets`      | `postgresql+asyncpg://aegis:pw@aegis.abc.eu-west-1.rds.amazonaws.com:5432/aegis` |
| `AEGIS_REDIS_URL`        | Secret `aegis-secrets`      | `rediss://:pw@aegis.abc.cache.amazonaws.com:6379/0` |
| `AEGIS_TEMPORAL_ADDRESS` | ConfigMap (overlay literal) | `aegis.abcde.tmprl.cloud:7233` or `temporal-frontend.temporal.svc.cluster.local:7233` |
| `AEGIS_SIMULATOR_URL`    | ConfigMap (overlay literal) | your infrastructure gateway - `/ready` checks it |

Steps:

```bash
# 0. edit overlays/production: hosts, AEGIS_TEMPORAL_ADDRESS, AEGIS_SIMULATOR_URL,
#    image registry; pin the tag CI pushed (sha-<commit>)
cd deploy/kubernetes/overlays/production
kustomize edit set image \
  ghcr.io/example-org/aegis/aegis=ghcr.io/example-org/aegis/aegis:sha-<sha> \
  ghcr.io/example-org/aegis/web=ghcr.io/example-org/aegis/web:sha-<sha>
cd -

# 1. secret (see above) and TLS (cert-manager annotation, or create aegis-tls yourself)
# 2. apply
kubectl apply -k deploy/kubernetes/overlays/production
kubectl -n aegis wait --for=condition=complete --timeout=10m job/aegis-migrate
kubectl -n aegis rollout status deploy/api deploy/worker deploy/detector deploy/web
```

### Migration ordering (Helm-hook semantics by hand)

`aegis-migrate` is a plain Job applied together with everything else. That is
safe because the API's `/ready` fails until the Alembic revision equals head,
so no api pod receives traffic before the schema is current, and worker /
detector simply retry until the DB is ready. For an explicit gate, apply the
Job first:

```bash
kubectl kustomize deploy/kubernetes/overlays/production | kubectl apply -f - -l app.kubernetes.io/component=migrate
kubectl -n aegis wait --for=condition=complete --timeout=10m job/aegis-migrate
kubectl apply -k deploy/kubernetes/overlays/production
```

Job specs are immutable: before re-applying with a new image tag either let
`ttlSecondsAfterFinished: 600` collect the old Job or
`kubectl -n aegis delete job aegis-migrate --ignore-not-found`.

### Production overlay contents

* `AEGIS_ENVIRONMENT=production`, `AEGIS_API_AUTH_MODE=api_key`, OTEL on.
* Namespace enforces the `restricted` Pod Security Standard.
* api 3 replicas (HPA 3-12 on CPU 70 %), worker 3, PDBs `minAvailable: 2`,
  zone topology spread, larger resource requests/limits.
* Ingress with TLS (`cert-manager.io/cluster-issuer`), forced HTTPS redirect,
  50 rps limit; SSE-friendly nginx timeouts/buffering.
* NetworkPolicy: ingress denied by default; api reachable from the
  `ingress-nginx` namespace, the web pods and the `monitoring` namespace; web
  from `ingress-nginx`; worker/detector metrics from `monitoring`. Adjust the
  namespace labels in `base/networkpolicy.yaml` if yours differ. Egress is open
  (LLM API, managed data stores).

## Monitoring (optional)

Requires the Prometheus Operator CRDs (kube-prometheus-stack):

```bash
kubectl apply -k deploy/kubernetes/monitoring
```

Scrapes api `/metrics` (ServiceMonitor), worker `:9464` and detector `:9465`
(PodMonitors). The `release: kube-prometheus-stack` label in
`monitoring/kustomization.yaml` must match your Prometheus'
`serviceMonitorSelector`/`podMonitorSelector`.

## Validation

```bash
# render (no cluster needed)
kubectl kustomize deploy/kubernetes/overlays/dev
kubectl kustomize deploy/kubernetes/overlays/production
kubectl kustomize deploy/kubernetes/monitoring

# server-side dry run against a real cluster (validates schemas + admission)
kubectl apply -k deploy/kubernetes/overlays/production --dry-run=server

# offline schema validation
kubectl kustomize deploy/kubernetes/overlays/production | kubeconform -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

# diff before applying
kubectl diff -k deploy/kubernetes/overlays/production
```

## Operations cheatsheet

```bash
kubectl -n aegis get pods -L app.kubernetes.io/component
kubectl -n aegis logs deploy/worker -f
kubectl -n aegis logs job/aegis-migrate
kubectl -n aegis port-forward svc/api 8600:8600 && curl -s localhost:8600/ready | jq
kubectl -n aegis rollout restart deploy/api          # e.g. after rotating aegis-secrets
kubectl -n aegis exec deploy/api -- aegis check      # dependency + readiness report
```
