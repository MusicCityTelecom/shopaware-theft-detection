# Training a ShopAware detector on server2

The beta server2 profile persists:

- exported/extracted datasets: `/var/lib/shopaware/training` → `/app/training-data`
- training runs/checkpoints: `/var/lib/shopaware/runs` → `/app/runs`
- qualified runtime models: `/var/lib/shopaware/models` → `/app/models`

The dashboard Training page stores its curation workspace in SQLite and downloads a reviewed YOLO ZIP to the browser. Move that ZIP to server2 before running the explicit trainer.

## Import an exported ZIP

From your workstation:

```bash
scp ~/Downloads/shopaware-*.zip installer@server2:/tmp/shopaware-dataset.zip
```

On server2:

```bash
sudo mkdir -p /var/lib/shopaware/training/camera
sudo rm -rf /var/lib/shopaware/training/camera/*
sudo python3 -m zipfile -e /tmp/shopaware-dataset.zip /var/lib/shopaware/training/camera
sudo rm -f /tmp/shopaware-dataset.zip
```

The extracted directory must contain `data.yaml`, `manifest.json`, `images/` and `labels/`.

## Validate without training

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml run --rm --no-deps shopaware \
  python -m tools.train_camera --data /app/training-data/camera/data.yaml --check-only
```

Do not continue if the checker reports missing/corrupt files, invalid labels, duplicate content or split leakage.

## CPU training

Stop the live analyzer first so training does not compete with real-time inference:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml stop shopaware dashboard
```

Run training:

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml run --rm --no-deps shopaware \
  python -m tools.train_camera \
  --data /app/training-data/camera/data.yaml \
  --device cpu \
  --epochs 50 \
  --batch 4
```

Training outputs persist under `/var/lib/shopaware/runs/shopaware` even after the temporary container exits.

Restart ShopAware when training is finished:

```bash
docker compose --env-file .env -f deploy/server2/docker-compose.yml up -d
```

CPU training can be very slow. A separate NVIDIA training machine is preferred for serious datasets.

## NVIDIA training

Only use a GPU container after the NVIDIA driver, Container Toolkit and PyTorch CUDA runtime are verified. Then use the GPU compose profile documented in `docs/DEPLOYMENT.md` and pass `--device 0` instead of `--device cpu`.

## Qualify before activation

The trainer does **not** activate a new model. Review its held-out test report and compare the custom detector with `yolo26n.pt` on the same independent scenes. Also stage normal customer activity and concealment-like behavior through the full ShopAware incident pipeline.

When a checkpoint is accepted, copy it to the persistent model directory:

```bash
sudo cp /var/lib/shopaware/runs/shopaware/<run>/weights/best.pt \
  /var/lib/shopaware/models/shopaware-retail-beta.pt
sudo chmod 0644 /var/lib/shopaware/models/shopaware-retail-beta.pt
```

In **Settings → Detection model**, set:

```text
/app/models/shopaware-retail-beta.pt
```

Keep the pose model as `yolo26n-pose.pt`, keep `SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false`, save settings and restart the backend:

```bash
cd /opt/shopaware
docker compose --env-file .env -f deploy/server2/docker-compose.yml restart shopaware
```

If the custom detector performs worse, roll back the detection model to `yolo26n.pt` and restart.

A detector trained this way improves object detection only. It does not by itself learn theft intent or temporal activity recognition.
