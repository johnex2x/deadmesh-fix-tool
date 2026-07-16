# DeadMesh Fix Tool

**自動修復 [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829)（作者 TesFantom）掃出的 mesh 碰撞問題。**

> [English README](README.md)

DeadMesh 能揪出 Skyrim SE/AE/VR `.nif` 檔裡損壞的 Havok collision，也就是那些會讓遊戲當機、凍結，或狂吃 FPS 的元凶，但它本身不負責修復。**DeadMesh Fix Tool 是非官方的輔助工具，接手把修復這一步自動做完**，你不用再為每個被抓到的 mesh 手動跑一次 Blender、PyNifly、NifSkope 那套流程。

這是非官方的搭配工具，並非 TesFantom 製作或背書。

## 修得了哪些問題

| DeadMesh 判定結果 | 工具的處理方式 |
|---|---|
| **CRASH RISK / HANG RISK / BROKEN COLLISION（當機風險 / 損壞的 MOPP）** | 從完好的碰撞幾何重建損壞的 MOPP 搜尋樹。幾何本身完全不會動，MOPP 區塊以外的每個 byte 都維持原樣。 |
| **HEAVY / VERY HEAVY COLLISION（過重碰撞網格）** | 對過密的碰撞 mesh 做減面，依材質分組處理並保持外形，再重建 chunk 與 MOPP。減面強度可選（保守 / 一般 / 積極）。 |
| **DEGENERATE COLLISION（退化碰撞三角形）** | 移除零面積、退化的碰撞三角形，重建 MOPP。 |
| **INVERTED COLLISION（反向碰撞面）** | 翻正反向的碰撞殼環繞順序——就是那種會害玩家掉出模型外的缺陷。無法明確判斷的情況一律拒修，不會用猜的。 |
| **孤立碰撞區塊** | 在能確保安全的前提下，移除未被引用的殘留碰撞區塊；否則會提示你到 NifSkope 手動處理。 |
| **ORPHAN MOPP** | 無法自動修復，因為檔案裡的幾何已經被移除，只會回報需要手動處理。 |

## 安全保證

1. **原始檔案絕不會被改動。** 修好的 mesh 會以 loose file 的形式，寫進獨立的輸出資料夾（預設是 `<mod>\DeadMesh-Fixed\Meshes`），並保留 `meshes\...` 以下的相對路徑結構，方便你直接丟進 Data 資料夾或 mod 管理器裡。BSA 封存檔只會被讀取，絕不會被改寫或重新打包。
2. **由 DeadMesh 判定成敗，不是我們說了算。** 每次修復完成後，工具都會用 DeadMesh 自己的 `dmscan` 引擎把結果重新掃一遍。只有原本的缺陷確實消失、而且沒有任何其他項目變差（沒有新增翻面、沒有超出容許範圍的新破洞、也沒有新的當機風險，其中也包含 dmscan 的 `--vs` 環繞順序回歸檢查），才會真正寫出檔案。
3. **失敗就不輸出。** 任何無法被證明安全的修復結果，一律不會寫出；報告裡會列出失敗原因，並附上手動修復的建議做法。

## 系統需求

- Windows 10/11，64 位元
- 已安裝 [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829)（工具第一次執行時會請你指定它的安裝資料夾，因為需要用到裡面的 `dmscan.exe`）

## 使用方式（GUI）

1. 執行 `DeadMeshFixTool.exe`，第一次執行時請指定你的 DeadMesh 資料夾位置。
2. 選擇要掃描的 mod 資料夾（loose file 和 BSA 封存檔都支援，且 loose file 會覆蓋同名的 BSA 檔案，跟遊戲本身的行為一致）。
3. 勾選要修復的類別（預設全開），並選擇減面強度。
4. 按下 **Scan** 掃描，檢查結果列表後，再按 **Fix** 執行修復。
5. 輸出資料夾裡的每個檔案都已通過驗證。修復失敗的項目會連同原因列在結果表格裡，也會寫進 `deadmesh-fix-report.txt` / `.json` 報告檔中。

## 使用方式（命令列）

同一個資料夾裡的 `dmfix.exe` 是命令列版本（`DeadMeshFixTool.exe` 則是視窗化的 GUI 版本，不會對終端機輸出任何內容）：

```
dmfix.exe <mod 資料夾> [--deadmesh <資料夾>] [--out <資料夾>]
          [--fix crash,heavy,degenerate,inverted,orphan_blocks]
          [--strength conservative|normal|aggressive] [--no-bsa]
```

退出碼：0 代表所有能修的都修好了；1 代表有檔案修復失敗或無法修復；2 代表使用方式錯誤。

## 工具拒絕修復時的手動處理方式

工具的「拒修清單」其實就是你走傳統手動流程的待辦事項：

1. 用 [PyNifly 附加元件](https://github.com/BadDogSkyrim/PyNifly) 把 `.nif` 匯入 Blender。
2. 修正或重建碰撞幾何（碰撞形狀只要大致貼合外形即可，低面數才是正確做法，不要拿算圖用的高面數 mesh 當碰撞）。
3. 指定正確的 `SKY_HAV_MAT_*` vertex group，再用 PyNifly 匯出。
4. 用 NifSkope 檢查比對，最後用 DeadMesh 驗證一次。

## 從原始碼建置

```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m PyInstaller build.spec
```

測試用的 `.nif` fixture 檔案**沒有包含在這個 repo 裡**，因為那些是第三方 mod 的美術資產，我們沒有重新散布的權利。請自行準備測試用的 mesh 放進 `tests/fixtures/`；另外，執行測試時 `dmscan.exe` 必須存在於同層的 `DeadMesh - MOPP Collision Validator` 資料夾裡：

```
.venv\Scripts\python tests\test_mopp_rebuild.py
.venv\Scripts\python tests\test_simplify.py
.venv\Scripts\python tests\test_other_fixes.py
.venv\Scripts\python tests\test_bsa.py
.venv\Scripts\python tests\test_gui_logic.py
```

## 致謝與授權

- **TesFantom** — 打造 [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829)，也就是本工具賴以運作的偵測引擎，以及背後的逆向工程研究。
- **BadDogSkyrim** — 開發 [PyNifly](https://github.com/BadDogSkyrim/PyNifly)。本工具在 GPL-3.0 授權下 vendor 了 PyNifly 的 `pyn` 套件（NIF 讀寫、MOPP 編譯器與驗證器、NiflyDLL）。
- BSA 讀取功能是針對公開的 BSA v104/v105 格式獨立撰寫的 clean-room 實作。

DeadMesh Fix Tool 是依 **GNU General Public License v3.0** 授權的自由軟體（詳見 `LICENSE`）。原始碼：<https://github.com/johnex2x/deadmesh-fix-tool>
