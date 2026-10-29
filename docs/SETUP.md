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
3. 同一頁的 **Privileged Gateway Intents** 三個都**不用開**（bot 只看 @它的訊息，Discord 本來就會給內容）
4. 左邊 **OAuth2 → URL Generator**：
   - Scopes 勾 `bot`
   - Bot Permissions 勾 `View Channels`、`Send Messages`、`Read Message History`
   - 想讓它 @ 一般不能被 @ 的身分組，再勾 `Mention Everyone`（可不勾）
   - 複製最下面的網址，在瀏覽器開啟，把 bot 邀進你的伺服器
5. 拿頻道 ID：Discord 設定 → 進階 → 打開**開發者模式** → 在頻道名稱上按右鍵 → **複製頻道 ID**，這就是 `CHANNEL_IDS`
6. 如果頻道是私人的，記得在頻道權限裡讓 bot 看得到

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
sudo nano /opt/jvs/.env      # 填 DISCORD_TOKEN、CHANNEL_IDS、CWA_API_KEY
sudo systemctl enable --now jvs
journalctl -u jvs -f         # 看到「已登入：jvs#xxxx」就成功了，Ctrl+C 離開
```

到頻道打 `@jvs 說明` 試試看。

之後更新程式：
```bash
cd ~/jvs && sudo bash deploy/update.sh
```

資料庫每天凌晨 4 點自動備份到 `/opt/jvs/data/backups/`，保留 14 天。

## 5. 保護設定網頁

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
