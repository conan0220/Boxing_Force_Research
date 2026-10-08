# Boxing Force Research — Data Recorder

目前階段先收集 IMU 與拳擊力板 Peak Force（kgf）資料，不進行模型選擇。

## 安裝

在 Windows PowerShell 中執行：

```powershell
uv sync
```

`uv sync` 會依據 `.python-version` 使用 Python 3.12，在專案下建立 `.venv`，並安裝 `pyserial`。

執行測試：

```powershell
uv run python -m unittest discover -s .\tests -v
```

## 設定

複製並修改 [`configs/example.cfg`](configs/example.cfg)。每個 Receiver section 指定：

- Windows COM Port
- Group ID
- Node ID 與佩帶位置
- 受試者身高（cm）與體重（kg）
- 該 Trial 的出拳手（`punch_hand = left` 或 `right`）

## 錄製一個 Trial

```powershell
uv run python .\record_trial.py --config .\configs\example.cfg
```

腳本會先開啟所有 Ports，倒數 3 秒，錄製 5 秒，然後要求輸入力板顯示的 Peak Force（kgf）。資料預設寫入 `data/`。

完整格式與行為請見 [`spec.md`](spec.md)。

## Training Artifacts

訓練完成的模型與該次訓練的 Metadata 存放於 `training_artifacts/`。原始 IMU 與 Ground Truth 資料仍保留在 `data/`，不得複製到 Training Artifact 資料夾。

### 資料夾架構

第一層依模型類型分類。第二層代表一次獨立的 Training Run：

```text
training_artifacts/
├─ xgboost/
│  └─ 20261008T120000Z_wrist-shoulder-baseline_a1b2c3d4/
│     ├─ model.ubj
│     └─ training.json
├─ cnn/
│  └─ 20261008T130000Z_four-imu-raw-window_b2c3d4e5/
│     ├─ model.onnx
│     └─ training.json
└─ random_forest/
   └─ ...
```

第二層資料夾名稱格式：

```text
<UTC timestamp>_<training特色>_<training-id前8碼>
```

每個 Training Run 資料夾 MUST 包含：

- 一個訓練完成的模型檔案，例如 XGBoost 的 `model.ubj`。
- 一個 `training.json`，記錄 Training Metadata。

### `training.json`

```json
{
  "schema_version": 1,
  "training_id": "a1b2c3d4-...",
  "created_at": "2026-10-08T12:00:00Z",
  "description": "使用 right_wrist 與 left_shoulder 的撞擊區間特徵訓練 XGBoost。",
  "model_file": "model.ubj",
  "git_commit": "...",
  "target": "peak_force_kgf",
  "training_config": {
    "input_locations": [
      "right_wrist"
    ],
    "input_signals": [
      "acc_x_g",
      "acc_y_g",
      "acc_z_g",
      "gyro_x_dps",
      "gyro_y_dps",
      "gyro_z_dps"
    ],
    "feature_set": "right-wrist-impact-stats-v1",
    "hyperparameters": {
      "max_depth": 6,
      "learning_rate": 0.05,
      "n_estimators": 500
    },
    "random_seed": 42
  },
  "data_split": {
    "training": {
      "count": 82,
      "trials": [
        {
          "trial_id": "uuid",
          "path": "data/shouyi_withoutGloves_fourIMU/trials/..."
        }
      ]
    },
    "validation": {
      "count": 10,
      "trials": [
        {
          "trial_id": "uuid",
          "path": "data/shouyi_withoutGloves_fourIMU/trials/..."
        }
      ]
    },
    "testing": {
      "count": 10,
      "trials": [
        {
          "trial_id": "uuid",
          "path": "data/shouyi_withoutGloves_fourIMU/trials/..."
        }
      ]
    }
  },
  "metrics": {
    "validation": {
      "mae_kgf": 3.2,
      "rmse_kgf": 4.1,
      "r2": 0.72
    },
    "testing": {
      "mae_kgf": 3.0,
      "rmse_kgf": 3.8,
      "r2": 0.75
    }
  }
}
```

規則：

- `description` MUST 說明本次 Training 的主要特色。
- `model_file` MUST 指向同一個 Training Run 資料夾內的模型檔案。
- `data_split.training` 與 `data_split.testing` MUST 列出實際使用的 Trial ID 和路徑。
- `count` MUST 等於對應 `trials` 陣列的數量。
- Trial 路徑 MUST 使用相對於 Repository Root 的路徑，不得使用 Windows 絕對路徑。
- Training Artifact MUST NOT 包含複製的 IMU CSV、`ground_truth.json` 或 Trial 資料夾。
- 若訓練使用 Validation Set，MAY 在 `data_split` 加入同格式的 `validation` 欄位。

### 執行 XGBoost Baseline Training

```powershell
uv run python .\train_xgboost.py
```

預設行為：

- 使用 `data/shouyi_withoutGloves_fourIMU` 中的 Complete Trials。
- 僅使用 `right_wrist` 的三軸加速度與三軸角速度。
- 以右手腕合成加速度峰值為中心建立 500 ms 撞擊區間。
- 從完整 5 秒資料和撞擊區間產生 48 個統計特徵。
- 依 Peak Force 分布建立 80% Training、10% Validation、10% Testing Split。
- 將模型與 Metadata 寫入 `training_artifacts/xgboost/<training-run>/`。

CSV 的 `gyro_*_dps` 是角速度，不是角加速度。
