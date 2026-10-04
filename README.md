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

## 錄製一個 Trial

```powershell
uv run python .\record_trial.py --config .\configs\example.cfg
```

腳本會先開啟所有 Ports，倒數 3 秒，錄製 5 秒，然後要求輸入力板顯示的 Peak Force（kgf）。資料預設寫入 `data/`。

完整格式與行為請見 [`spec.md`](spec.md)。
