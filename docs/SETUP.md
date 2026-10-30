# 部署步驟

整個流程大約 30～60 分鐘，全部免費。打勾的步驟做完就能用。

- [ ] 1. 建 Discord bot，拿 token
- [ ] 2. 申請中央氣象署 API key
- [ ] 3. 開 Oracle Cloud 免費 VM
- [ ] 4. 在 VM 上一鍵安裝、填設定、啟動
- [ ] 5. 保護設定網頁（Cloudflare Tunnel + Access，或 SSH 轉發）

---

## 1. Discord bot

1. 到 <https://discord.com/developers/applications> → **New Application**，名字取 `jvs`（或你喜歡的）
2. 左邊 **Bot** → **Reset Token** → 複製下來，這就是 `DISCORD_TOKEN`（**不要給任何人看**）
3. 同一頁的 **Privileged Gateway Intents**：打開 **Message Content Intent**（其他兩個不用開）
   - 這是為了讓你「沒 @ bot、只是在聊天裡說『我想畫…』」時也能被記下來
   - 不開也能用，只是只有 @bot 的訊息會被處理
4. 左邊 **OAuth2 → URL Generator**：
   - Scopes 勾 `bot`
   - Bot Permissions 勾 `View Channels`、`Send Messages`、`Read Message History`
   - 想讓它 @ 一般不能被 @ 的身分組，再勾 `Mention Everyone`（可不勾）
   - 複製最下面的網址，在瀏覽器開啟，把 bot 邀進你的伺服器
5. 拿你的使用者 ID（填 `OWNER_ID`）：開發者模式下在自己的頭像上按右鍵 → **複製使用者 ID**。預設只有你的「我想畫…」會被自動記錄
6. 拿頻道 ID：Discord 設定 → 進階 → 打開**開發者模式** → 在頻道名稱上按右鍵 → **複製頻道 ID**，這就是 `CHANNEL_IDS`
7. 提醒一律發到 `CHANNEL_IDS` 的第一個頻道；其他頻道 @bot 也會回，前提是 bot 在那些頻道有「檢視頻道」「傳送訊息」權限（私人頻道要另外加）。不想這樣的話，在設定頁關掉「所有頻道都回應」

## 2. 中央氣象署 API key

1. 到 <https://opendata.cwa.gov.tw/> 註冊會員、登入
2. 右上角會員 → **取得授權碼** → 複製，這就是 `CWA_API_KEY`

## 3. Oracle Cloud 免費 VM

1. 到 <https://www.oracle.com/cloud/free/> 註冊。要驗證信用卡，但 Always Free 的資源不會扣款
   - Home Region 選 **Japan East (Tokyo)** 或 **Japan Central (Osaka)**，離台灣近。**註冊後不能改**
2. 主控台 → **Compute → Instances → Create instance**
   - Image：**Ubuntu 24.04**（或 22.04）
   - Shape：**VM.Standard.E2.1.Micro**（AMD，標示 Always Free-eligible）。也可以選 Ampere A1（ARM，比較強），但常常顯示容量不足
   - **Add SSH keys** → Generate a key pair → **下載 private key**（好好保存，等等連線要用）
   - 其他用預設 → Create
3. 等狀態變成 Running，記下 **Public IP address**
4. 不用開任何對外的 port。bot 和 Cloudflare Tunnel 都是由 VM 主動往外連

連進 VM（Windows 可以用 PowerShell，Mac 用終端機）：
```bash
chmod 600 ~/Downloads/ssh-key-*.key          # Windows 不用這行
ssh -i ~/Downloads/ssh-key-*.key ubuntu@你的PublicIP
```

> 💡 E2.1.Micro 只有 1GB 記憶體，跑這個 bot 綽綽有餘。Oracle 有時會回收長期閒置的免費 VM；bot 一直在跑，通常不會被當成閒置。

## 4. 安裝與啟動

repo 是 private 的，在 VM 上 clone 需要驗證。最簡單的方法是到 GitHub → Settings → Developer settings → **Fine-grained tokens** 建一個只能讀 `jvs` 的 token（Contents: Read-only），clone 時拿它當密碼。

```bash
git clone https://github.com/Russell0123/jvs.git
cd jvs
sudo bash deploy/install.sh
sudo nano /opt/jvs/.env      # 填 DISCORD_TOKEN、CHANNEL_IDS、OWNER_ID、CWA_API_KEY
sudo systemctl enable --now jvs
journalctl -u jvs -f         # 看到「已登入：jvs#xxxx」就成功了，Ctrl+C 離開
```

到頻道打 `@jvs 說明` 試試看。

### 之後更新程式：在設定網頁按一下

設定網頁上方的「**更新**」頁會顯示主機現在跑的版本、GitHub 上最新的版本（有新版會標「有新版本」）和上次更新的結果。按「更新到最新版」，大約 1 分鐘後頁面會自己重新整理出結果。

它在主機上做的事：
1. bot 只寫一個觸發檔 `/opt/jvs/data/update-request`（bot 用 jvs 帳號跑，沒有 sudo 權限）
2. `jvs-update.path` 看到觸發檔，用 root 跑 `jvs-update.service`（`deploy/web-update.sh`）
3. 用 repo 擁有者（ubuntu）的帳號在 `~/jvs` 跑 `git pull --ff-only`
4. 先檢查：套件裝得起來、每個 `.py` 都能編譯、`reminderbot` 能 import。沒過就不安裝，原始碼退回原本的版本
5. 跑 `deploy/install.sh` 安裝並重啟，等 20～40 秒確認服務和設定網頁都有回應
6. 新版沒起來就自動退回上一版再裝一次，記錄「新版有問題，已退回」
7. 全程紀錄在 `/opt/jvs/data/update.log`（更新頁可以展開看），結果在 `/opt/jvs/data/update-status.json`

同一時間只會有一個更新在跑，重複按會提示「正在更新」。

這個按鈕要先在主機上跑過一次新版的 `install.sh` 才會出現（它會安裝 `jvs-update.path`）：
```bash
cd ~/jvs && git pull && sudo bash deploy/install.sh
```
之後就不用再連進主機了。網頁更新也壞掉的時候，還是可以用上面這行手動更新。

資料庫每天凌晨 4 點自動備份到 `/opt/jvs/data/backups/`，保留 14 天；上傳的圖片打包成 `images.tgz`。更新程式時 `/opt/jvs/data/` 整個不會動。

## 4.5 加第二個 bot（蔣寶）

蔣寶和賈維斯跑在同一支程式、同一台主機，共用一個設定網頁（最左邊的圓圈切換），但設定、提醒、清單、規則、圖片都各自分開。它一開始**什麼都不回**，之後要加功能再開。

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**，名字填「蔣寶」→ Create
2. 左邊 **Bot**：
   - 按 **Reset Token** → 複製 token（只會顯示一次）
   - 往下打開 **Message Content Intent** → Save Changes
   - 想換頭像可以在這頁上傳
3. 左邊 **OAuth2** → 複製 **Client ID**，把下面網址的 `CLIENT_ID` 換掉，用瀏覽器打開邀請到伺服器：
   ```
   https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=388160
   ```
4. 在主機上把 token 填進 `.env`，重啟：
   ```bash
   sudo nano /opt/jvs/.env
   # 在最後加一行（等號後面貼 token，前後不要有空白）：
   # JBAO_DISCORD_TOKEN=貼上蔣寶的token
   # 想讓蔣寶只在某些頻道：再加 JBAO_CHANNEL_IDS=頻道ID
   sudo systemctl restart jvs
   journalctl -u jvs -n 30 --no-pager   # 看到「蔣寶 已登入」就成功了
   ```
5. 打開設定網頁，最左邊多一個「蔣」，點進去就是蔣寶自己的設定。「設定 → 功能 → 回應訊息」打開才會開始回話。

token 錯了只會讓蔣寶不跑，賈維斯照常；紀錄裡會寫「蔣寶 的 token 不對」。

## 5. 設定網頁

### 最簡單：公開網址＋帳密（不用網域）
網址會是 `https://你的IP用-連起來.sslip.io`，例如 `https://158-179-177-8.sslip.io`，任何裝置都能開，輸入帳密就能進。

1. Oracle 主控台：☰ → **Networking → Virtual cloud networks** → 點你的 VCN → **Security** 分頁 → **Default Security List**
   → **Security rules** → **Add Ingress Rules**：Source CIDR `0.0.0.0/0`、IP Protocol `TCP`、Destination port range `80,443` → **Add**
2. 主機上：`cd ~/jvs && git pull && sudo bash deploy/install.sh && sudo bash deploy/public-web.sh`
   （會問你要設定的帳號密碼，密碼請用英文和數字）
3. 等 1 分鐘，打開它印出的網址

### 只有自己的裝置能連


設定網頁只聽 VM 自己的 `127.0.0.1:8080`，外面直接連不到。兩種方法擇一：

### 方法 A：Cloudflare Tunnel + Access（推薦，要有一個放在 Cloudflare 的網域）

用手機或任何電腦都能開。只有用你的 email 收驗證碼登入的人才進得去。

1. 你需要一個網域交給 Cloudflare 管（沒有的話可以在 Cloudflare 買，一年大約 10 美元起；不想買就用方法 B）
2. Cloudflare 主控台 → **Zero Trust**（第一次會請你選方案，選 **Free**；有時會要求填付款方式，但 Free 方案不收費）
3. **Networks → Tunnels → Create a tunnel** → Cloudflared → 名字 `jvs`
   → 選 Debian／64-bit（ARM VM 選 arm64）→ 複製它給的安裝指令，貼到 VM 上執行
4. 同一個精靈的 **Public Hostname**：
   - Subdomain：`jvs`，Domain：你的網域 → 網址會是 `https://jvs.你的網域`
   - Service：`HTTP`，URL：`127.0.0.1:8080`
5. **Access → Applications → Add an application → Self-hosted**
   - Application domain：`jvs.你的網域`
   - Policy：Action **Allow**，Include → **Emails** → 填你的 email
   - Authentication 用預設的 **One-time PIN**（寄驗證碼到信箱）
6. 回到 VM，在 `/opt/jvs/.env` 填 `ADMIN_EMAIL=你的email`，然後 `sudo systemctl restart jvs`
   - 這樣就算 Access 設錯，bot 也會再檢查一次 email

打開 `https://jvs.你的網域`，輸入信箱收到的驗證碼，就能進設定頁。

### 方法 B：SSH 轉發（不用網域，最簡單）

只有拿著 SSH 金鑰的電腦能開：
```bash
ssh -i ~/Downloads/ssh-key-*.key -L 8080:127.0.0.1:8080 ubuntu@你的PublicIP
```
連著的時候，在自己的電腦開 <http://localhost:8080>。想多一層保護，可以在 `.env` 填 `WEB_PASSWORD`（帳號 `admin`）。

---

## 常見問題

| 狀況 | 怎麼辦 |
|---|---|
| @bot 沒反應 | `journalctl -u jvs -n 50` 看錯誤；確認 `CHANNEL_IDS` 是這個頻道的 ID；確認 bot 在這個頻道有「檢視頻道」和「傳送訊息」權限 |
| 天氣說查不到 | 確認 `CWA_API_KEY` 有填，而且改完有重啟；縣市名稱要用「臺」（設定頁的選單會處理） |
| 提醒時間錯 | VM 時區不影響，程式固定用 `Asia/Taipei`。如果是句子解讀錯，把那句話告訴我，我加進測試再修 |
| 想換頻道 | 改 `.env` 的 `CHANNEL_IDS`，然後 `sudo systemctl restart jvs` |
| 設定頁打不開 | `curl http://127.0.0.1:8080/healthz` 在 VM 上確認程式有在跑；Tunnel 狀態到 Zero Trust → Tunnels 看 |
