# DeadMesh Fix Tool

**[DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/)（作者 TesFantom）掃描出的 mesh 問題，自動修復工具。**

> [English README](README.md)

DeadMesh 能找出 Skyrim SE/AE/VR `.nif` 檔裡損壞的 Havok collision——那些會讓遊戲當機、凍結，
或狂吃你 FPS 的問題——但它刻意不做任何修復。**DeadMesh Fix Tool 是非官方的下游修復工具**，
自動完成修復這一步，讓你不用每個被抓到的 mesh 都手動走一遍 Blender + PyNifly + NifSkope
那套流程。

這是非官方的搭配工具，並非 TesFantom 製作或背書。

## 修得了什麼

| DeadMesh 判定結果 | 工具做的事 |
|---|---|
| **CRASH RISK / HANG RISK / BROKEN COLLISION** | 從完好的 collision 幾何重建損壞的 MOPP 搜尋樹。幾何本身完全不動——MOPP 區塊以外的每個 byte 都保持一致。 |
| **HEAVY / VERY HEAVY COLLISION** | 對過密的 collision mesh 做減面（依材質分組、保形），重建 chunk 與 MOPP。強度可選（保守／標準／激進）。 |
| **DEGENERATE COLLISION** | 移除零面積／退化的 collision 三角形，重建 MOPP。 |
| **INVERTED COLLISION** | 翻轉反向 collision 殼的環繞順序（也就是會讓玩家掉落穿模的那種缺陷）。無法明確判斷的情況會拒絕修復，而不是用猜的。 |
| **孤立 collision 區塊** | 在可證明安全的前提下移除未被引用的殘留 collision 區塊；否則會告訴你去 NifSkope 手動處理。 |
| **ORPHAN MOPP** | 無法自動修復（檔案裡的幾何已經被移除），會回報需要手動修。 |

## 安全保證

1. **原始檔案永遠不會被修改。** 修好的 mesh 會以 loose file 形式寫到獨立的輸出資料夾
   （預設 `<mod>\DeadMesh-Fixed`），並保留 `meshes\...` 的相對路徑，方便你直接丟進
   Data 資料夾或 mod 管理器的 mod 裡。BSA 封存檔只會被讀取，絕不會被寫入或重新打包。
2. **DeadMesh 才是裁判，不是我們自己說了算。** 每次修復後，工具都會用 DeadMesh 自己的
   `dmscan` 引擎重新掃描結果。只有在原本的缺陷消失**而且沒有任何其他項目變差**（沒有新增
   的翻面、沒有超出容許範圍的新破洞、沒有新的當機風險類別——包含 dmscan 的
   `--vs` 環繞順序回歸檢查）的情況下，才會寫出檔案。
3. **失敗一律不輸出（fail closed）。** 任何無法被證明安全的修復結果都**不會**被寫出；
   會在報告中列出原因，並附上手動修復的建議路線。

## 系統需求

- Windows 10/11，64 位元
- 已安裝 [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/)
  （工具第一次執行時會請你指定它的資料夾，因為需要用到裡面的 `dmscan.exe`）

## 使用方式（GUI）

1. 執行 `DeadMeshFixTool.exe`。第一次執行時，指定你的 DeadMesh 資料夾位置。
2. 選擇要掃描的 mod 資料夾（loose file 和 BSA 封存檔都支援；loose file 會覆蓋 BSA
   裡的同名檔案，跟遊戲本身的行為一致）。
3. 勾選要修復的類別（預設全開），並選擇減面強度。
4. 按 **Scan**（掃描），檢查列表，再按 **Fix**（修復）。
5. 輸出資料夾裡的每一個檔案都已通過驗證。失敗的項目會連同原因列在結果表格裡，
   以及 `deadmesh-fix-report.txt` / `.json` 報告檔中。

## 使用方式（命令列）

用同一個資料夾裡的 `dmfix.exe`（命令列版本；`DeadMeshFixTool.exe` 是視窗化的 GUI 版本，
不會對終端機輸出任何東西）：

```
dmfix.exe <mod 資料夾> [--deadmesh <資料夾>] [--out <資料夾>]
          [--fix crash,heavy,degenerate,inverted,orphan_blocks]
          [--strength conservative|normal|aggressive] [--no-bsa]
```

退出碼 0 = 所有能修的都修好了；1 = 有檔案修復失敗／無法修復；2 = 使用方式錯誤。

## 工具拒絕修復時的手動後備方案

工具的「拒絕清單」就是你走傳統手動流程的待辦清單：

1. 用 [PyNifly 附加元件](https://github.com/BadDogSkyrim/PyNifly) 把 `.nif` 匯入 Blender。
2. 修正或重建 collision 幾何（collision 只需要大略貼合外形即可——低面數才是對的做法，
   不要拿算圖用的高面數 mesh 當 collision）。
3. 指定正確的 `SKY_HAV_MAT_*` vertex group，用 PyNifly 匯出。
4. 用 NifSkope 比對，再用 DeadMesh 驗證一次。

## 從原始碼建置

```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m PyInstaller build.spec
```

測試（測試用的 `.nif` fixture 檔案**不包含在這個 repo 裡**，因為那些是第三方 mod 的美術
資產，我們沒有重新散布的權利——請自行準備測試用的 mesh 放進 `tests/fixtures/`；
另外執行測試時，`dmscan.exe` 必須存在於同層的 `DeadMesh - MOPP Collision Validator`
資料夾裡）：

```
.venv\Scripts\python tests\test_mopp_rebuild.py
.venv\Scripts\python tests\test_simplify.py
.venv\Scripts\python tests\test_other_fixes.py
.venv\Scripts\python tests\test_bsa.py
.venv\Scripts\python tests\test_gui_logic.py
```

## credits 與授權

- **TesFantom** — [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/)，
  這個工具所依賴的偵測引擎，以及背後的逆向工程研究。
- **BadDogSkyrim** — [PyNifly](https://github.com/BadDogSkyrim/PyNifly)。本工具在
  GPL-3.0 授權下 vendor 了 PyNifly 的 `pyn` 套件（NIF 讀寫、MOPP 編譯器／驗證器、NiflyDLL）。
- BSA 讀取部分是針對公開的 BSA v104/v105 格式所寫的原創 clean-room 實作。

DeadMesh Fix Tool 是依 **GNU General Public License v3.0** 授權的自由軟體（詳見
`LICENSE`）。原始碼：<https://github.com/johnex2x/deadmesh-fix-tool>
