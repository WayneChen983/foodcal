# 雲端 GPU 推論服務部署報告

> **專案**：基於 3D 重建與語意分割之食物熱量估算系統（foodcal）  
> **部署平台**：RunPod GPU Cloud  
> **文件用途**：論文第五章（5.2、5.4）與實驗部署之技術報告  
> **最後更新**：2026-07-04

---

## 1. 摘要

本系統採**行動端—雲端協同**架構：使用者以手機拍攝三視角餐點影像並上傳，雲端 GPU 伺服器依序執行 Qwen2.5-VL 食物辨識、SAM3 食材分割、DUSt3R 多視角三維重建，再換算體積與營養素後以 JSON 回傳。由於三個模型合計需占用大量 GPU 顯示記憶體，且單次推論耗時較長，不適合在行動裝置端即時執行，因此選擇**按秒計費之雲端 GPU 租用服務**承載推論。

本報告記錄平台選型、硬體規格、部署流程、計費模式、環境建置腳本與後續待完成項目。

---

## 2. 系統架構概覽

### 2.1 行動端與雲端分工

| 端點 | 職責 |
|------|------|
| **行動端（手機）** | 多視角拍攝、影像壓縮與上傳、顯示分析進度、呈現營養報告 |
| **雲端 GPU 推論服務** | Qwen2.5-VL 辨識 → SAM3 分割 → DUSt3R 三維重建 → 體積與營養計算 |

### 2.2 資料流

```
手機拍攝三張影像（＋比例尺卡片）
        │
        ▼  POST /analyze（上傳影像）
雲端 GPU 推論管線
        │
        ▼  回傳營養報告（JSON）
手機呈現各食材與全餐營養明細
```

對應論文圖：**圖 4-2**（行動端與雲端協同架構）、**圖 5-2**（上傳與傳輸時序）、**圖 5-4**（推論流程與 GPU 記憶體管理）。

---

## 3. 平台選型

### 3.1 候選方案比較

| 平台 | 優點 | 缺點 | 本專案結論 |
|------|------|------|------------|
| **AWS EC2（G 系列）** | 穩定、企業級 SLA、可整合其他 AWS 服務 | 按小時計費、設定複雜、閒置成本高 | 適合正式上線，但初期成本較高 |
| **RunPod** | 按秒計費、GPU 選擇多、啟停彈性高、Network Volume 可持久化 | 非傳統 IaaS、供給隨供需波動 | **本研究採用** |
| **Vast.ai** | 價格常更低 | 節點穩定性與供給不確定、較偏研究／個人用途 | 可作成本實驗，不建議作主要部署 |
| **Lambda Labs** | 介面簡潔 | 常缺貨、區域選擇少 | 備選 |

### 3.2 選擇 RunPod 的理由

1. **按秒計費（billed per-second）**：本系統屬間歇性推論（使用者拍完才上傳），用畢可 Terminate Pod，僅支付實際運算時間。
2. **Network Volume 持久化**：模型權重（Qwen2.5-VL、SAM3、DUSt3R）合計約數十 GB，可存於 `/workspace`，關閉 Pod 後下次啟動無需重新下載。
3. **中階 GPU 供給充足**：24–32 GB 顯存之 L4、RTX 4090、RTX PRO 4500、RTX 5090 等皆可執行本管線。
4. **適合論文實驗階段**：開發、測試、量測推理時間與成本時，可隨開隨關，控制預算。

---

## 4. 部署配置（實際採用）

### 4.1 RunPod Pod 設定

| 項目 | 設定值 |
|------|--------|
| 平台 | RunPod GPU Cloud（On-Demand） |
| 資料中心區域 | **EU-RO-1**（Pod 與 Network Volume 需同區） |
| 容器映像 | Runpod PyTorch（Ubuntu 22.04，CUDA 12.x） |
| GPU | **NVIDIA RTX PRO 4500**（32 GB GDDR7）※主要實驗 |
| 系統 RAM | 62 GB |
| vCPU | 12 核 |
| 持久化儲存 | **80 GB Network Volume**，掛載至 `/workspace` |
| 計費方式 | 按秒計費；Pod 執行中即開始計費，Terminate 後停止 |

### 4.2 軟體環境

| 項目 | 版本／說明 |
|------|------------|
| 作業系統 | Ubuntu 22.04 LTS |
| Python | 3.12 |
| PyTorch | 2.7.0（CUDA 12.6） |
| 視覺語言模型 | Qwen2.5-VL-7B-Instruct |
| 分割模型 | SAM3（facebook/sam3，需 HuggingFace 存取權） |
| 三維重建模型 | DUSt3R（ViTLarge, 512, dpt） |
| Conda 環境名稱 | `foodcal` |

### 4.3 GPU 最低需求

本管線推論尖峰時，顯示記憶體主要由 **Qwen2.5-VL-7B** 與 **DUSt3R** 主導。實務建議：

- **最低**：24 GB VRAM（如 NVIDIA L4、RTX 4090）
- **建議（本研究）**：32 GB VRAM（RTX PRO 4500、RTX 5090），記憶體餘裕較大、較不易 OOM
- **過剩**：A100 80GB、RTX PRO 6000 96GB — 可執行但成本偏高，就本應用而言運算能力過剩

---

## 5. GPU 規格與費用比較

以下為 RunPod On-Demand **參考價**（2026 年初，實際費用隨供需波動）：

| GPU 型號 | 架構 | 顯示記憶體 | 系統 RAM | 每小時費用 (US$) | 是否足以執行 | 定位／備註 |
|----------|------|------------|----------|------------------|--------------|------------|
| NVIDIA L4 | Ada | 24 GB | — | 約 0.40–0.79 | 足夠 | 成本最低，速度較慢 |
| RTX 4090 | Ada | 24 GB | — | 約 0.60 | 足夠 | 性價比高，常缺貨 |
| **RTX PRO 4500** | Blackwell | 32 GB | 62 GB | **約 0.74** | **足夠（本研究採用）** | 供貨穩定、成本適中 |
| RTX 5090 | Blackwell | 32 GB | 60 GB | 約 0.99 | 足夠 | 速度最快之消費級卡 |
| RTX PRO 6000 | Blackwell | 96 GB | 283 GB | 約 2.09 | 過剩 | 大顯存，成本偏高 |
| NVIDIA A100 | Ampere | 80 GB | — | 约 1.40–2.10 | 過剩 | 資料中心級，適合訓練 |

### 5.1 單次推論成本估算

RunPod 按秒計費，單次推論成本公式：

```
單次推理成本 (US$) = 推理時間 (秒) ÷ 3600 × 每小時租用費用 (US$)
```

**範例**（假設完整 pipeline 推理 60 秒、RTX PRO 4500 @ $0.74/hr）：

```
60 ÷ 3600 × 0.74 ≈ US$ 0.012（約每次 1.2 美分）
```

**各 GPU 單次成本估算（推理 60 秒）**：

| GPU 型號 | 每小時費用 (US$) | 單次成本 (60 秒) |
|----------|------------------|------------------|
| NVIDIA L4 | 0.40–0.79 | ≈ $0.007–$0.013 |
| RTX 4090 | 0.60 | ≈ $0.010 |
| **RTX PRO 4500** | **0.74** | **≈ $0.012** |
| RTX 5090 | 0.99 | ≈ $0.017 |
| RTX PRO 6000 | 2.09 | ≈ $0.035 |
| NVIDIA A100 | 1.40–2.10 | ≈ $0.023–$0.035 |

> 推理時間採 60 秒作報告估算；實測後可更新論文表 6-9。

### 5.2 計費注意事項

| 行為 | 是否計費 |
|------|----------|
| Pod **Running**（即使閒置未跑推論） | ✅ 是，持續按秒計費 |
| Pod **Terminated** | ❌ 否（GPU 計費停止） |
| Network Volume 掛載中 | ✅ 是，磁碟按容量與時間另計（通常遠低於 GPU） |
| 僅下載模型、未開 GPU Pod | 視是否掛載 Volume 而定 |

**省錢建議**：實驗或開發結束後在 RunPod 控制台 **Terminate Pod**，勿僅 Stop 或閒置放著；模型與 conda 環境已存於 Network Volume，下次開機可快速恢復。

---

## 6. 部署流程

### 6.1 前置準備

1. **RunPod 帳號**與付款方式
2. **建立 80 GB Network Volume**（區域：EU-RO-1）
3. **HuggingFace 帳號**並至 [facebook/sam3](https://huggingface.co/facebook/sam3) 申請 **Request access**（通常數小時內核准）
4. 產生 HuggingFace **Access Token**（`hf_xxxx`），**勿公開分享**

### 6.2 建立 Pod

1. 選擇 **GPU Cloud → Pods → Deploy**
2. 選 GPU：**RTX PRO 4500**（或表 5-3 其他 24GB+ 規格）
3. 選 Template：**Runpod PyTorch**
4. 掛載 Network Volume 至 **`/workspace`**
5. 確認區域與 Volume 一致（EU-RO-1）
6. 部署後進入 **Web Terminal**

### 6.3 一鍵安裝（推薦）

在 Web Terminal 執行（先替換 token）：

```bash
export HF_TOKEN="hf_你的token"
curl -fsSL https://raw.githubusercontent.com/WayneChen983/foodcal/main/scripts/runpod_bootstrap.sh | bash
```

若 repo 已在 `/workspace/foodcal`：

```bash
export HF_TOKEN="hf_你的token"
bash /workspace/foodcal/scripts/runpod_bootstrap.sh
```

### 6.4 安裝腳本做了什麼

`scripts/runpod_bootstrap.sh` → 呼叫 `scripts/cloud_setup.sh`，共 7 步：

| 步驟 | 內容 |
|------|------|
| 0 | 檢查 `nvidia-smi`、列出 GPU 資訊 |
| 1 | 安裝系統套件（git、build-essential、OpenCV 依賴等） |
| 2 | 安裝 Miniconda 至 `/workspace/miniconda3`（持久化） |
| 3 | 建立 conda 環境 `foodcal`、安裝 PyTorch 2.7 + CUDA 12.6 |
| 4 | 安裝 SAM3（`pip install -e .`） |
| 5 | 安裝 DUSt3R 與 pipeline 依賴（transformers、qwen-vl-utils 等） |
| 6 | HuggingFace 登入（使用 `HF_TOKEN`） |
| 7 | 寫入 `~/.foodcal_env`、執行 import smoke test |

**快取目錄**（皆指向 `/workspace`，關 Pod 仍保留）：

- `HF_HOME` / `TRANSFORMERS_CACHE` → `/workspace/.cache/huggingface`
- `TORCH_HOME` → `/workspace/.cache/torch`
- `PIP_CACHE_DIR` → `/workspace/.cache/pip`

**環境變數**（`~/.foodcal_env`）：

```bash
export FOODCAL_DIR="/workspace/foodcal"
export SAM3_ROOT="/workspace/foodcal"
export DUSTER_ROOT="/workspace/foodcal/duster"
export HF_HOME="/workspace/.cache/huggingface"
export TRANSFORMERS_CACHE="/workspace/.cache/huggingface"
```

### 6.5 驗證與執行範例

```bash
source ~/.foodcal_env
conda activate foodcal
cd /workspace/foodcal
bash scripts/run_example.sh
```

或直接：

```bash
python master_pipeline.py
```

範例使用 repo 內 `1001.jpg`、`1002.jpg`、`1003.jpg`；完成後檢查 `master_report_*.json`。

### 6.6 上傳自訂影像

```bash
scp 1001.jpg 1002.jpg 1003.jpg ubuntu@YOUR_POD_IP:/workspace/foodcal/
```

---

## 7. 推論管線與 GPU 記憶體管理

### 7.1 管線階段

```
三視角影像
  → Qwen2.5-VL：食物辨識與邊界框定位
  → SAM3：依邊界框進行像素級分割
  → DUSt3R：多視角三維點雲重建
  → 比例尺錨定（信用卡 ISO/IEC 7810 ID-1）＋平面擬合＋厚度積分
  → 體積計算 → 營養資料庫匹配 → JSON 報告
```

### 7.2 記憶體管理策略

三個模型無法同時常駐顯存。雲端服務設計為：

1. **依序載入**：完成該階段推論後釋放模型權重與 CUDA cache
2. **半精度推論**：Qwen2.5-VL 以 float16 載入
3. **單 Pod 單請求**：開發／論文實驗階段以循序處理為主；多使用者需水平擴充（多 GPU + 請求佇列）

對應論文圖：**圖 5-4**。

---

## 8. API 設計（規劃）

雲端對外以 HTTP API 供行動端呼叫（FastAPI 為建議實作，論文已規劃規格）：

| API 端點 | 方法 | 輸入 | 輸出 |
|----------|------|------|------|
| `/analyze` | POST | 三張影像、參考視角索引 | 各食材與全餐營養報告（JSON） |
| `/health` | GET | — | 服務健康狀態 |

**回傳 JSON 內容（概念）**：

- 各食材：名稱、體積 (cm³)、熱量 (kcal)、蛋白質／脂肪／碳水化合物 (g)
- 全餐：營養總和
- 未匹配食材：附註說明

> 詳細 request／response schema、驗證機制與錯誤碼：論文 TODO，待 App 開發時補齊。

---

## 9. 流量與擴展建議

| 使用情境 | 建議配置 | 備註 |
|----------|----------|------|
| 單人測試／開發 | 1 × 24GB+ GPU（L4／RTX PRO 4500） | 用畢 Terminate Pod |
| 小規模（數人並行） | 數個 GPU Pod + 請求佇列 | 待實測吞吐量 |
| 中大規模 | Auto-scaling + 負載平衡 | 可評估 RunPod Serverless 或遷移至 AWS |

本管線單次推論時間較長，**吞吐量 = GPU 數量 ÷ 單次推理時間**，瓶頸在 GPU 而非 CPU。

---

## 10. 安全與維運

### 10.1 HuggingFace Token

- 僅在 Pod 環境變數或一次性登入中使用，**勿寫入 git、勿貼至公開聊天**
- 若 token 外洩：立即至 HuggingFace Settings → Access Tokens **撤銷並重新產生**

### 10.2 日常維運檢查清單

- [ ] 實驗結束 → Terminate Pod
- [ ] Network Volume 與 Pod **同區域**
- [ ] SAM3 存取權是否仍有效
- [ ] `source ~/.foodcal_env && conda activate foodcal` 後再跑 pipeline
- [ ] 定期確認 RunPod 帳單與 Volume 容量（80GB 是否足夠）

### 10.3 常見問題

| 問題 | 可能原因 | 處理方式 |
|------|----------|----------|
| `CUDA out of memory` | GPU 顯存不足或前模型未釋放 | 換 32GB GPU；確認管線有釋放 cache |
| SAM3 下載失敗 | 未申請 HF 權限或 token 無效 | 重新申請 access、更新 `HF_TOKEN` |
| 冷啟動很慢 | 首次下載模型 | 使用 Network Volume 快取；第二次起會快很多 |
| Volume 未掛載 | Pod 與 Volume 不同區 | 重建 Pod，選同區 Volume |
| 計費持續增加 | Pod 未 Terminate | 控制台 Terminate，勿閒置 Running |

---

## 11. 與 AWS 等方案的取捨（補充）

若未來要從論文原型走向**正式 App 上線**，可考慮：

| 階段 | 建議 |
|------|------|
| 論文實驗／原型 | **RunPod**（本報告方案）— 成本低、設定快 |
| 小流量 Beta | RunPod 多 Pod + API Gateway，或 AWS EC2 g6/g5 單機 |
| 正式服務 | AWS EC2 Auto Scaling Group + ALB + S3；或 AWS SageMaker Endpoint |

RunPod 的主要缺點：**非企業 SLA、節點可能被搶占（視方案而定）、區域與合規選項較少**；優點是**按秒計費與快速實驗**，非常適合本研究目前階段。

---

## 12. 相關檔案一覽

| 檔案 | 說明 |
|------|------|
| `scripts/runpod_bootstrap.sh` | RunPod 一鍵 clone + 安裝 |
| `scripts/cloud_setup.sh` | 通用雲端 GPU 環境建置 |
| `scripts/run_example.sh` | 以範例圖跑 `master_pipeline.py` |
| `master_pipeline.py` | 完整推論管線（路徑已改為環境變數，支援雲端） |
| `auto_pipeline.py` | 自動化管線變體 |
| `thesis/thesis_gen.py` | 論文 5.2、5.4、6.4.4 章節內容來源 |
| `thesis/figures/fig4_2.png` | 行動端—雲端架構圖 |
| `thesis/figures/fig5_2.png` | API 上傳時序圖 |
| `thesis/figures/fig5_4.png` | 推論流程與 GPU 記憶體管理 |

---

## 13. 待完成項目（與論文實驗銜接）

### 13.1 已完成（2026-07-15 RunPod RTX PRO 4500 實測）

| 項目 | 對應論文 | 結果 |
|------|----------|------|
| 各階段推理耗時（常駐） | 表 6-7 | 合計 **20.61 s**（VLM 10.88 / SAM3 4.48 / DUSt3R 4.31） |
| 冷啟動對照 | 表 6-8 | 合計 **249.42 s**（VLM 首次約 190.7 s） |
| 單次成本（常駐） | 表 6-9 | **US$ 0.0042**（$0.74/hr） |
| 顯存峰值 | 表 6-7 | **約 21.4 GB**（三模型常駐） |
| FastAPI + 模型快取 | 5.2 | `webapp/`、`model_cache.py`、`FOODCAL_KEEP_MODELS=1` |

數據檔：`benchmark_report.json`

### 13.2 仍待完成

| 項目 | 對應論文 | 說明 |
|------|----------|------|
| 單一食材端到端 | 表 6-8 | 以標定單品影像補測 |
| 多 GPU 推理時間比較 | 表 6-9、圖 6-2 | L4／4090／5090 實測 |
| 行動端 App 串接 | 5.1 | 上傳、進度、結果 UI |
| 小／中規模吞吐量 | 表 5-4 | 多 Pod 或佇列之實測數據 |

---

## 14. 結論

本專案之雲端 GPU 部署以 **RunPod + RTX PRO 4500 + 80GB Network Volume（EU-RO-1）** 為主要實驗環境，搭配一鍵安裝腳本將 PyTorch、SAM3、DUSt3R 與 Qwen2.5-VL 依賴集中建置於持久化 `/workspace`，實現「**用時開機、用畢關機、按秒計費**」的間歇性推論模式。在 24GB 以上顯存之中階 GPU 上即可完整執行三階段 AI 管線，無需資料中心級 A100/H100，兼顧**可負擔成本**與**論文可重現性**。

後續工作重點為：於所選 GPU 上實測推理時間與成本、完成 FastAPI 封裝、並與行動端 App 串接，以形成可演示之端到端原型。

---

*本文件由 foodcal 專案部署紀錄整理，供論文撰寫與口試報告使用。*
