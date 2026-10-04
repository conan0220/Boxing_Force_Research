# IMU 拳擊力量資料收集系統規格

## 專有名詞

| 名詞 | 定義 |
|---|---|
| IMU | 慣性量測單元（Inertial Measurement Unit）。本系統至少使用三軸加速度、三軸角速度，並保存裝置可提供的磁力計與 Quaternion。 |
| 無線接收器 | 連接 Windows COM Port，接收同一個 Group 下多顆無線 IMU Node 資料的裝置。 |
| Port | Windows 序列埠名稱，例如 `COM3`、`COM4`。 |
| Group ID | 無線接收器及其所屬 IMU Nodes 共用的識別碼。 |
| Node ID | 同一 Group 內一顆 IMU 的識別碼。 |
| 佩帶位置 | IMU 在人體或裝備上的安裝位置，例如 `left_wrist`、`right_wrist` 或 `torso`。 |
| Subject Height | 受試者身高，以公分（cm）記錄，欄位名稱為 `subject_height_cm`。 |
| Subject Weight | 受試者體重，以公斤（kg）記錄，欄位名稱為 `subject_weight_kg`。 |
| Trial | 一次完整資料收集，包含固定 5 秒的 IMU 資料及一個由使用者輸入的 Peak Force。 |
| Peak Force | 該次擊打後，力板顯示的最大力量。 |
| kgf | 公斤力（kilogram-force），為力量單位；`1 kgf = 9.80665 N`。 |
| Ground Truth | 與一次 Trial 對應的力板 Peak Force，本系統以 `peak_force_kgf` 保存。 |
| Countdown | 正式錄製前固定 3 秒的就定位時間；Countdown 期間的 IMU 資料不得寫入 Trial CSV。 |
| Recording Clock | 所有 Port 共用的 monotonic clock；正式錄製開始時定義為 `elapsed_us = 0`。 |
| Sensor Frame | 一顆 IMU 在一個取樣時間點產生的一筆已解析資料。 |
| Raw IMU CSV | 一顆 IMU 在一次 Trial 中的原始已解析時序資料；不包含濾波、重採樣或特徵工程。 |
| Trial Config | 使用者在執行腳本前自行撰寫的 `.cfg`，定義 Port、Group ID、Node ID 與佩帶位置。 |
| Complete Trial | 完成 5 秒錄製、所有設定來源皆至少收到一筆 Frame，且成功保存有效 Peak Force 的 Trial。 |
| Invalid Trial | 已保存但有來源缺少 Frame、錄製錯誤或其他品質問題的 Trial；不得直接用於模型訓練。 |

## 1. 目標與範圍

### 1.1 目前目標

目前階段的唯一主要目標是建立可重現的資料收集流程。系統 MUST 在 Windows PowerShell 中執行，依使用者提供的 Trial Config 同時收集一個或多個無線接收器的指定 IMU Nodes，並將一次 5 秒錄製與一個 `peak_force_kgf` Ground Truth 綁定成一個 Trial。

### 1.2 延後事項

下列事項不屬於目前版本：

- 選擇、訓練或部署機器學習／深度學習模型。
- 自動偵測拳擊事件或自動切割 Punch Window。
- 從力板直接讀取完整力量曲線。
- 自動從力板取得 Peak Force；目前由使用者手動輸入。
- 濾波、插值、重採樣、正規化或特徵工程。
- 即時力量推論。

Raw Data MUST 保持不可變，讓未來可以在不重新收集資料的前提下比較不同模型與前處理方法。

## 2. 命令列介面

### 2.1 執行方式

使用者 MUST 在 Windows PowerShell 中執行：

```powershell
python .\record_trial.py --config .\configs\example.cfg
```

`--config` MUST 是必要參數，且 MUST 指向副檔名為 `.cfg` 的現有檔案。腳本不得要求使用者透過其他必要命令列參數描述 IMU 拓撲。

### 2.2 啟動前驗證

倒數開始前，腳本 MUST：

1. 讀取並驗證 `.cfg`。
2. 確認至少定義一個 Receiver 及一個 Node。
3. 確認每個 Port 只出現一次。
4. 確認同一 Receiver 內 Node ID 不重複。
5. 確認 Group ID、Node ID 與 baud rate 為有效整數。
6. 開啟所有設定的 COM Ports。

任何必要設定無效或 Port 無法開啟時，腳本 MUST 在 Countdown 前失敗，不得建立一個看似成功的 Trial。

同一 Trial 中不同 Receiver SHOULD 使用不同 Group ID。本研究直接假設使用者會遵守此限制；實作 MAY 額外拒絕重複 Group ID，以防止來源識別不明。

## 3. Trial Config 格式

### 3.1 格式

`.cfg` MUST 使用 INI 語法及 UTF-8 編碼。

```ini
[recording]
output_root = data
subject_id = subject_001
subject_height_cm = 175
subject_weight_kg = 70
session_id = session_001
default_baud_rate = 921600

[receiver.COM3]
group_id = 1
node.0 = left_wrist
node.1 = right_wrist

[receiver.COM4]
group_id = 2
baud_rate = 921600
node.0 = torso
```

### 3.2 `[recording]`

| Key | 必要 | 預設值 | 說明 |
|---|---:|---|---|
| `output_root` | 否 | `data` | Dataset 根目錄；相對路徑以執行腳本時的工作目錄為基準。 |
| `subject_id` | 否 | 空字串 | 匿名受試者 ID。 |
| `subject_height_cm` | 是 | 無 | 受試者身高，單位 cm；必須是有限且大於 0 的數值。 |
| `subject_weight_kg` | 是 | 無 | 受試者體重，單位 kg；必須是有限且大於 0 的數值。 |
| `session_id` | 否 | 空字串 | 使用者定義的 Session ID。 |
| `default_baud_rate` | 否 | `921600` | Receiver 未個別設定時使用的 baud rate。 |

Countdown 固定為 3 秒、正式錄製固定為 5 秒，不由 `.cfg` 覆寫。

### 3.3 `[receiver.<PORT>]`

每個 Receiver MUST 使用一個 `[receiver.<PORT>]` section，例如 `[receiver.COM3]`。

| Key | 必要 | 說明 |
|---|---:|---|
| `group_id` | 是 | 該 Port 無線接收器的 Group ID，範圍 `0..255`。 |
| `baud_rate` | 否 | 覆寫 `default_baud_rate`。 |
| `node.<NODE_ID>` | 至少一個 | 值為該 Node 的佩帶位置；Node ID 範圍 `0..15`。 |

腳本 MUST 只保存 Config 中明確列出的 Group ID 與 Node IDs。相同 Port 收到的其他 Group 或 Node 資料 MUST 被忽略。

## 4. 錄製流程

### 4.1 狀態流程

```text
載入 Config
    -> 驗證 Config
    -> 開啟所有 Ports
    -> 清除 Serial Input Buffer
    -> Countdown 3 秒
    -> 再次清除 Serial Input Buffer
    -> 建立共用 Recording Clock
    -> 同時錄製所有來源 5 秒
    -> 自動停止並關閉 Ports
    -> 使用者輸入 Peak Force (kgf)
    -> 寫入 Ground Truth 與 Trial Metadata
    -> 更新 Dataset Manifest
```

### 4.2 Countdown

腳本 MUST 顯示 `3`、`2`、`1` 的就定位倒數。Countdown Frames MUST NOT 寫入 CSV。倒數完成後，所有 Port MUST 以同一個 host Recording Clock 開始計時。

### 4.3 正式錄製

- 正式錄製時間 MUST 為 5 秒。
- 腳本 MUST 使用 monotonic clock 判斷錄製期限，不得依賴可被系統時間校正影響的 wall clock。
- 所有 IMU CSV 的 `elapsed_us` MUST 使用同一個錄製起點。
- 每個設定的 IMU Node MUST 寫入獨立 CSV。
- 每份 CSV 的 `sample_index` MUST 從 0 開始逐列遞增。
- 一個 Serial read 同時解析出多個 Nodes 時，各 Node可使用相同的 host observation `elapsed_us`。
- `device_time_ms` MUST 優先保存 Gateway timestamp。

### 4.4 Peak Force 輸入

5 秒錄製結束並關閉 Ports 後，腳本 MUST 顯示：

```text
Enter peak force (kgf):
```

輸入 MUST 是有限且大於 0 的十進位數值。無效輸入 MUST 顯示原因並要求重新輸入，不得默認為 0。

### 4.5 完整性判定

若所有設定的 Nodes 均至少收到一筆 Frame，且 Peak Force 有效，Trial `quality_status` MUST 為 `complete`。

若任一設定來源沒有 Frame、錄製期間發生錯誤或實際錄製時間顯著不足，資料 MAY 保存以利除錯，但 `quality_status` MUST 為 `invalid`，並在 `quality_flags` 列出原因。

## 5. Data Path

```text
*.cfg
  -> Config Validator
  -> Serial Port Preflight
  -> 3-second Countdown
  -> 5-second Multi-port Capture
  -> ANROT Frame + CRC Validation
  -> Group/Node Filtering
  -> One Raw IMU CSV per Node
  -> Manual peak_force_kgf Input
  -> ground_truth.json + trial.json
  -> dataset_manifest.csv
```

資料關聯為：

```text
一個 Trial
  ├─ 一或多份 Raw IMU CSV
  └─ 一個 peak_force_kgf Ground Truth
```

## 6. 輸出目錄

每次執行 MUST 建立新的 `trial_id`，不得覆寫既有 Trial：

```text
<output_root>/
├─ dataset_manifest.csv
└─ trials/
   └─ <UTC timestamp>_<trial-id-prefix>/
      ├─ recording.cfg
      ├─ trial.json
      ├─ ground_truth.json
      └─ imu/
         ├─ imu_COM3_G1_N0.csv
         ├─ imu_COM3_G1_N1.csv
         └─ imu_COM4_G2_N0.csv
```

Port、Group ID、Node ID、佩帶位置及 CSV 路徑 MUST 同時記錄在 `trial.json`。佩帶位置不必放入檔名，因此可安全使用中文或其他 Unicode 文字。

## 7. Raw IMU CSV

### 7.1 編碼與欄位

CSV MUST 使用 UTF-8、逗號分隔、`.` 作為小數點，Header 只出現一次。欄位順序固定為：

```text
sample_index,
elapsed_us,
device_time_ms,
acc_x_g,acc_y_g,acc_z_g,
gyro_x_dps,gyro_y_dps,gyro_z_dps,
mag_x_ut,mag_y_ut,mag_z_ut,
quat_w,quat_x,quat_y,quat_z
```

| 欄位 | 型別 | 必要 | 說明 |
|---|---|---:|---|
| `sample_index` | int | 是 | 該 CSV 從 0 開始逐列增加。 |
| `elapsed_us` | int | 是 | 共用 Recording Clock 起點後的微秒數，不得倒退。 |
| `device_time_ms` | int | 否 | Gateway／IMU 提供的毫秒時間。 |
| `acc_x_g..acc_z_g` | float | 是 | Body-frame 三軸加速度，g。 |
| `gyro_x_dps..gyro_z_dps` | float | 是 | Body-frame 三軸角速度，deg/s。 |
| `mag_x_ut..mag_z_ut` | float | 否 | 三軸磁場，µT。 |
| `quat_w..quat_z` | float | 否 | Quaternion，WXYZ 順序。 |

裝置未提供的選填值 MUST 留白，不得填入假造的 0。CSV 不重複保存 Port、Group ID、Node ID 或佩帶位置；這些靜態資料保存於 `trial.json`。

### 7.2 HI221 `0x63` 單位

對 HI221 Gateway compact `0x63` payload：

- Acceleration：raw `int16 / 1000`，單位 g。
- Gyroscope：raw `int16 / 10`，單位 deg/s。
- Magnetometer：raw `int16 / 10`，單位 µT。
- Quaternion：raw `int16 / 32767`，WXYZ。
- 多位元數值使用 little-endian。

只有通過 ANROT CRC-16 驗證的完整 Frame 才能寫入 CSV。

## 8. Ground Truth JSON

`ground_truth.json` MUST 使用 UTF-8，格式如下：

```json
{
  "schema_version": 1,
  "trial_id": "uuid",
  "peak_force_kgf": 42.5,
  "peak_force_n": 416.782625,
  "measurement_method": "manual",
  "recorded_at": "ISO-8601 UTC timestamp"
}
```

`peak_force_n` MUST 依下式產生：

```text
peak_force_n = peak_force_kgf * 9.80665
```

模型未來的原始 Ground Truth 欄位為 `peak_force_kgf`；Newton 欄位只是固定換算值。

## 9. Trial Metadata JSON

`trial.json` MUST 至少保存：

```json
{
  "schema_version": 1,
  "trial_id": "uuid",
  "subject_id": "optional",
  "subject_height_cm": 175.0,
  "subject_weight_kg": 70.0,
  "session_id": "optional",
  "quality_status": "complete | invalid | aborted",
  "quality_flags": [],
  "countdown_seconds": 3,
  "requested_recording_seconds": 5,
  "actual_recording_seconds": 5.0,
  "recording_started_at": "ISO-8601 UTC timestamp",
  "recording_ended_at": "ISO-8601 UTC timestamp",
  "sources": [
    {
      "port": "COM3",
      "group_id": 1,
      "node_id": 0,
      "wear_location": "left_wrist",
      "baud_rate": 921600,
      "csv_path": "imu/imu_COM3_G1_N0.csv",
      "row_count": 2000
    }
  ]
}
```

腳本 SHOULD 另外保存每個 CSV 的檔案大小與 SHA-256，以便後續檢查資料完整性。

## 10. Dataset Manifest

`dataset_manifest.csv` MUST 一列代表一個 Trial，至少包含：

```text
trial_id,subject_id,subject_height_cm,subject_weight_kg,session_id,recorded_at,trial_path,
peak_force_kgf,peak_force_n,quality_status
```

`trial_path` MUST 使用相對於 `output_root` 的路徑。Manifest 不展開每顆 IMU 的 CSV；完整來源清單由該 Trial 的 `trial.json` 提供。

## 11. 失敗與中斷

- Config validation 或 Port preflight 失敗時 MUST 回傳非零 exit code。
- 錄製期間發生錯誤時 MUST 關閉所有已開啟 Ports 與檔案。
- 使用者在 Countdown 或錄製期間按下 `Ctrl+C` 時，Trial MUST 標示為 `aborted`，且腳本 MUST 安全關閉資源。
- 已寫入的部分資料 MAY 保留，但不得標示為 `complete`。
- 寫檔 SHOULD 先使用 `.part` 暫存檔，完成後再原子化更名為 `.csv`。
- 成功結束時 exit code MUST 為 0；Invalid／Aborted Trial 或系統錯誤 MUST 回傳非零 exit code。

## 12. 驗收條件

1. 未提供 `--config` 時，CLI 拒絕執行。
2. Config 可定義多個 Ports、每個 Port 一個 Group ID，以及多個 Node-to-location mappings。
3. 所有 Ports 在 3 秒 Countdown 前開啟。
4. Countdown 資料不會出現在 CSV。
5. 正式錄製自動持續 5 秒後停止。
6. 每顆設定 Node 產生一份符合固定 schema 的 CSV。
7. 所有 CSV 使用同一個 `elapsed_us` 時間起點。
8. 錄製結束後要求使用者輸入正數 Peak Force（kgf）。
9. Trial Config、Ground Truth、來源對應、品質狀態與資料檔皆可追溯。
10. 不同 Trial 不會互相覆寫。
